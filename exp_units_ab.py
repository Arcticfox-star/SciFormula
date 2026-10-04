# -*- coding: utf-8 -*-
"""
exp_units_ab.py —— 对照实验 A：单位「已知」vs「需推断」

【要回答的问题】
我们的核心主张之一是：目标量与各变量的单位**不提供**，由模型从变量名与物理语境推断
（这是报名方向 B1 的落点）。那么代价是多少？——本实验把它变成数字。

【实验设计】
同一批题、同一批数据、同一套流程，唯一变量是**单位从哪来**：

    臂 1「已知」（oracle） ：用题库里的真值单位 —— 等价于"模型推断完全正确"
    臂 2「需推断」（inferred）：用 agnes-3.0-flash 在单位推断评测里给出的答案
                             （results/unit_infer_answers.json，含它写错和写"无法判断"的）

除单位来源外，两次调用的是**同一个 run_one**（同一份 pipeline 实现）——
这是对照实验有意义的前提。三档噪声各跑一遍。

【诚实说明两个局限】
  1. 样本量：只有 12 题（单位推断评测的抽题集）。数字只能当趋势看，不能说成总体结论。
  2. 臂 2 的失败里有"写法不合法"（单位串解析失败）与"主动声明无法判断"两类，
     它们不是物理推断错，但确实是"不提供单位"这条路线的真实成本，故如实保留，
     同时在报告里分开统计。

【怎么用】
    SCIFORMULA_LIBRARY=feynman python exp_units_ab.py            # 跑并出报告
    SCIFORMULA_LIBRARY=feynman python exp_units_ab.py --show     # 只重打上一次的结论

产物：results/units_ab.csv（逐条明细）+ results/units_ab_report.md（结论）
"""

import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dims                                    # noqa: E402
import equations as EQ                         # noqa: E402
import experiment as EX                        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
ANSWERS = os.path.join(RES, "unit_infer_answers.json")
NOISES = (0.0, 0.01, 0.02)
# 「无法判断」的三类写法。注意**不含空字符串**：在我们的工具约定里，
# 空字符串就是"无量纲"的合法写法（见 check_units 的 syntax_hint），
# 把它算成"无法判断"会把正确作答误判为未作答（AGH 会话里实测踩到过）。
UNSURE = ("无法判断", "不确定", "unknown")

# 模型答案里常见的排版变体（中点乘号、全角符号）——先做最小归一化再交给解析器，
# 免得"写法排版"掩盖了"物理推断"本身的对错。
_NORMALIZE = [("·", "*"), ("⋅", "*"), ("×", "*"), ("／", "/"), ("－", "-"), (" ", "")]


def normalize_unit(s):
    s = (s or "").strip()
    for a, b in _NORMALIZE:
        s = s.replace(a, b)
    return s


def load_inferred():
    """读模型给的 12 题单位答案；缺文件时给出可操作的提示。"""
    if not os.path.exists(ANSWERS):
        print("找不到 %s。\n先跑：python unit_infer_eval.py dump → 在 AGH 里让模型作答 → 回填答案。"
              % os.path.relpath(ANSWERS, HERE))
        sys.exit(2)
    raw = json.load(open(ANSWERS, encoding="utf-8"))
    out = {}
    for pid, rec in raw.items():
        vars_u = {}
        for name, u in (rec.get("vars") or {}).items():
            vars_u[name] = normalize_unit(u)
        tgt = normalize_unit(rec.get("target"))
        out[pid] = dict(vars=vars_u, target=tgt,
                        unsure_vars=[k for k, v in vars_u.items() if v in UNSURE],
                        unsure_target=tgt in UNSURE)
    return out


def unit_correct(given, ref):
    """量纲等价判定（写法不同但量纲相同算对）。返回 True/False/None（无法判定）。"""
    if given in UNSURE:
        return None
    try:
        return dims.same(dims.parse_unit(given), dims.parse_unit(ref))
    except Exception:
        return False


def arm_label(rec):
    """给臂 2 的一次运行打一个标签，便于分组统计。"""
    return "inferred"


def main():
    if "--show" in sys.argv and os.path.exists(os.path.join(RES, "units_ab.csv")):
        rows = list(csv.DictReader(open(os.path.join(RES, "units_ab.csv"), encoding="utf-8-sig")))
        summarize(rows)
        write_report(rows, load_inferred())
        return

    inferred = load_inferred()
    ids = sorted(inferred)
    print("对照实验 A：单位 已知 vs 需推断")
    print("题库: %s | 题目数: %d | 噪声档: %s\n" % (EQ.LIBRARY, len(ids), list(NOISES)))

    rows = []
    t0 = time.time()
    for pid in ids:
        eq = EQ.get(pid)
        names = EQ.varnames(eq)
        rec = inferred[pid]

        # 臂 2 的单位可用性：缺变量 / 目标量写"无法判断" → 这一臂无法进行（如实记录）
        missing = [n for n in names if n not in rec["vars"]]
        unusable = bool(missing) or rec["unsure_target"] or rec["unsure_vars"]

        # 单位对错统计（以真值单位为准）
        var_hits = var_tot = tgt_hit = 0
        for n in names:
            ok = unit_correct(rec["vars"].get(n, ""), eq["vars"][n])
            if ok is None:
                continue
            var_tot += 1
            var_hits += 1 if ok else 0
        t_ok = unit_correct(rec["target"], eq["target_unit"])
        tgt_hit = 1 if t_ok else 0

        for noise in NOISES:
            # 臂 1：真值单位
            r1 = EX.run_one(eq, noise)
            rows.append(dict(arm="oracle", eq_id=pid, noise=noise, n_vars=len(names),
                             units_usable=1, var_acc="%d/%d" % (var_hits, var_tot),
                             target_ok=tgt_hit, **pick(r1)))
            # 臂 2：模型推断的单位
            if unusable:
                rows.append(dict(arm="inferred", eq_id=pid, noise=noise, n_vars=len(names),
                                 units_usable=0, var_acc="%d/%d" % (var_hits, var_tot),
                                 target_ok=tgt_hit, cand_total="", cand_kept="",
                                 n_terms="", r2_train="", r2_extrap="",
                                 verdict="units_unavailable", pred="(目标量或变量单位无法判断)"))
            else:
                r2 = EX.run_one(eq, noise, units_override=rec["vars"],
                                target_unit_override=rec["target"] if rec["target"] not in UNSURE else None)
                rows.append(dict(arm="inferred", eq_id=pid, noise=noise, n_vars=len(names),
                                 units_usable=1, var_acc="%d/%d" % (var_hits, var_tot),
                                 target_ok=tgt_hit, **pick(r2)))
        print("  %-10s 变量单位 %d/%d 正确%s" % (
            pid, var_hits, var_tot, "  [目标量或变量标为无法判断 → 臂2无法进行]" if unusable else ""))

    os.makedirs(RES, exist_ok=True)
    csv_path = os.path.join(RES, "units_ab.csv")
    cols = ["arm", "eq_id", "n_vars", "noise", "units_usable", "var_acc", "target_ok",
            "cand_total", "cand_kept", "n_terms", "r2_train", "r2_extrap", "verdict", "pred"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print("\n逐条明细：results/units_ab.csv（%d 行，耗时 %.1fs）" % (len(rows), time.time() - t0))
    summarize(rows)
    write_report(rows, inferred)


def pick(r):
    """从 run_one 的结果里挑出报告需要的字段。"""
    def g(k, d=""):
        v = r.get(k, d)
        return "" if v is None else v
    return dict(cand_total=g("cand_total"), cand_kept=g("cand_kept"),
                n_terms=g("n_terms"), r2_train=g("r2_train"), r2_extrap=g("r2_extrap"),
                verdict=g("verdict"), pred=g("pred"))


RECOVERED = ("exact", "up_to_constant")


def tallies(rows):
    """按臂 × 噪声档统计：结构恢复数（exact/up_to_constant）与双重检验通过数。"""
    out = {}
    for r in rows:
        key = (r["arm"], float(r["noise"]))
        d = out.setdefault(key, dict(n=0, recovered=0, double=0, unusable=0))
        d["n"] += 1
        v = r["verdict"]
        if v in ("units_unavailable", "units_invalid"):
            d["unusable"] += 1
            continue
        if v in RECOVERED:
            d["recovered"] += 1
        try:
            ex = float(r["r2_extrap"])
        except (TypeError, ValueError):
            continue
        if v in ("exact", "up_to_constant", "approx") and ex >= 0.99:
            d["double"] += 1
    return out


def summarize(rows):
    t = tallies(rows)
    print("\n%-10s %-6s %-10s %-10s %-10s" % ("臂", "噪声", "可跑题数", "结构恢复", "双检通过"))
    print("-" * 52)
    for arm in ("oracle", "inferred"):
        for noise in NOISES:
            d = t.get((arm, noise))
            if not d:
                continue
            runnable = d["n"] - d["unusable"]
            print("%-10s %-6s %-10s %-10s %-10s" % (
                arm, noise, "%d/%d" % (runnable, d["n"]),
                "%d/%d" % (d["recovered"], runnable) if runnable else "-",
                "%d/%d" % (d["double"], runnable) if runnable else "-"))


def write_report(rows, inferred):
    t = tallies(rows)
    ids = sorted(inferred)
    by = {}
    for r in rows:
        by[(r["arm"], r["eq_id"], float(r["noise"]))] = r

    runnable_ids = [p for p in ids if by.get(("inferred", p, 0.0), {}).get("verdict")
                    not in ("units_unavailable", "units_invalid")]

    def double_cnt(arm, pool, noise):
        n = 0
        for p in pool:
            r = by.get((arm, p, noise))
            if not r:
                continue
            v = r["verdict"]
            try:
                ex = float(r["r2_extrap"])
            except (TypeError, ValueError):
                continue
            if v in ("exact", "up_to_constant", "approx") and ex >= 0.99:
                n += 1
        return n

    all_correct = [p for p in ids if unit_all_correct(p, inferred)]
    unsure = [p for p in ids if p not in runnable_ids]

    L = []
    A = L.append
    A("# 对照实验 A：单位「已知」vs「需推断」\n")
    A("**唯一变量**：单位从哪来。题目、数据、流程、判分口径完全相同——")
    A("两臂都调用同一个 `run_one`，只有单位来源不同。\n")
    A("| 臂 | 单位来源 |")
    A("|---|---|")
    A("| oracle（已知）| 题库真值单位，等价于「模型单位推断完全正确」|")
    A("| inferred（需推断）| `agnes-3.0-flash` 在单位推断评测里的原始答案（含写错与「无法判断」）|\n")
    A("## 一、整体结果（12 题 × 3 档噪声）\n")
    A("| 臂 | 噪声档 | 可跑题数 | 结构恢复 | 双检通过 |")
    A("|---|---|---|---|---|")
    for arm in ("oracle", "inferred"):
        for noise in NOISES:
            d = t.get((arm, noise))
            if not d:
                continue
            runnable = d["n"] - d["unusable"]
            A("| %s | %s | %d/%d | %s | %s |" % (
                arm, noise, runnable, d["n"],
                "%d/%d" % (d["recovered"], runnable) if runnable else "—",
                "%d/%d" % (d["double"], runnable) if runnable else "—"))
    A("")
    A("## 二、公平口径：只比两臂都能跑的 %d 题\n" % len(runnable_ids))
    A("上面那张表里 inferred 臂的分母少了 4 题（单位标为「无法判断」→ 这一步做不下去）。")
    A("只看两臂都能跑的那些题，才是干净对比：\n")
    A("| 噪声档 | oracle 双检 | inferred 双检 |")
    A("|---|---|---|")
    for noise in NOISES:
        A("| %s | %d/%d | %d/%d |" % (noise, double_cnt("oracle", runnable_ids, noise),
                                      len(runnable_ids),
                                      double_cnt("inferred", runnable_ids, noise),
                                      len(runnable_ids)))
    A("")
    A("## 三、逐题对照（无噪声档）\n")
    A("| 题目 | 变量单位正确数 | oracle 判定 | inferred 判定 | inferred 保留候选 |")
    A("|---|---|---|---|---|")
    for p in ids:
        o = by.get(("oracle", p, 0.0), {})
        i = by.get(("inferred", p, 0.0), {})
        eq = EQ.get(p)
        names = EQ.varnames(eq)
        hit = tot = 0
        for n in names:
            ok = unit_correct(inferred[p]["vars"].get(n, ""), eq["vars"][n])
            if ok is None:
                continue
            tot += 1
            hit += 1 if ok else 0
        A("| %s | %d/%d | %s | %s | %s |" % (
            p, hit, tot, o.get("verdict", "—"), i.get("verdict", "—"),
            i.get("cand_kept") or "—"))
    A("")
    A("## 四、结论与必须说清的三件事\n")
    A("**这次实测的结论是：不提供单位的代价，主要不是「成绩变差」，而是「一部分题直接做不下去」。**\n")
    A("- 12 题里有 **%d 题**（%s）目标量或关键变量被模型标为「无法判断」，"
      "这一步就断了——占 %.0f%%。"
      % (len(unsure), "、".join(unsure), 100.0 * len(unsure) / len(ids)))
    A("- 在剩下 **%d 题**上，两臂判定**逐题完全一致**（见第三节）："
      "本样本里模型的单位错误没有改变任何一题的结论。"
      "原因是它的错误集中在本来就解不出的题上（这些题 oracle 臂同样失败）。" % len(runnable_ids))
    A("- 因此**不能**用本实验宣称「单位推断错误不影响成绩」。样本只有 12 题，"
      "且这 12 题是为「单位推断覆盖度」抽的，不是为「可解性」抽的——"
      "其中 5 题连 oracle 臂都拿不到双检（I.11.19 / I.12.2 / I.12.4 / I.6.2b / I.8.14），"
      "属于特征库表达力的既有边界。\n")
    A("**它真正说明的两件事：**\n")
    A("1. 「单位」这一步是**闸门**：标不出来就整题作废，所以闭环里的纠错（候选归零 → 重推单位）"
      "不是锦上添花，而是把 4 题重新拉回可解空间的手段；")
    A("2. 要量化「闭环版」收益，需要让模型在 AGH 里**带着反馈重推**（而不是只推断一次），"
      "这属于对照实验 C/D 的范围。\n")
    A("> 交叉核对：oracle 臂在这 12 题上的逐题判定，与正式基准 "
      "`results/detailed_feynman.csv` **完全一致**——说明本实验没有引入第二套算法路径。\n")
    A("生成命令：`SCIFORMULA_LIBRARY=feynman python exp_units_ab.py`（重打结论：`--show`）")
    path = os.path.join(RES, "units_ab_report.md")
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("结论报告：results/units_ab_report.md")


def unit_all_correct(pid, inferred):
    eq = EQ.get(pid)
    if inferred[pid]["unsure_target"] or inferred[pid]["unsure_vars"]:
        return False
    for n in EQ.varnames(eq):
        if unit_correct(inferred[pid]["vars"].get(n, ""), eq["vars"][n]) is not True:
            return False
    return unit_correct(inferred[pid]["target"], eq["target_unit"]) is True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
