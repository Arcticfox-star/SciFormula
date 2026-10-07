# -*- coding: utf-8 -*-
"""agh_provenance.py —— 把「这次开发用了什么模型」变成可自行复算的一手证据。

【这个文件为什么存在】
外部审计提了一条硬性要求：**必须留有 trace，以便核对运行记录是否使用了 AGH /
Agnes 系列模型进行开发**。核查的结论是分两半的：

  · 「有没有 trace 留存」——**符合**。7 份 AGH 官方会话导出全部入库并受哈希保护。
  · 「能不能核对模型」——**不符合**。因为：

      1. 7 份 AGH 官方 HTML 导出里**不含任何模型字段**（实测 grep "agnes" 零命中）；
      2. 模型名 `agnes-3.0-flash` 实际只存在于 JSONL 事件导出的 `session/start`
         事件的 `modelSettings[*].model` 里，而 **JSONL 原件没有入库**；
      3. 于是文档里的模型名只是**作者的转述**——复核者无从复算，只能选择相信。

「能被引证」和「能被核对」是两回事。转述属于前者，不属于后者。

【它怎么解决】
把 AGH 的 JSONL 事件原件放进 `results/agh-event/`，本工具从原件的
`session/start` 事件里**读出**模型信息，生成一份登账表。于是：

      任何人拿到这份仓库 → 跑 `python agh_provenance.py` → 得到同一张表

模型名就从「作者说」变成了「原件算出来」，满足核对要求。

【为什么登账表禁止人工编辑】
一旦允许手改，它就退化成又一份转述——和现在的处境没有区别。因此本文件
**不加人工标注区**（与 report_notes.py 那套机制刻意不同），表头写明全部由脚本算出。

【用法】
    # 1) 在真实终端里导出事件原件（agh 不在 PATH 时需用完整路径）
    agh sessions list
    agh export <会话id> --format agnes -o results/agh-event/<会话id>.jsonl

    # 2) 生成（或复算）登账表
    python agh_provenance.py                 # 生成 results/agh-model-provenance.md
    python agh_provenance.py --check         # 只核对现有表与原件是否一致
    python agh_provenance.py --selftest      # 自检（用伪造原件跑全流程）

【缺件时的行为（重要）】
仓库里一个 JSONL 都没有时，本工具**明确报告缺件并以非 0 退出**，
绝不自造数据、也绝不打印「全部通过」。
一个「没数据也全绿」的核查工具，比没有工具更糟——它会让人误以为已经合规。
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EVENT_DIR = os.path.join("results", "agh-event")          # JSONL 事件原件目录
OUT_PATH = os.path.join("results", "agh-model-provenance.md")

# AGH 版本有时导出成 "0.0.0"——那是导出侧没填，不是真实的 0.0.0。
# 直接显示 0.0.0 会让复核者去查一个不存在的版本，所以如实标注「未提供」。
_NULL_VERSIONS = {"", "0.0.0", "0.0", "unknown", "none", "null"}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_events(path):
    """读一行一个 JSON 的事件流水。返回 None 表示格式不对。"""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            events = [json.loads(ln) for ln in f.read().splitlines() if ln.strip()]
    except Exception as exc:
        print("  !! 无法解析 %s：%s" % (path, exc))
        return None
    if not events:
        return None
    if not all(isinstance(e, dict) and "type" in e for e in events):
        return None
    return events


def session_meta(events):
    """从 session/start 事件里抓会话元信息。

    注意 modelSettings 是个列表：一次会话可能配了多个模型（主模型 / 降级备用等）。
    旧版 trace_summary.py 只读了 [0]，多模型会话会静默漏掉——这里读全部。
    """
    for ev in events:
        if ev.get("type") != "session/start":
            continue
        d = ev.get("data", {}) or {}
        models = [m.get("model") for m in (d.get("modelSettings") or [])
                  if isinstance(m, dict) and m.get("model")]
        ver = d.get("agnesVersion") or ""
        if ver.strip().lower() in _NULL_VERSIONS:
            ver = ""                       # 空串由上层渲染成「未提供」
        return dict(
            sid=d.get("key") or "",
            version=ver,
            models=models,
            preset=d.get("preset") or "",
            cwd=d.get("cwd") or "",
        )
    return dict(sid="", version="", models=[], preset="", cwd="")


def tally(events):
    """统计工具调用、批准与被拒次数——用来证明运行确实受 AGH 逐次管辖。"""
    counts = {}
    for ev in events:
        counts[ev.get("type")] = counts.get(ev.get("type"), 0) + 1
    rejected = 0
    for ev in events:
        if ev.get("type") == "approval/decided":
            if (ev.get("data") or {}).get("verdict") == "rejected":
                rejected += 1
    return dict(
        total=len(events),
        turns=counts.get("turn/start", 0),
        steps=counts.get("step/start", 0),
        calls=counts.get("tool/call", 0),
        approvals=counts.get("approval/decided", 0),
        rejected=rejected,
    )


def index_mentions():
    """扫描仓库文档，建立「会话 ID → 提到它的文档」倒排索引。

    这一步是为了把 JSONL 原件和已有的 HTML 导出 / trace 摘要关联起来。
    AGH 的 HTML 导出里没写会话 ID（实测如此），所以只能从整理好的 trace 里反查。
    """
    idx = {}
    scan_roots = [os.path.join("results", "agh-trace-%s.md" % n)
                  for n in ("summary", "demo", "feynman", "sim", "data")]
    for rel in scan_roots:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        body = io.open(p, encoding="utf-8", errors="replace").read()
        for sid in set(UUID_RE.findall(body)):
            idx.setdefault(sid.lower(), set()).add(rel)
    return idx


def build_table(rows, missing_html):
    L = []
    L.append("# AGH 运行模型溯源（一手登账）\n")
    L.append("本文件**全部由 `agh_provenance.py` 从 AGH 事件原件（JSONL）读出**，")
    L.append("没有任何一行是人工填写的。任何人重跑 `python agh_provenance.py`")
    L.append("都应得到同一份内容——这是它能作为核对依据的前提。\n")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 事件原件目录 | `%s/` |" % EVENT_DIR.replace("\\", "/"))
    L.append("| 已入库原件 | %d 份 |" % len(rows))
    L.append("| 生成方式 | `python agh_provenance.py`（可自行复算）|")
    L.append("")
    L.append("## 一、逐会话登账\n")
    L.append("| 会话 ID | 模型 | AGH 版本 | 预设 | 事件数 | 轮/步 | 工具调用 | 人工批准（拒）| 原件 sha256 前 16 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        models = ", ".join("`%s`" % m for m in r["meta"]["models"]) or "（原件未提供）"
        L.append("| `%s` | %s | %s | %s | %d | %d/%d | %d | %d（%d）| `%s` |" % (
            r["meta"]["sid"], models,
            ("`%s`" % r["meta"]["version"]) if r["meta"]["version"] else "（未提供）",
            r["meta"]["preset"] or "—",
            r["tally"]["total"], r["tally"]["turns"], r["tally"]["steps"],
            r["tally"]["calls"], r["tally"]["approvals"], r["tally"]["rejected"],
            r["sha"][:16]))
    L.append("")
    L.append("> 「人工批准」= 每次工具调用前 AGH 都弹卡由人裁决。")
    L.append("> 这一列不为零，才能说明运行是在 AGH 里**逐次受管辖**地发生的，")
    L.append("> 而不是本地脚本一次性跑完后再补一份记录。\n")
    if missing_html:
        L.append("## 二、尚未落到官方 HTML 导出的会话\n")
        L.append("以下会话有事件原件，但在 `results/agh-trace-*.md` 里查不到对应 ID，")
        L.append("复核时可用 `agh export <会话id> --format agnes --html` 补一份：\n")
        for sid in missing_html:
            L.append("- `%s`" % sid)
        L.append("")
    L.append("---\n")
    L.append("本文件只是**登账**。真正的原件是 `%s/` 下的 JSONL，" % EVENT_DIR.replace("\\", "/"))
    L.append("它们同样受 `results/EVIDENCE.sha256` 逐字节保护。")
    return "\n".join(L)


def collect():
    """扫描原件目录，返回 (rows, errs, missing)。"""
    d = os.path.join(ROOT, EVENT_DIR)
    rows, errs, missing = [], [], []
    if not os.path.isdir(d):
        return rows, ["原件目录不存在：%s" % EVENT_DIR], missing
    files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))
    if not files:
        return rows, ["原件目录为空（%s），尚无 JSONL 入库" % EVENT_DIR], missing
    idx = index_mentions()
    known = set(idx.keys())
    for fn in files:
        p = os.path.join(d, fn)
        events = load_events(p)
        if events is None:
            errs.append("%s 不是有效的 AGH 事件流水" % fn)
            continue
        meta = session_meta(events)
        if not meta["sid"]:
            errs.append("%s 里没有 session/start 事件，取不到会话 ID" % fn)
            continue
        # 文件名里的 ID 与内容里的 ID 不一致时，以内容为准并提醒
        file_sid = fn[:-6]
        if not UUID_RE.search(file_sid):
            errs.append("%s 文件名不是会话 ID，建议改名为 %s.jsonl" % (fn, meta["sid"]))
        elif file_sid.lower() != meta["sid"].lower():
            errs.append("%s 文件名与内容里的会话 ID（%s）不一致" % (fn, meta["sid"]))
        if not meta["models"]:
            errs.append("%s 的 session/start 里没有 model 字段——无法证明用了什么模型" % fn)
        rows.append(dict(file=fn, path=p, meta=meta, tally=tally(events),
                         sha=sha256_of(p)))
    rows.sort(key=lambda r: r["meta"]["sid"])
    missing = [r["meta"]["sid"] for r in rows if r["meta"]["sid"].lower() not in known]
    return rows, errs, missing


def selftest():
    """用伪造的事件原件跑一遍全流程，证明工具本身没坏。

    自检造的文件放在临时目录、跑完删掉，不污染仓库——
    因为一旦仓库里出现伪造的 JSONL，它就再也不是「原件」了。
    """
    import shutil
    import tempfile

    print("=== agh_provenance 自检 ===")
    tmpdir = tempfile.mkdtemp(prefix="aghprov_")
    saved = globals()["EVENT_DIR"], globals()["OUT_PATH"]
    try:
        # EVENT_DIR 设成绝对路径：os.path.join(ROOT, abs) 会返回 abs 本身，
        # collect() 因此直接扫临时目录，不碰仓库。
        globals()["EVENT_DIR"] = tmpdir
        evdir = tmpdir

        # ① 空目录：必须报缺件，绝不假装通过
        rows, errs, missing = collect()
        assert not rows and errs, "空目录应当报缺件，实际 rows=%d errs=%r" % (len(rows), errs)
        print("  ① 空目录正确报缺件：%s" % errs[0])

        # ② 造一份「完美」原件：有 model、有批注、文件名=会话 ID
        good = "11111111-2222-3333-4444-555555555555"
        events = [
            {"type": "session/start", "ts": "2026-10-07T00:00:00Z",
             "data": {"key": good, "agnesVersion": "0.0.0",
                      "preset": "standard-windows",
                      "modelSettings": [{"model": "agnes-3.0-flash"},
                                        {"model": "agnes-3.0-mini"}]}},
            {"type": "turn/start", "ts": "2026-10-07T00:00:01Z", "data": {}},
            {"type": "step/start", "ts": "2026-10-07T00:00:02Z", "data": {}},
            {"type": "tool/call", "ts": "2026-10-07T00:00:03Z",
             "data": {"name": "mcp_sciformula_1_list_problems", "args": {}}},
            {"type": "approval/decided", "ts": "2026-10-07T00:00:04Z",
             "data": {"verdict": "allowed-once"}},
            {"type": "approval/decided", "ts": "2026-10-07T00:00:05Z",
             "data": {"verdict": "rejected"}},
        ]
        with io.open(os.path.join(evdir, good + ".jsonl"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")

        rows, errs, missing = collect()
        assert len(rows) == 1, "应扫到 1 份原件，实际 %d" % len(rows)
        r = rows[0]
        # 多模型必须全部读出（这正是旧版 trace_summary 会漏的那种）
        assert r["meta"]["models"] == ["agnes-3.0-flash", "agnes-3.0-mini"], \
            "多模型未完整读出：%r" % r["meta"]["models"]
        assert r["meta"]["version"] == "", "0.0.0 应被视作未提供，实际 %r" % r["meta"]["version"]
        assert r["tally"]["calls"] == 1 and r["tally"]["rejected"] == 1, \
            "调用/拒绝统计有误：%r" % r["tally"]
        print("  ② 多模型全部读出：%s" % ", ".join(r["meta"]["models"]))
        print("  ③ version=0.0.0 正确标注为「未提供」")
        print("  ④ 批准 2 次 / 拒绝 1 次 统计正确")

        # ③ 缺 model 字段的原件必须报警（否则「用了什么模型」照样无法核对）
        bad = "99999999-8888-7777-6666-555555555555"
        bad_ev = [{"type": "session/start", "ts": "2026-10-07T00:00:00Z",
                   "data": {"key": bad, "modelSettings": []}}]
        with io.open(os.path.join(evdir, bad + ".jsonl"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in bad_ev) + "\n")
        rows, errs, missing = collect()
        assert any("没有 model 字段" in e for e in errs), \
            "缺 model 的原件漏了告警：errs=%r" % errs
        print("  ⑤ 缺 model 字段的原件正确告警")

        # ④ 生成的表格必须包含模型名
        tbl = build_table(rows, missing)
        assert "agnes-3.0-flash" in tbl, "登账表里没有模型名"
        assert "（未提供）" in tbl, "version=0.0.0 未渲染成「未提供」"
        print("  ⑥ 登账表渲染正常，含模型名与「未提供」标注")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        globals()["EVENT_DIR"], globals()["OUT_PATH"] = saved
    print("=== 自检通过 ===")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="AGH 运行模型溯源（一手登账）")
    ap.add_argument("--check", action="store_true", help="只核对登账表与原件是否一致")
    ap.add_argument("--selftest", action="store_true", help="工具自检")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    rows, errs, missing = collect()

    if not rows:
        # 缺件就明确报错。宁可让 CI 红掉，也不要打印「全部通过」。
        print("!! 尚未取得 AGH 事件原件（JSONL），无法生成模型溯源登账。")
        print("   这意味着「用了什么模型」目前仍**不可核对**——只有作者本人的转述。")
        if errs:
            for e in errs:
                print("   · %s" % e)
        print()
        print("   补证只需在真实终端导出（无需重跑任何实验）：")
        print("       agh sessions list")
        print("       agh export <会话id> --format agnes -o %s/<会话id>.jsonl"
              % EVENT_DIR.replace("\\", "/"))
        print("   之后再跑本命令即可生成一手登账表。")
        return 1

    text = build_table(rows, missing)

    if a.check:
        if not os.path.exists(os.path.join(ROOT, OUT_PATH)):
            print("!! 登账表不存在：%s（先跑一次 python agh_provenance.py）" % OUT_PATH)
            return 1
        old = io.open(os.path.join(ROOT, OUT_PATH), encoding="utf-8", newline="\n").read()
        if old.strip() == text.strip():
            print("登账表与 %d 份事件原件一致（含意外到的例外项见下）" % len(rows))
        else:
            print("!! 登账表已与原件不一致，请重新生成")
            return 1
    else:
        with io.open(os.path.join(ROOT, OUT_PATH), "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        print("已写出 %s（%d 份原件，%d 字节）" % (OUT_PATH, len(rows), len(text)))

    for e in errs:
        print("  ⚠ %s" % e)
    if missing:
        print("  以下会话尚无对应 trace 文档：%s" % ", ".join(missing))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
