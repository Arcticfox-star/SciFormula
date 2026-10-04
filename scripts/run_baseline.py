# -*- coding: utf-8 -*-
"""
run_baseline.py —— 在隔离的基线环境里跑对照方法（**必须用 .venv-baseline 的解释器运行**）

【只读什么】
只读 `results/baseline_input/<tag>_train.npz`（拟合用）与 `<tag>_query.npz`（要出预测的点）。
**不读** `<tag>_truth.npz`——真值只给主环境的评分脚本。想偷看也没有。

【跑哪些方法】
    rf     随机森林（scikit-learn）——黑箱，给不出公式
    gbrt   梯度提升（scikit-learn）——黑箱，给不出公式
    gp     gplearn 遗传规划符号回归——会给出一条公式（默认关闭，见 --methods）

【怎么用】
    .venv-baseline/Scripts/python.exe scripts/run_baseline.py
    .venv-baseline/Scripts/python.exe scripts/run_baseline.py --methods rf,gbrt,gp
    .venv-baseline/Scripts/python.exe scripts/run_baseline.py --limit 10      # 先小跑试试

产物：results/baseline_pred/<method>__<tag>.npz（预测值）+ results/baseline_timing.csv
"""

import argparse
import csv
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IN = os.path.join(ROOT, "results", "baseline_input")
PRED = os.path.join(ROOT, "results", "baseline_pred")
TIMING = os.path.join(ROOT, "results", "baseline_timing.csv")

GP_FUNCTIONS = ("add", "sub", "mul", "div", "sqrt", "log", "abs", "neg", "inv")


def make_model(kind, n_vars):
    if kind == "rf":
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(n_estimators=200, random_state=0, n_jobs=1)
    if kind == "gbrt":
        from sklearn.ensemble import GradientBoostingRegressor
        return GradientBoostingRegressor(random_state=0)
    if kind == "gp":
        from gplearn.genetic import SymbolicRegressor
        return SymbolicRegressor(population_size=1000, generations=20,
                                 function_set=GP_FUNCTIONS, random_state=0, n_jobs=1)
    raise ValueError(kind)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="rf,gbrt")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 组（调试用）")
    a = ap.parse_args()
    methods = [m.strip() for m in a.methods.split(",") if m.strip()]

    os.makedirs(PRED, exist_ok=True)
    manifest = json.load(open(os.path.join(IN, "manifest.json"), encoding="utf-8"))
    if a.limit:
        manifest = manifest[:a.limit]
    print("基线实验：%d 组数据 × %d 种方法（%s）" % (len(manifest), len(methods), ",".join(methods)))

    rows, t_all = [], time.time()
    for i, item in enumerate(manifest, 1):
        tag = item["tag"]
        tr = np.load(os.path.join(IN, tag + "_train.npz"))
        qy = np.load(os.path.join(IN, tag + "_query.npz"))
        X, y, Xi, Xe = tr["X"], tr["y"], qy["Xi"], qy["Xe"]

        for kind in methods:
            t0 = time.time()
            try:
                model = make_model(kind, item["n_vars"])
                model.fit(X, y)
                pin, pex = model.predict(Xi), model.predict(Xe)
                sec = time.time() - t0
                np.savez(os.path.join(PRED, "%s__%s.npz" % (kind, tag)),
                         pred_in=pin, pred_ex=pex)
                r2tr = float(model.score(X, y))
                expr = str(model) if kind == "gp" else ""
                rows.append(dict(method=kind, tag=tag, ok=1, seconds=round(sec, 3),
                                 r2_train=round(r2tr, 6), expr=expr))
            except Exception as exc:                     # 单个失败不能拖垮整批
                rows.append(dict(method=kind, tag=tag, ok=0, seconds=round(time.time() - t0, 3),
                                 r2_train="", expr="", error=str(exc)[:120]))
        if i % 25 == 0:
            print("  [%3d/%d] 已用时 %.0fs" % (i, len(manifest), time.time() - t_all))

    cols = ["method", "tag", "ok", "seconds", "r2_train", "expr", "error"]
    new = not os.path.exists(TIMING)
    with open(TIMING, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)

    bad = [r for r in rows if r["ok"] == 0]
    print("\n完成 %d 次拟合（失败 %d 次），总耗时 %.0fs" % (len(rows), len(bad), time.time() - t_all))
    for r in bad[:5]:
        print("  失败样例：%s %s → %s" % (r["method"], r["tag"], r.get("error", "")[:80]))
    print("预测落盘：results/baseline_pred/ | 计时：results/baseline_timing.csv")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
