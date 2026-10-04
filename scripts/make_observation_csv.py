# -*- coding: utf-8 -*-
"""
make_observation_csv.py —— 把观测数据**物化成磁盘上的 CSV 文件**（一次性生成）

【为什么要有这一步】
竞赛规则要求「由 AGH 连接数据、专业软件、仿真环境或设备接口」。
之前我们的观测数据是在进程内即时合成的：智能体看不到"数据"这个东西，
只看到工具返回的数组。这个脚本把数据落成磁盘文件，于是：

    智能体 →（AGH 的 MCP 通道）→ 读磁盘上的数据文件 → 走发现流程

好处不只是"合规"：**数据文件是可替换的**。以后换成真实测量数据（实验记录、
公开数据集），同一套流程照跑，不需要改任何算法。

【生成什么】
    data/observations/<题目编号>.csv     变量列 + 目标量列（200 行观测值，1% 噪声）
    data/observations/README.md          来源说明（含"不含公式、不含单位"的声明）

【纪律】
CSV 里**只有观测值**：不含真值公式、不含单位——这两样都不给智能体。
真值仍在官方数据集文件（data/FeynmanEquations.csv）里，只有验证/评分环节读它。

【怎么用】
    SCIFORMULA_LIBRARY=feynman python scripts/make_observation_csv.py
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np                                  # noqa: E402
import equations as EQ                              # noqa: E402
from equivalence import evaluate                    # noqa: E402
from pipeline import add_noise, sample_X            # noqa: E402

OUT = os.path.join(ROOT, "data", "observations")
N_SAMPLES, NOISE, SEED = 200, 0.01, 0

README = """# 观测数据文件（由官方 Feynman 数据集方程采样生成）

这些 CSV 是**观测数据**：每行是一次实验/采样，列为各变量取值与目标量观测值。

**刻意不包含的东西**：真值公式、各量的单位。
它们只存在于 `data/FeynmanEquations.csv`（官方数据集文件）中，供验证与评分环节使用，
智能体通过 AGH 的 MCP 工具读这些 CSV 时看不到任何真值信息。

生成参数：`n_samples=%(n)d`、噪声 %(noise)s、随机种子 %(seed)d。
生成脚本：`scripts/make_observation_csv.py`（可重跑，结果确定）。

**怎样换成真实数据**：把同格式的 CSV 放进本目录即可——列名写变量名、
最后一列写目标量名。只要名字与官方题库对应，验证环节就能用题库里的参考解；
否则该数据集只能做拟合，做不了独立验证（工具会明确告知）。
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    eqs = EQ.ensure_library("feynman")
    print("物化观测数据：%d 题 → %s" % (len(eqs), os.path.relpath(OUT, ROOT)))
    for k, eq in enumerate(eqs, 1):
        names = EQ.varnames(eq)
        X = sample_X(eq, N_SAMPLES, SEED, expand=0.0)
        y = add_noise(evaluate(eq["expr"], X, names), NOISE, SEED)
        path = os.path.join(OUT, eq["id"] + ".csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(list(names) + [eq["target"]])
            for row, val in zip(X, y):
                w.writerow([("%.10g" % v) for v in row] + ["%.10g" % float(val)])
        if k % 25 == 0:
            print("  [%3d/%d]" % (k, len(eqs)))
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write(README % dict(n=N_SAMPLES, noise=NOISE, seed=SEED))
    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT) if f.endswith(".csv"))
    print("\n完成：%d 个 CSV，合计 %.1f KB" % (len(eqs), total / 1024.0))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
