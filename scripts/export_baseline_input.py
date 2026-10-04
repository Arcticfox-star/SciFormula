# -*- coding: utf-8 -*-
"""
export_baseline_input.py —— 为对照实验准备数据（在主环境里跑）

【边界设计（重要）】
每个 (题目, 噪声档) 导出三个文件，基线只能看到前两个：

    <tag>_train.npz   X, y            ← 基线用它拟合
    <tag>_query.npz   Xi, Xe          ← 基线在**这两批点上出预测**（不含真值）
    <tag>_truth.npz   yi, ye          ← 只有评分脚本读它（真值）

这样即使基线脚本想"偷看答案"也看不到——和 `posthoc_eval.py` 不进 MCP 工具集是同一条纪律。

    Xi, yi：训练区间内的**新采样点**（seed 与训练不同）——对应我们验收口径 G1
    Xe, ye：向区间外扩 30% 的点——对应 G2

【怎么用】
    SCIFORMULA_LIBRARY=feynman python scripts/export_baseline_input.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np                                # noqa: E402
import equations as EQ                            # noqa: E402
import pipeline as P                              # noqa: E402

OUT = os.path.join(ROOT, "results", "baseline_input")
NOISES = (0.0, 0.01, 0.02)
N_TRAIN, N_QUERY = 100, 100
SEED, QUERY_SEED = 0, 7          # 与 experiment.py / verify_formula 的种子口径一致


def tag_of(pid, noise):
    return "%s__nz%s" % (pid, ("%g" % noise) if noise else "0")


def main():
    os.makedirs(OUT, exist_ok=True)
    eqs = EQ.ensure_library("feynman")
    manifest = []
    print("导出 %d 题 × %d 档噪声 → %s" % (len(eqs), len(NOISES), os.path.relpath(OUT, ROOT)))
    for k, eq in enumerate(eqs, 1):
        names = EQ.varnames(eq)
        for noise in NOISES:
            tag = tag_of(eq["id"], noise)
            d = P.load_problem(eq["id"], n_samples=N_TRAIN, noise=noise,
                               seed=SEED, mode=P.BENCHMARK)
            z = np.load(os.path.join(P.RUN_DIR, d["data_id"] + ".npz"))
            np.savez(os.path.join(OUT, tag + "_train.npz"), X=z["X"], y=z["y"])

            Xi = P.sample_X(eq, N_QUERY, QUERY_SEED, expand=0.0)
            Xe = P.sample_X(eq, N_QUERY, QUERY_SEED, expand=0.3)
            np.savez(os.path.join(OUT, tag + "_query.npz"), Xi=Xi, Xe=Xe)

            from equivalence import evaluate
            yi = evaluate(eq["expr"], Xi, names)
            ye = evaluate(eq["expr"], Xe, names)
            np.savez(os.path.join(OUT, tag + "_truth.npz"), yi=yi, ye=ye)

            manifest.append(dict(tag=tag, problem_id=eq["id"], noise=noise,
                                 variables=names, target=eq["target"],
                                 n_vars=len(names)))
        if k % 20 == 0:
            print("  [%3d/%d]" % (k, len(eqs)))

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("\n完成：%d 组数据（每组 3 个 npz）" % len(manifest))
    print("  训练/预测点给基线：*_train.npz、*_query.npz")
    print("  真值只给评分脚本：*_truth.npz")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
