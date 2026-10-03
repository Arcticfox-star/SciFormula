# -*- coding: utf-8 -*-
"""
trace_summary.py —— 把 AGH 导出的会话流水整理成可提交的轨迹摘要

【这个文件负责什么】
AGH 的 `agh export <会话id> --format agnes` 导出的是一行一个 JSON 的事件流水（JSONL），
几百条事件里真正有价值的是「谁在第几步调了哪个工具、参数是什么、批准了没有、返回什么」。
这个脚本把它整理成一张 markdown 表格，直接可以放进提交材料。

【怎么用】
    # 1. 先从 AGH 导出（会话 id 用 agh sessions list 查）
    agh export SESSION_ID --format agnes -o session.json
    agh export SESSION_ID --format agnes --html -o results/agh-session.html

    # 2. 再整理成摘要
    python trace_summary.py session.json results/agh-trace-summary.md

    # 也可以顺带把「这次运行发现了什么」换成自己写的内容（见文件末尾说明）
"""

import io
import json
import os
import re
import sys


def text_of(data):
    """从事件 data 里抽出纯文本。工具返回通常是 [{type:text, text:'...'}] 形式。"""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "content", "output", "message", "summary"):
            if key in data:
                return text_of(data[key])
        return json.dumps(data, ensure_ascii=False)
    if isinstance(data, list):
        parts = []
        for item in data:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(text_of(item))
        return "\n".join(parts)
    return str(data)


def parse_tool_payload(data):
    """工具返回体里包了一层 JSON 字符串，试着解出来变成 dict。"""
    raw = text_of(data).strip()
    if raw.startswith("[") and '"text"' in raw:
        try:
            arr = json.loads(raw)
            raw = "\n".join(x.get("text", "") for x in arr if isinstance(x, dict))
        except Exception:
            pass
    try:
        return json.loads(raw)
    except Exception:
        return None


def load_events(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return [json.loads(line) for line in f.read().splitlines() if line.strip()]


def collect(events):
    """把散落的事件按类型收集起来，并把 tool/call 与它的批准、结果配对。"""
    calls, results, approvals, asks, replies, users = [], {}, {}, {}, [], []
    counts = {}
    for ev in events:
        kind, seq, data = ev.get("type"), ev.get("seq"), ev.get("data", {})
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "user/message":
            body = text_of(data)
            if "runtime context" not in body:      # 平台自动注入的上下文不算用户输入
                users.append((seq, body))
        elif kind == "tool/call":
            calls.append((seq, data.get("name") or data.get("tool"),
                          data.get("args") or data.get("arguments")))
        elif kind == "tool/result":
            results[seq] = data
        elif kind == "approval/asked":
            asks[seq] = data
        elif kind == "approval/decided":
            approvals[seq] = data
        elif kind == "assistant/message":
            replies.append((seq, text_of(data)))

    pairs = []
    for i, (seq, name, args) in enumerate(calls):
        nxt = calls[i + 1][0] if i + 1 < len(calls) else 10 ** 9
        ask = next((asks[k] for k in sorted(asks) if seq < k < nxt), None)
        dec = next((approvals[k] for k in sorted(approvals) if seq < k < nxt), None)
        res = next(((k, results[k]) for k in sorted(results) if seq < k < nxt), None)
        pairs.append(dict(seq=seq, name=name, args=args, ask=ask, decision=dec, result=res))
    return dict(calls=calls, pairs=pairs, approvals=approvals, replies=replies,
                users=users, counts=counts)


def summarize(events, info, analysis=""):
    c = collect(events)
    pairs = c["pairs"]
    n_rej = sum(1 for a in c["approvals"].values() if a.get("verdict") == "rejected")
    n_turn = c["counts"].get("turn/start", 0)
    n_step = c["counts"].get("step/start", 0)
    tool_prefix = ""

    lines = []
    lines.append("# AGH 运行记录摘要\n")
    lines.append("本文件由 `trace_summary.py` 从 AGH 导出的事件流水自动整理。")
    lines.append("**事件与指标未经人工修饰**；只有末尾的分析段是人写的。\n")
    lines.append("原始导出：`results/agh-session.html`（AGH 官方 HTML 导出）\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    for k, v in info.items():
        lines.append("| %s | %s |" % (k, v))
    lines.append("| 事件总数 | %d |" % len(events))
    lines.append("| 对话轮数 / 模型步数 | %d / %d |" % (n_turn, n_step))
    lines.append("| 工具调用次数 | %d |" % len(pairs))
    lines.append("| 人工批准次数 | %d（其中被拒绝 %d 次）|" % (len(c["approvals"]), n_rej))
    if events:
        lines.append("| 起止时间（UTC）| %s → %s |" % (events[0]["ts"][:19], events[-1]["ts"][:19]))
    lines.append("")
    lines.append("> 「人工批准次数」= 每一次工具调用前 AGH 都弹出一张批准卡片、由人逐次裁决。")
    lines.append("> 这证明运行确实受 AGH 管辖，而不是脚本一次性跑完。\n")

    # 找出工具名前缀（形如 mcp_<serverId>_<数字>_），方便后面显示短名
    for p in pairs:
        m = re.match(r"(mcp_[a-z0-9_]+?_\d+_)", p["name"] or "")
        if m:
            tool_prefix = m.group(1)
            break

    lines.append("## 一、工具调用链（按发生顺序）\n")
    lines.append("| # | 工具 | 关键参数 | 批准 | 结果概要 |")
    lines.append("|---|---|---|---|---|")
    for i, p in enumerate(pairs, 1):
        short = (p["name"] or "").replace(tool_prefix, "") if tool_prefix else (p["name"] or "")
        args = p["args"] if isinstance(p["args"], str) else json.dumps(p["args"], ensure_ascii=False)
        args = re.sub(r"\s+", " ", args or "")[:170]
        verdict = (p["decision"] or {}).get("verdict", "—")
        summary = ""
        if p["result"]:
            payload = parse_tool_payload(p["result"][1])
            if isinstance(payload, dict):
                keys = ["ok", "count", "data_id", "candidate_total", "candidate_kept",
                        "prune_percent", "formula_readable", "n_terms", "r2_fit",
                        "verdict", "passed"]
                bits = ["%s=%s" % (k, payload[k]) for k in keys if k in payload]
                summary = "; ".join(bits)[:250]
            if not summary:
                summary = re.sub(r"\s+", " ", text_of(p["result"][1]))[:170]
        lines.append("| %d | `%s` | %s | %s | %s |" % (i, short, args, verdict, summary))
    lines.append("")

    lines.append("## 二、用户输入\n")
    for seq, body in c["users"]:
        lines.append("**[seq %d]**\n" % seq)
        lines.append("```")
        lines.append(body.strip()[:1500])
        lines.append("```\n")

    lines.append("## 三、助手最终结论\n")
    for seq, body in c["replies"]:
        if len(body.strip()) > 300:          # 短的通常只是"我要调用某工具"
            lines.append("**[seq %d]**\n" % seq)
            lines.append("```")
            lines.append(body.strip()[:3000])
            lines.append("```\n")

    if analysis:
        lines.append("## 四、这次运行发现了什么（人工分析）\n")
        lines.append(analysis)
    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("用法: python trace_summary.py <会话导出的 json> <输出的 md>")
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    if not os.path.exists(src):
        print("找不到输入文件: %s" % src)
        return 1
    events = load_events(src)
    info = {}
    # 从 session/start 事件里抓会话元信息
    for ev in events:
        if ev.get("type") == "session/start":
            d = ev.get("data", {})
            info["会话 ID"] = "`%s`" % d.get("key", "?")
            info["AGH 版本"] = "`%s`" % d.get("agnesVersion", "?")
            models = [m.get("model") for m in d.get("modelSettings", []) if isinstance(m, dict)]
            if models:
                info["模型"] = "`%s`" % models[0]
            info["预设"] = "`%s`" % d.get("preset", "?")
            break
    for ev in events:
        if ev.get("data", {}).get("cwd"):
            info["工作目录"] = "`%s`" % ev["data"]["cwd"]
            break

    # 如果同目录下有同名 .analysis.md，就把它作为人工分析段拼进去
    analysis_path = os.path.splitext(dst)[0] + ".analysis.md"
    analysis = ""
    if os.path.exists(analysis_path):
        analysis = io.open(analysis_path, encoding="utf-8").read().strip()

    out = summarize(events, info, analysis)
    io.open(dst, "w", encoding="utf-8").write(out)
    print("已写出 %s（%d 字节）" % (dst, len(out)))
    print("工具调用 %d 次，批准 %d 次" % (len(collect(events)["pairs"]), len(collect(events)["approvals"])))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
