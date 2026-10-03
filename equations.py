# -*- coding: utf-8 -*-
"""
equations.py —— 内置物理方程库 + 观测数据生成

【这个文件负责什么】
1. 定义一个"迷你物理方程库"：20 多条有标准答案的物理定律。
   每条都记着：真值公式、有哪些变量、每个变量的单位、采样区间。
2. 按指定噪声水平，从真值公式生成"带噪声的实验观测数据"。

【为什么需要"标准答案"】
因为我们的目标是"用 AI 从数据里反推出公式"，那就必须有一把尺子来量
它到底推对没有。真值公式就是这把尺子——评审里"结果正确性"和"验证严谨性"
两项都靠它。

【关于数据来源的诚实说明】
这个库是手写的迷你库，**不是**官方的 Feynman 符号回归基准数据集。
但它与官方基准的结构完全一致（公式 + 变量 + 单位 + 采样区间），
其中若干条目就是官方基准里的原题（下面用 ref 字段标明编号）。
这样做的原因是：官方完整库约 6.5GB、需要联网下载，先用迷你库把
"算法能否跑通"这件事验证掉，之后再无缝替换成官方数据。
"""

import os

import numpy as np

from dims import expr_dim as _dims_expr_dim


# ---------------------------------------------------------------------------
# 求解表达式量纲：转发给 dims.expr_dim
# 保留这个三参数签名，是因为 features.py 的自检里已经在这么调用它了。
# 真正的实现在 dims.py —— 放那里是为了让方程库和它自己的自检都能用，
# 而不会形成循环依赖。
# ---------------------------------------------------------------------------
def _expr_dim(e, dims, parse_unit=None):
    """算一个 sympy 表达式的量纲。parse_unit 参数保留只为兼容旧签名。"""
    return _dims_expr_dim(e, dims)


# ---------------------------------------------------------------------------
# 方程库
#   expr      真值公式（用 ** 表示乘方；三角函数参数是弧度）
#   vars      变量名 -> 单位（空字符串 "" 表示无量纲）
#   ranges    变量名 -> 采样区间；写第三个元素 "log" 表示对数均匀采样
#   target    目标变量名（我们要求 AI 反推出的那个量）
#   ref       公式出处
# ---------------------------------------------------------------------------
MINI_EQUATIONS = [
    dict(id="P01", name="滑动摩擦力", ref="Feynman I.12.1",
         target="f_f", target_unit="N", expr="mu*f_n",
         vars={"mu": "", "f_n": "N"},
         ranges={"mu": (0.1, 1.0), "f_n": (1.0, 10.0)}),

    dict(id="P02", name="动量", ref="经典力学",
         target="p", target_unit="kg*m/s", expr="m*v",
         vars={"m": "kg", "v": "m/s"},
         ranges={"m": (0.5, 10.0), "v": (0.5, 20.0)}),

    dict(id="P03", name="动能", ref="经典力学",
         target="energy", target_unit="J", expr="m*v**2/2",
         vars={"m": "kg", "v": "m/s"},
         ranges={"m": (0.5, 10.0), "v": (0.5, 20.0)}),

    dict(id="P04", name="欧姆定律", ref="电学",
         target="volt", target_unit="V", expr="curr*res",
         vars={"curr": "A", "res": "ohm"},
         ranges={"curr": (0.01, 2.0, "log"), "res": (1.0, 1000.0, "log")}),

    dict(id="P05", name="电功率", ref="电学",
         target="power", target_unit="W", expr="volt*curr",
         vars={"volt": "V", "curr": "A"},
         ranges={"volt": (1.0, 240.0), "curr": (0.01, 2.0, "log")}),

    dict(id="P06", name="弹簧弹性势能", ref="经典力学",
         target="energy", target_unit="J", expr="k*x**2/2",
         vars={"k": "N/m", "x": "m"},
         ranges={"k": (1.0, 500.0, "log"), "x": (-2.0, 2.0)}),

    dict(id="P07", name="牛顿第二定律", ref="经典力学",
         target="acc", target_unit="m/s^2", expr="f/m",
         vars={"f": "N", "m": "kg"},
         ranges={"f": (0.1, 100.0, "log"), "m": (0.5, 20.0)}),

    dict(id="P08", name="密度", ref="经典力学",
         target="rho", target_unit="kg/m^3", expr="m/vol",
         vars={"m": "kg", "vol": "m^3"},
         ranges={"m": (0.1, 50.0, "log"), "vol": (0.001, 1.0, "log")}),

    dict(id="P09", name="压强", ref="经典力学",
         target="pres", target_unit="Pa", expr="f/area",
         vars={"f": "N", "area": "m^2"},
         ranges={"f": (1.0, 1000.0, "log"), "area": (0.001, 1.0, "log")}),

    dict(id="P10", name="安培力", ref="电磁学",
         target="f", target_unit="N", expr="curr*ell*b",
         vars={"curr": "A", "ell": "m", "b": "T"},
         ranges={"curr": (0.1, 5.0), "ell": (0.1, 2.0), "b": (0.01, 2.0, "log")}),

    dict(id="P11", name="单摆周期", ref="经典力学",
         target="period", target_unit="s", expr="sqrt(ell/g)",
         vars={"ell": "m", "g": "m/s^2"},
         ranges={"ell": (0.1, 5.0), "g": (1.0, 20.0)}),

    dict(id="P12", name="热传导功率", ref="Feynman II.2.42",
         target="qdot", target_unit="W", expr="cond*area*dtemp/thick",
         vars={"cond": "W/m/K", "area": "m^2", "dtemp": "K", "thick": "m"},
         ranges={"cond": (0.1, 400.0, "log"), "area": (0.01, 1.0, "log"),
                 "dtemp": (1.0, 100.0), "thick": (0.001, 0.1, "log")}),

    dict(id="P13", name="角动量", ref="经典力学",
         target="ang", target_unit="kg*m^2/s", expr="m*v*r",
         vars={"m": "kg", "v": "m/s", "r": "m"},
         ranges={"m": (0.5, 10.0), "v": (0.5, 20.0), "r": (0.1, 5.0)}),

    dict(id="P14", name="环形电流磁矩", ref="Feynman II.34.2",
         target="mag", target_unit="A*m^2", expr="q*v*r/2",
         vars={"q": "C", "v": "m/s", "r": "m"},
         ranges={"q": (1e-6, 1e-3, "log"), "v": (0.5, 20.0), "r": (0.1, 5.0)}),

    dict(id="P15", name="力矩", ref="Feynman I.18.4",
         target="torque", target_unit="N*m", expr="r*f*sin(theta)",
         vars={"r": "m", "f": "N", "theta": "rad"},
         ranges={"r": (0.1, 5.0), "f": (1.0, 100.0), "theta": (0.1, 1.5)}),

    dict(id="P16", name="匀强电场强度", ref="电磁学",
         target="efield", target_unit="V/m", expr="volt/d",
         vars={"volt": "V", "d": "m"},
         ranges={"volt": (1.0, 1000.0, "log"), "d": (0.001, 1.0, "log")}),

    dict(id="P17", name="电容电荷量", ref="电学",
         target="charge", target_unit="C", expr="cap*volt",
         vars={"cap": "F", "volt": "V"},
         ranges={"cap": (1e-9, 1e-3, "log"), "volt": (1.0, 100.0)}),

    dict(id="P18", name="电容储能", ref="电学",
         target="energy", target_unit="J", expr="cap*volt**2/2",
         vars={"cap": "F", "volt": "V"},
         ranges={"cap": (1e-9, 1e-3, "log"), "volt": (1.0, 100.0)}),

    # --- 下面三条的目标是无量纲量，考验特征库对"比值"的表达能力 ---
    dict(id="P19", name="相对论因子（预期失败案例）", ref="狭义相对论",
         target="gamma", target_unit="", expr="1/sqrt(1-v**2/c_light**2)",
         vars={"v": "m/s", "c_light": "m/s"},
         ranges={"v": (1e7, 2e8), "c_light": (2.9e8, 3.1e8)}),

    dict(id="P20", name="指数衰减", ref="经典",
         target="decay", target_unit="", expr="exp(-t/tau)",
         vars={"t": "s", "tau": "s"},
         ranges={"t": (0.5, 5.0), "tau": (0.5, 2.0)}),

    dict(id="P21", name="高斯核", ref="Feynman I.6.20",
         target="gauss", target_unit="", expr="exp(-x**2/(2*sigma**2))",
         vars={"x": "", "sigma": ""},
         ranges={"x": (-3.0, 3.0), "sigma": (0.5, 2.0)}),
]


# ---------------------------------------------------------------------------
# 选择使用哪一套方程库
#
#   mini（默认）  —— 上面这份手写的 21 条迷你库，与官方基准结构一致
#   feynman      —— 官方 Feynman 符号回归基准的 100 条方程（data/FeynmanEquations.csv）
#
# 切换方式（环境变量，主要给 experiment.py 批量评测用）：
#     Linux/macOS :  SCIFORMULA_LIBRARY=feynman python experiment.py
#     Windows     :  $env:SCIFORMULA_LIBRARY="feynman"; python experiment.py
#
# 【AGH / MCP 路径不走环境变量】——AGH 拉起 mcp_server.py 的进程时看不到
# 用户 shell 里的环境变量。所以 MCP 工具（list_problems / load_problem）
# 带了显式的 library 参数，两套库在同一个进程里共存、按需装载：
#   EQ.ensure_library("feynman")  首次调用时读 CSV，之后复用
#   EQ.find(eq_id)                在已装载的库里跨库解析（mini 优先；两库编号
#                                 不重叠——mini 是 P01..P21，feynman 是 I.6.2a 这类）
# 下游 pipeline 只调 EQ.get()，不感知库的存在，一行都不用改。
# ---------------------------------------------------------------------------
LIBRARY = os.environ.get("SCIFORMULA_LIBRARY", "mini").strip().lower()
SKIPPED = []          # 装载官方数据集时被量纲自检排除的条目（含原因）

# 已装载的库：name -> 方程列表。mini 是内存里的常量，零成本先放进来；
# feynman 首次用到才读 CSV（读取 + 量纲自检约几十毫秒，懒装载完全够用）。
_LIBRARIES = {}


def ensure_library(name):
    """装载（或返回已装载的）一套方程库。返回方程列表。"""
    global SKIPPED
    name = (name or "mini").strip().lower()
    if name in ("feynman", "official", "benchmark"):
        name = "feynman"
    else:
        name = "mini"
    if name not in _LIBRARIES:
        if name == "feynman":
            import feynman
            eqs, skipped = feynman.load()
            SKIPPED = skipped
            _LIBRARIES[name] = eqs
        else:
            _LIBRARIES[name] = MINI_EQUATIONS
    return _LIBRARIES[name]


def library_names():
    """当前进程里已装载的库名列表（调试与自检用）"""
    return sorted(_LIBRARIES.keys())


def find(eq_id):
    """
    在已装载的库里跨库找一条方程，返回 (方程, 库名)；找不到返回 (None, None)。
    mini 优先；mini 里没有时自动装载 feynman 再找——这让 pipeline 下游的
    EQ.get(meta["problem_id"]) 无论题目来自哪套库都能解析到。
    """
    for e in _LIBRARIES.get("mini", []):
        if e["id"] == eq_id:
            return e, "mini"
    if "feynman" not in _LIBRARIES:
        try:
            ensure_library("feynman")
        except Exception:
            pass
    for e in _LIBRARIES.get("feynman", []):
        if e["id"] == eq_id:
            return e, "feynman"
    return None, None


def _load_library():
    ensure_library("mini")                      # mini 永远可用（零成本）
    return ensure_library(LIBRARY)              # 再按环境变量装载主库


EQUATIONS = _load_library()


def get(eq_id):
    """按 id 取出某条方程（跨已装载的库解析）"""
    eq, _lib = find(eq_id)
    if eq is None:
        raise KeyError(eq_id)
    return eq

def varnames(eq):
    """返回这条方程的变量名列表（顺序固定，和 vars 字典的书写顺序一致）"""
    return list(eq["vars"].keys())


def _sample(rng, lo, hi, n, mode):
    """在一个区间里采 n 个点；mode 为 'log' 时按对数均匀采样"""
    if mode == "log":
        return np.exp(rng.uniform(np.log(lo), np.log(hi), n))
    return rng.uniform(lo, hi, n)


def sample_data(eq, n=100, noise=0.0, seed=0):
    """
    生成一批"观测数据"。

    参数
        n      样本数
        noise  噪声水平：加到真值上的高斯噪声，标准差 = noise * std(真值)。
               noise=0 表示无噪声（用来先确认算法本身对不对）
    返回
        X      形状 (n, 变量数)
        y      形状 (n,)  目标量的观测值（带噪声）
        y_clean 形状 (n,) 同一批点的真值（无噪声，用于对照，不参与建模）
    """
    rng = np.random.default_rng(seed)
    names = varnames(eq)
    cols = []
    for name in names:
        spec = eq["ranges"][name]
        mode = spec[2] if len(spec) > 2 else "linear"
        cols.append(_sample(rng, spec[0], spec[1], n, mode))
    X = np.column_stack(cols)

    # 把变量塞进求值环境里算真值
    env = dict(zip(names, X.T))
    y_clean = eval(eq["expr"], {"__builtins__": {}, "sqrt": np.sqrt, "exp": np.exp,
                                "sin": np.sin, "cos": np.cos, "log": np.log,
                                "tanh": np.tanh, "arcsin": np.arcsin,
                                "pi": np.pi}, env)
    y_clean = np.asarray(y_clean, dtype=float) * np.ones(n)

    if noise > 0:
        y = y_clean + noise * np.std(y_clean) * rng.standard_normal(n)
    else:
        y = y_clean.copy()
    return X, y, y_clean


def make_truth_expr(eq):
    """把真值公式转成 sympy 表达式，供符号等价判定使用"""
    import sympy as sp
    names = varnames(eq)
    syms = sp.symbols(names)
    return sp.sympify(eq["expr"], locals=dict(zip(names, syms)))


def _fraction_of(x):
    """兼容旧调用：转发给 dims.fraction_of"""
    from dims import fraction_of
    return fraction_of(x)


# ---------------------------------------------------------------------------
# 自检：直接运行本文件，会把每条方程的"声明单位"和"公式实际量纲"对一遍。
# 这一步能抓出单位表里的笔误——比如把 J 写成 N 之类的低级错误，
# 否则后面量纲剪枝会静默地剪掉正确的项，你根本不知道错在哪。
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import sympy as sp
    sys.stdout.reconfigure(encoding="utf-8")
    from dims import parse_unit, fmt, same

    print("=== 当前方程库：%s（共 %d 条）===" % (LIBRARY, len(EQUATIONS)))
    if SKIPPED:
        print("=== 另有 %d 条因量纲自检未通过被排除（见 feynman.py 的报告）===" % len(SKIPPED))
    print("%-10s %-22s %-6s %-14s %s" % ("ID", "名称", "变量数", "目标量纲", "来源"))
    bad = []
    for eq in EQUATIONS:
        d = parse_unit(eq["target_unit"])
        names = varnames(eq)
        syms = sp.symbols(names)
        e = sp.sympify(eq["expr"], locals=dict(zip(names, syms)))
        dims = {name: parse_unit(eq["vars"][name]) for name in names}
        try:
            calc = _expr_dim(e, dims, parse_unit)
        except Exception as exc:
            bad.append((eq["id"], eq["name"], "检查失败", str(exc)))
            calc = d
        if not same(calc, d):
            bad.append((eq["id"], eq["name"], fmt(calc), "声明为 %s" % (eq["target_unit"] or "无量纲")))
        print("%-10s %-22s %-6d %-14s %s" % (
            eq["id"], eq["name"], len(names),
            eq["target_unit"] or "(无量纲)", eq["ref"]))

    print("\n=== 量纲自检：加法是否同量纲、三角函数参数是否无量纲 ===")
    if bad:
        print("  有 %d 条没通过：" % len(bad))
        for row in bad[:40]:
            print("    FAIL %s %s：%s" % (row[0], row[1], row[2]))
        if len(bad) > 40:
            print("    ...（还有 %d 条）" % (len(bad) - 40))
    else:
        print("  全部通过 ✔")

    print("\n=== 数据生成自检 ===")
    probe = "P03" if any(x["id"] == "P03" for x in EQUATIONS) else EQUATIONS[0]["id"]
    X, y, yc = sample_data(get(probe), n=5, noise=0.0, seed=1)
    print("  %s：前 3 个观测值 %s" % (probe, np.round(y[:3], 5)))
    print("  与真值完全一致：", np.allclose(y, yc))
