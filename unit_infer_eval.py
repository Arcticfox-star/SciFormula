# -*- coding: utf-8 -*-
"""
unit_infer_eval.py —— 「单位推断」小规模评测（B1 方向的核心证据）

背景
----
本项目的主张之一：目标量与各变量的物理单位**不给**，由智能体根据问题语境
（变量名、采样区间的量级、Feynman 章节号）自行推断，再交给确定性的量纲校验器把关。
之前只有 P03 / P20 两道题的个案，本脚本把它变成一个可量化的小评测：

    12 道官方 Feynman 题（覆盖 1~6 个变量、力学/电磁/相对论/几何）
    → 智能体在 AGH 里推断单位（不许用会泄漏单位的工具）
    → 本脚本对照 feynman_units.py 的标准答案打分

用法（三步）
----
1) 出题：
       python unit_infer_eval.py dump
   打印题目卡（只有变量名、采样区间、章节号——没有单位），
   并在 results/unit_infer_answers.json 生成答案模板（若尚不存在）。

2) 作答：把题目卡粘贴进 AGH 网页工作台，让模型逐题推断
   「每个变量 + 目标量」的单位，把答案填进 answers JSON。
   允许写 "无法判断"（按错误计，但单独统计）。

3) 打分：
       python unit_infer_eval.py score
   逐变量按**量纲等价**比较（kg*m/s^2 与 N 算对，不是字符串比对），
   输出变量级 / 题目级准确率，并写 results/unit_infer_report.md。

设计说明
----
- 标准答案来自 feynman_units.py（我们自建的量纲表，原数据集不含单位）；
  打分用 dims.parse_unit + dims.same，即「量纲等价」而非「写法一致」。
- 题目固定写死在 PROBLEMS 里（不走桶抽样），保证任何时间复跑同一份卷子。
"""

import json
import os
import sys

import equations as EQ
from dims import parse_unit, same, fmt

PICKED = [
    "I.6.2a",    # 1 变量：高斯函数（theta 无量纲）
    "I.6.2",     # 2 变量：高斯函数（sigma/theta 无量纲）
    "I.12.1",    # 2 变量：库仑力（mu 无量纲、Nn 是计数）
    "I.6.2b",    # 3 变量：高斯函数
    "I.10.7",    # 3 变量：相对论质量
    "I.12.4",    # 3 变量：电场（epsilon 是介电常数）
    "I.8.14",    # 4 变量：两点距离
    "I.12.2",    # 4 变量：库仑力
    "I.13.4",    # 4 变量：动能合成
    "I.12.11",   # 5 变量：洛伦兹力（含 Ef/B/q）
    "I.13.12",   # 5 变量：引力势能（G 是引力常数）
    "I.11.19",   # 6 变量：面积
]

HERE = os.path.dirname(os.path.abspath(__file__))
ANSWERS_PATH = os.path.join(HERE, "results", "unit_infer_answers.json")
REPORT_PATH = os.path.join(HERE, "results", "unit_infer_report.md")


def _eqs():
    return {e["id"]: e for e in EQ.ensure_library("feynman")}


def dump():
    """打印题目卡 + 生成答案模板"""
    eqs = _eqs()
    lines = []
    tmpl = {}
    lines.append("=" * 78)
    lines.append("单位推断评测 · 题目卡（共 %d 题，不含任何单位信息）" % len(PICKED))
    lines.append("=" * 78)
    for pid in PICKED:
        e = eqs[pid]
        rng = "  ".join("%s∈[%g, %g]" % (n, r[0], r[1])
                        for n, r in e["ranges"].items())
        lines.append("")
        lines.append("【%s】（%s）" % (pid, e["ref"]))
        lines.append("  目标量：%s    变量：%s" % (e["target"], ", ".join(e["vars"])))
        lines.append("  采样区间：%s" % rng)
        tmpl[pid] = {"vars": {n: "" for n in e["vars"]}, "target": ""}
    lines.append("")
    lines.append("作答要求：对每题给出 每个变量 与 目标量 的单位"
                 "（如 kg、m/s、V/m、T、无量纲写 1）。不确定可写「无法判断」。")
    print("\n".join(lines))

    if not os.path.exists(ANSWERS_PATH):
        os.makedirs(os.path.dirname(ANSWERS_PATH), exist_ok=True)
        with open(ANSWERS_PATH, "w", encoding="utf-8") as f:
            json.dump(tmpl, f, ensure_ascii=False, indent=2)
        print("\n答案模板已生成：%s" % ANSWERS_PATH)
        print("把模型的推断结果填进对应字段后，运行：python unit_infer_eval.py score")
    else:
        print("\n答案文件已存在（不覆盖）：%s" % ANSWERS_PATH)


def _norm(unit_str):
    """把答案归一化成量纲元组；非法写法/无法判断 → None"""
    if unit_str is None:
        return None, "未作答"
    s = str(unit_str).strip()
    if not s or s in ("无法判断", "unknown", "?", "-"):
        return None, "无法判断"
    try:
        return parse_unit(s), None
    except Exception:
        return None, "写法不合法"


def score():
    eqs = _eqs()
    if not os.path.exists(ANSWERS_PATH):
        sys.exit("找不到答案文件 %s，先运行 dump 生成模板。" % ANSWERS_PATH)
    with open(ANSWERS_PATH, encoding="utf-8") as f:
        answers = json.load(f)

    rows = []
    var_ok = var_tot = 0
    unattempted = bad_syntax = 0
    for pid in PICKED:
        e = eqs[pid]
        ans = answers.get(pid) or {}
        got_vars = ans.get("vars") or {}
        got_tgt = ans.get("target")
        items = [(n, got_vars.get(n), e["vars"][n]) for n in e["vars"]]
        items.append(("(目标) " + e["target"], got_tgt, e["target_unit"]))
        prob_ok = True
        cell = []
        for name, got, truth in items:
            var_tot += 1
            gd, gwhy = _norm(got)
            td = parse_unit(truth)
            if gd is None:
                prob_ok = False
                if gwhy == "无法判断":
                    unattempted += 1
                elif gwhy == "写法不合法":
                    bad_syntax += 1
                cell.append("%s: %s（%s）" % (name, got, gwhy))
            elif same(gd, td):
                var_ok += 1
                cell.append("%s: %s  [对，等价 %s]" % (name, got, fmt(td) or "1"))
            else:
                prob_ok = False
                cell.append("%s: %s  [错，应为 %s]" % (name, got, fmt(td) or "1"))
        rows.append((pid, prob_ok, cell))

    n_ok = sum(1 for _, ok, _ in rows if ok)
    rep = []
    rep.append("# 单位推断评测报告")
    rep.append("")
    rep.append("- 题目：官方 Feynman 基准抽样 %d 题（1~6 变量）" % len(PICKED))
    rep.append("- 判分口径：量纲等价（parse_unit + same），非字符串比对")
    rep.append("- 题目级准确率：**%d / %d（%.0f%%）**"
               % (n_ok, len(PICKED), 100 * n_ok / len(PICKED)))
    rep.append("- 变量级准确率：**%d / %d（%.0f%%）**"
               % (var_ok, var_tot, 100 * var_ok / var_tot))
    rep.append("- 未作答 %d 项 · 写法不合法 %d 项" % (unattempted, bad_syntax))
    rep.append("")
    rep.append("## 逐题明细")
    for pid, ok, cell in rows:
        rep.append("")
        rep.append("- **%s** %s" % (pid, "✔ 全对" if ok else "✘ 有错"))
        for c in cell:
            rep.append("  - %s" % c)
    text = "\n".join(rep)
    print(text)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("\n报告已写出：%s" % REPORT_PATH)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "dump":
        dump()
    elif cmd == "score":
        score()
    else:
        print(__doc__)
        print("用法：python unit_infer_eval.py dump | score")
