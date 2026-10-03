# -*- coding: utf-8 -*-
"""
feynman.py —— 加载官方 Feynman 符号回归基准数据集

【数据来源】
`data/FeynmanEquations.csv`，取自 AI Feynman 论文（Udrescu & Tegmark, 2020）
随论文发布的 Feynman Symbolic Regression Database 的原始 100 条方程。
原始下载地址（MIT 站点）已 404，这里用的是 GitHub 上的同内容副本：
    https://github.com/chrishaider/FeynmanEquations-Python
    （该仓库说明其 CSV 于 2022-04-01 取自 space.mit.edu/home/tegmark/aifeynman.html）

CSV 结构（一行一条方程）：
    Filename, Number, Output, Formula, # variables,
    v1_name, v1_low, v1_high, v2_name, v2_low, v2_high, ... v10_name, v10_low, v10_high

【这个文件做了什么】
1. 把 CSV 解析成和 `equations.py` 内置迷你库**完全相同的字典结构**，
   这样下游（features / sparse / pipeline / experiment）一行都不用改。
2. 用 `feynman_units.py` 的单位表给每个变量配上单位，
   目标量的量纲**由公式和变量单位算出来**（不手写）。
3. 逐条做量纲自检；不自洽的条目被**排除并记录原因**，不静默混进去。

【关于"为什么有的方程会被排除"】
见 feynman_units.py 的 NOTES。这个文件只管执行检查、如实报告。
"""

import csv
import io
import os

import numpy as np

from dims import ZERO, expr_dim_str, fmt, parse_unit, same
from feynman_units import DEFAULT_UNITS, OVERRIDES

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CSV_NAME = "FeynmanEquations.csv"


def _normalize_formula(f):
    """
    把 CSV 里的公式写法统一成我们求值环境认得的写法。
    CSV 用的是人类写法，和 Python 的数学表达式基本一致，差别只有几处函数名。
    """
    s = f.strip()
    s = s.replace("ln(", "log(")          # CSV 用 ln，numpy 叫 log
    s = s.replace("arcsin(", "arcsin(")   # 保持原样（求值环境里有）
    return s


def _parse_rows(path):
    """读出 CSV 里的原始行（跳过空行）"""
    with io.open(path, encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if r and r[0].strip()]
    return rows[0], rows[1:]


def _unit_of(filename, varname):
    """先查逐方程覆盖，再查全局表"""
    key = (filename, varname)
    if key in OVERRIDES:
        return OVERRIDES[key]
    return DEFAULT_UNITS.get(varname)


def build_equations(csv_name=CSV_NAME, data_dir=DATA_DIR):
    """
    解析数据集，返回 (equations, skipped)。

    equations  结构与你熟悉的迷你库一致：
               id / name / ref / target / target_unit / expr / vars / ranges
    skipped    量纲自检没过的条目，形如 dict(id=..., reason=...)，
               单独返回而不是丢掉——报告里要如实写出有多少条、为什么。
    """
    header, rows = _parse_rows(os.path.join(data_dir, csv_name))

    equations, skipped = [], []
    for r in rows:
        filename = r[0].strip()
        output = r[2].strip()
        formula = _normalize_formula(r[3])
        try:
            nv = int(r[4])
        except ValueError:
            skipped.append(dict(id=filename, reason="无法解析变量个数：%r" % r[4]))
            continue

        # ---- 变量名与采样区间 ----
        varnames, ranges, missing = [], {}, []
        for i in range(nv):
            base = 5 + 3 * i
            if base + 2 >= len(r) or not r[base].strip():
                missing.append(i + 1)
                continue
            name = r[base].strip()
            varnames.append(name)
            ranges[name] = (float(r[base + 1]), float(r[base + 2]))
        if missing:
            skipped.append(dict(id=filename, reason="第 %s 个变量缺名字或区间" % missing))
            continue

        # ---- 单位表 ----
        units, no_unit = {}, []
        for name in varnames:
            u = _unit_of(filename, name)
            if u is None:
                no_unit.append(name)
                continue
            units[name] = u
        if no_unit:
            skipped.append(dict(id=filename,
                                reason="单位表里缺这些变量的单位：%s" % ", ".join(no_unit)))
            continue

        # ---- 目标量量纲：由公式和变量单位算出来 ----
        try:
            dims = {n: parse_unit(units[n]) for n in varnames}
            target_dim = expr_dim_str(formula, dims)
        except Exception as exc:
            skipped.append(dict(id=filename, reason="量纲自检未通过：%s" % exc))
            continue

        equations.append(dict(
            id=filename.replace("/", "_"),
            name=filename,                       # 这份数据集没有题名，用编号当名字
            ref="Feynman %s" % filename,
            target=output,
            target_unit=fmt(target_dim),         # 算出来的，不是手写的
            expr=formula,
            vars={n: units[n] for n in varnames},
            ranges=ranges,
        ))

    return equations, skipped


_CACHE = {}


def load(csv_name=CSV_NAME):
    """带缓存的加载，返回 (equations, skipped)"""
    if csv_name not in _CACHE:
        _CACHE[csv_name] = build_equations(csv_name)
    return _CACHE[csv_name]


def coverage_report(equations, skipped):
    """给出一份可读的装载情况说明（用于报告）"""
    lines = []
    total = len(equations) + len(skipped)
    lines.append("数据集：%s" % CSV_NAME)
    lines.append("总条目 %d，成功装载 %d，排除 %d" % (total, len(equations), len(skipped)))
    if skipped:
        lines.append("")
        lines.append("被排除的条目及原因：")
        for s in skipped:
            lines.append("  %-12s %s" % (s["id"], s["reason"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 自检：直接运行本文件，会解析并报告装载情况
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    eqs, sk = load()
    print("=" * 72)
    print(coverage_report(eqs, sk))
    print("=" * 72)

    if eqs:
        nv = [len(e["vars"]) for e in eqs]
        print("\n变量个数分布：1 变量 %d 条，2 变量 %d 条，3 变量 %d 条，4+ 变量 %d 条"
              % (sum(1 for k in nv if k == 1), sum(1 for k in nv if k == 2),
                 sum(1 for k in nv if k == 3), sum(1 for k in nv if k >= 4)))
        print("\n前 8 条装载结果：")
        print("%-10s %-6s %-14s %-34s %s" % ("编号", "目标", "目标量纲", "公式", "变量单位"))
        for e in eqs[:8]:
            print("%-10s %-6s %-14s %-34s %s" % (
                e["id"], e["target"], e["target_unit"], e["expr"][:34],
                ", ".join("%s:%s" % (k, v) for k, v in e["vars"].items())))

        # 抽样验证：真值公式在随机点上能算出来、且是有限值
        print("\n求值自检（前 3 条，各取 5 个点）：")
        from equations import sample_data
        for e in eqs[:3]:
            try:
                X, y, yc = sample_data(e, n=5, noise=0.0, seed=1)
                print("  %-10s y = %s" % (e["id"], np.round(y, 4)))
            except Exception as exc:
                print("  %-10s 求值失败：%s" % (e["id"], exc))
