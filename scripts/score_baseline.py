# -*- coding: utf-8 -*-
"""
score_baseline.py —— 给基线结果打分，并与本项目的成绩并列（在主环境里跑）

【它读什么】
  · results/baseline_pred/*.npz   基线的预测值（不含真值）
  · results/baseline_input/*_truth.npz   真值（只有这个脚本读）
  · results/verify_ab.csv         本项目在同批数据上的两个口径误差（n_in / n_ex）

【口径（与项目验收口径一致，谁都不特殊）】
  区间内归一化 RMSE  = rmse(预测, 真值) / std(真值)，在训练区间内的**新采样点**上算
  外推归一化 RMSE   = 同上，在向外扩张 30% 的点上算
  双口径通过        = 区间内 ≤ 0.05 且 外推 ≤ 0.20（本项目 verify_formula 的默认阈值）

【怎么用】
    SCIFORMULA_LIBRARY=feynman python scripts/score_baseline.py
产物：results/baseline_scores.csv + results/baseline_report.md
"""

import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np                                    # noqa: E402

IN = os.path.join(ROOT, "results", "baseline_input")
PRED = os.path.join(ROOT, "results", "baseline_pred")
RES = os.path.join(ROOT, "results")
TOL_IN, TOL_EX = 0.05, 0.20

METHOD_NAMES = {
    "rf": "随机森林（scikit-learn）",
    "gbrt": "梯度提升（scikit-learn）",
    "gp": "gplearn 遗传规划",
    "ours": "本项目（量纲剪枝 + OMP）",
}


def nrmse(pred, true):
    s = float(np.std(true))
    if not np.isfinite(s) or s == 0:
        return float("nan")
    return float(np.sqrt(np.mean((np.asarray(pred) - np.asarray(true)) ** 2)) / s)


def load_ours():
    """本项目在同批题目上的两个误差（来自 verify_ab.csv，口径相同）。"""
    p = os.path.join(RES, "verify_ab.csv")
    if not os.path.exists(p):
        return {}
    out = {}
    for r in csv.DictReader(open(p, encoding="utf-8-sig")):
        if str(r.get("has_formula")).strip() != "1":
            continue
        try:
            key = (r["eq_id"], float(r["noise"]))
            out[key] = dict(n_in=float(r["n_in"]), n_ex=float(r["n_ex"]),
                            verdict=r["truth_verdict"], pred=r["pred"])
        except (TypeError, ValueError):
            continue
    return out


def main():
    ours = load_ours()
    rows = []
    for f in sorted(glob.glob(os.path.join(PRED, "*.npz"))):
        base = os.path.basename(f)[:-4]
        method, tag = base.split("__", 1)
        truth_p = os.path.join(IN, tag + "_truth.npz")
        if not os.path.exists(truth_p):
            continue
        t = np.load(truth_p)
        p = np.load(f)
        n_in = nrmse(p["pred_in"], t["yi"])
        n_ex = nrmse(p["pred_ex"], t["ye"])
        rows.append(dict(method=method, tag=tag, n_in=n_in, n_ex=n_ex,
                         source="baseline"))
        # 同题把本项目的结果并进来（一次就够，避免重复）
        if method == "rf":
            pid, nz = tag.split("__nz")
            key = (pid, float(nz))
            if key in ours:
                rows.append(dict(method="ours", tag=tag, n_in=ours[key]["n_in"],
                                 n_ex=ours[key]["n_ex"], source="pipeline"))

    out_csv = os.path.join(RES, "baseline_scores.csv")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["method", "tag", "n_in", "n_ex", "source"])
        w.writeheader()
        w.writerows(rows)

    report(rows)
    print("逐条明细：results/baseline_scores.csv（%d 行）" % len(rows))


def report(rows):
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        g[r["method"]].append(r)

    timing, r2tr = {}, {}
    tp = os.path.join(RES, "baseline_timing.csv")
    if os.path.exists(tp):
        agg, agg_r2 = defaultdict(list), defaultdict(list)
        for r in csv.DictReader(open(tp, encoding="utf-8-sig")):
            if str(r.get("ok")) == "1":
                agg[r["method"]].append(float(r["seconds"]))
                try:
                    agg_r2[r["method"]].append(float(r["r2_train"]))
                except (TypeError, ValueError):
                    pass
        for k, v in agg.items():
            timing[k] = (float(np.median(v)), len(v))
        for k, v in agg_r2.items():
            r2tr[k] = float(np.median(v))

    # 本项目自己的训练 R² 与耗时（从正式基准明细里取中位数，口径同源）
    dp = os.path.join(RES, "detailed_feynman.csv")
    if os.path.exists(dp):
        r2s, secs = [], []
        for r in csv.DictReader(open(dp, encoding="utf-8-sig")):
            try:
                v = float(r["r2_train"])
                if np.isfinite(v):          # 无解那些行是 nan，不能混进中位数
                    r2s.append(v)
                secs.append(float(r["seconds"]))
            except (TypeError, ValueError, KeyError):
                continue
        if r2s:
            r2tr["ours"] = float(np.median(r2s))
        if secs:
            timing["ours"] = (float(np.median(secs)), len(secs))

    # 配对子集：只取本项目也给出了公式的那些组，控制题目难度差异
    ours_tags = {r["tag"] for r in rows if r["method"] == "ours"}

    L = []
    A = L.append
    A("# 对照实验：黑箱拟合基线 vs 本项目\n")
    A("同一批数据（官方 Feynman 基准 %d 组：100 题 × 3 档噪声）、同一套判分口径。"
      % len({r["tag"] for r in rows}))
    A("数据按「基线只见训练与预测点、真值只给评分脚本」的方式分离——")
    A("基线脚本无论如何都读不到真值（与 `posthoc_eval.py` 不进工具集是同一条纪律）。\n")
    A("| 方法 | 组数 | 训练 R²（中位）| 区间内 nRMSE（中位）| 外推 nRMSE（中位）| **双口径通过率** | 单组耗时（中位）| 输出公式 | 同题配对通过率 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for m, rs in sorted(g.items(), key=lambda kv: -sum(
            1 for r in kv[1] if r["n_in"] <= TOL_IN and r["n_ex"] <= TOL_EX)):
        ok = [r for r in rs if np.isfinite(r["n_in"]) and np.isfinite(r["n_ex"])]
        passed = sum(1 for r in ok if r["n_in"] <= TOL_IN and r["n_ex"] <= TOL_EX)
        med_in = float(np.median([r["n_in"] for r in ok])) if ok else float("nan")
        med_ex = float(np.median([r["n_ex"] for r in ok])) if ok else float("nan")
        tm = timing.get(m)
        paired = [r for r in ok if r["tag"] in ours_tags]
        paired_pass = sum(1 for r in paired if r["n_in"] <= TOL_IN and r["n_ex"] <= TOL_EX)
        denom = len(ok)
        if m == "ours":
            # 本项目还有 45 组根本没产出公式（能力边界），如实单列
            rate = "%d/%d（%.0f%%）" % (passed, 300, 100.0 * passed / 300)
            paired_txt = "—"
        else:
            rate = "%d/%d（%.0f%%）" % (passed, denom, 100.0 * passed / denom if denom else 0)
            paired_txt = "%d/%d（%.0f%%）" % (paired_pass, len(paired),
                                             100.0 * paired_pass / len(paired) if paired else 0)
        A("| %s | %d | %s | %.4f | %.4f | **%s** | %s | %s | %s |" % (
            METHOD_NAMES.get(m, m), len(rs),
            ("%.3f" % r2tr[m]) if m in r2tr else "—",
            med_in, med_ex, rate,
            ("%.2fs" % tm[0]) if tm else "—",
            "否" if m in ("rf", "gbrt") else "是", paired_txt))
    A("")
    A("## 怎么读这张表（把话说清楚）\n")
    A("1. **这两个黑箱方法不是「算法更弱」，而是「方法类别不同」**：随机森林与梯度提升")
    A("   本质是**插值式**拟合器——它们只能在训练区间内平滑，区间外退化成常数或线性。")
    A("   所以它们在区间内的 nRMSE 往往还不错，**外推一栏是它们的天花板所在**。")
    A("2. **这不是稻草人**：同一份协议的 gplearn（遗传规划符号回归）是同类方法，")
    A("   冒烟测试里它在 I.39.22 上外推 R² 拿到 1.0——真正的对手要留在后面单独比。")
    A("3. **本项目占的是不同位置**：给出**闭式公式**（可读、可审计、可追溯到量纲），")
    A("   并且**没有数据也能外推**（外推检验正是为此设的验收门槛）。")
    A("4. **分母要说清楚**：本项目的通过率按**全部 300 组**算（没有产出公式的 45 组计为未通过，")
    A("   因为它们拿不出可用的定律）；基线的 300 组里有 9 组真值为常数（标准差为 0，")
    A("   归一化误差无定义）已剔除。**同题配对**一列只统计本项目也出了公式的那些组，")
    A("   用来排除「题目难度不同」这一解释。")
    A("5. 口径完全一致：阈值（区间内 0.05 / 外推 0.20）就是本项目 `verify_formula` 的默认值，")
    A("   没有为任何一方调整。本项目一侧的数字直接取自 `results/verify_ab.csv`。\n")
    A("生成命令：`python scripts/export_baseline_input.py` →")
    A("`.venv-baseline/Scripts/python.exe scripts/run_baseline.py` →")
    A("`python scripts/score_baseline.py`")
    open(os.path.join(RES, "baseline_report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("结论报告：results/baseline_report.md")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
