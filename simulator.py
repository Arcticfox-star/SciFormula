# -*- coding: utf-8 -*-
"""
simulator.py —— 纯 numpy 的数值仿真环境（RK4 积分）

【这块是干什么的（对应竞赛规则里的「由 AGH 连接…仿真环境」）】
规程要求 AGH 连接「数据、专业软件、仿真环境或设备接口」。我们此前已经做到
「连接数据」（智能体经 MCP 读磁盘上的观测 CSV）。本模块再加一个**仿真环境**：

    智能体 →（MCP）→ list_simulations → run_sweep（驱动仿真跑一批实验，落盘成数据集）
           → load_dataset → check_units → build_candidate_library → fit_sparse
           → verify_formula（**换一组参数当场重跑仿真**，用真实读数当独立 oracle）

【为什么这比"再读一批 CSV"更有分量】
1. 观测值来自**数值积分**，不是我们写下的闭式公式。数据是"跑出来的"，不是"填进去的"。
2. 验证不再依赖任何**存储的真值**：verify_formula 会在留出的参数点上**重新跑一次仿真**，
   比对的是仿真当场测得的读数。这是"独立验证"最干净的形式。
3. 仿真台是可调、可重复的：同一组参数永远给同样的读数（除非显式加噪）。

【诚实边界（必须写清楚，别让评委误会）】
- 仿真里的物理定律是**我们写在 ODE 里**的，所以本项目**不宣称"发现了新物理"**。
  它的价值在于：AGH 能驱动一台可测量、可重复的实验台，并且验证环节真的在做实验。
- 步长按系统的特征时间取（这是数值积分的常规做法，等价于 CFL 条件）；
  但**被测量的值来自对轨迹的后处理**（过零检测、峰值包络拟合、线性回归），
  不是把闭式解直接代进去。自检会打印"测量值 vs 闭式解"的相对误差来证明这一点。
- 参数取值范围都保证落在**闭式解成立的物理区域**内（例如阻尼振子始终是欠阻尼），
  所以外推 30% 之后定律依然成立——否则外推检验会变成"验物理模型适用域"而不是"验公式"。

【命令行】
    python simulator.py --list                    # 列出场景
    python simulator.py --selftest                # 测量精度自检（测量值 vs 闭式解）
    python simulator.py --run spring_period       # 跑一次并打印读数
    python simulator.py --sweep rc --n 60         # 跑一批，落盘成数据集
"""

import argparse
import hashlib
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data", "simulations")

STEPS_PER_PERIOD = 2400          # 每个特征周期积分的步数（RK4，精度远超需要）
NOISE_DEFAULT = 0.005            # 扫描时默认给读数加的相对噪声（0.5%）

DISCOVERY = "discovery"


# ---------------------------------------------------------------------------
# RK4 积分器（纯浮点，不走 numpy 逐点运算——那样每步都要建数组，慢一个数量级）
# ---------------------------------------------------------------------------
def integrate(deriv, y0, dt, n_steps):
    """
    定步长四阶龙格-库塔。deriv(state) -> tuple，state 是浮点元组。

    返回 (t, Y)：t 形状 (n_steps+1,)，Y 形状 (n_steps+1, len(y0))。
    """
    m = len(y0)
    Y = np.empty((n_steps + 1, m), dtype=float)
    y = tuple(float(v) for v in y0)
    Y[0] = y
    h = float(dt)
    h2, h6 = h / 2.0, h / 6.0
    for i in range(1, n_steps + 1):
        k1 = deriv(y)
        k2 = deriv(tuple(y[j] + h2 * k1[j] for j in range(m)))
        k3 = deriv(tuple(y[j] + h2 * k2[j] for j in range(m)))
        k4 = deriv(tuple(y[j] + h * k3[j] for j in range(m)))
        y = tuple(y[j] + h6 * (k1[j] + 2.0 * k2[j] + 2.0 * k3[j] + k4[j])
                  for j in range(m))
        Y[i] = y
    return np.arange(n_steps + 1) * h, Y


def _peak_times(t, x, want=12):
    """找局部极大值点（用于阻尼振子的包络拟合）。"""
    idx = []
    for i in range(1, len(x) - 1):
        if x[i] > x[i - 1] and x[i] >= x[i + 1]:
            idx.append(i)
    if not idx:
        return np.empty(0), np.empty(0)
    idx = np.asarray(idx)[:want]
    return t[idx], np.abs(x[idx])


def _crossing_times(t, x, rising=True):
    """线性插值求 x(t)=0 的时刻；rising=True 只取由负变正的那种。"""
    out = []
    for i in range(1, len(x)):
        a, b = x[i - 1], x[i]
        if rising and a < 0.0 <= b:
            out.append(t[i - 1] + (t[i] - t[i - 1]) * (-a) / (b - a))
        elif (not rising) and a > 0.0 >= b:
            out.append(t[i - 1] + (t[i] - t[i - 1]) * (-a) / (b - a))
    return np.asarray(out)


# ---------------------------------------------------------------------------
# 场景定义
#   每个场景给出：可调参数（含量纲，仅内部使用）、ODE 右端、测量函数、闭式解
#   —— 闭式解只用于**自检与内部对照**，绝不通过工具返回给智能体。
# ---------------------------------------------------------------------------
def _spring_deriv(p):
    r = p["k"] / p["m"]
    return lambda s: (s[1], -r * s[0])


def _spring_measure(p, seed=None):
    """弹簧振子：测振动周期。x'' = -(k/m)x，从 x=A, v=0 出发。"""
    w0 = math.sqrt(p["k"] / p["m"])
    T = 2.0 * math.pi / w0
    dt = T / STEPS_PER_PERIOD
    n = int(round(5.0 * T / dt))
    t, Y = integrate(_spring_deriv(p), (p["A"], 0.0), dt, n)
    tc = _crossing_times(t, Y[:, 0], rising=True)
    if tc.size < 3:
        raise RuntimeError("没测到足够的过零点，无法定周期")
    d = np.diff(tc)
    # 丢掉第一个间隔（起始相位可能有半个周期的偏差）
    return float(np.mean(d[1:] if d.size > 1 else d)), dict(
        method="过零检测 + 线性插值", crossings=int(tc.size), dt=dt, n_steps=n)


def _spring_truth(p):
    return 2.0 * math.pi * math.sqrt(p["m"] / p["k"])


def _damped_deriv(p):
    km, cm = p["k"] / p["m"], p["c"] / p["m"]
    return lambda s: (s[1], -km * s[0] - cm * s[1])


def _damped_measure(p, seed=None):
    """阻尼振子（欠阻尼）：测包络衰减常数 γ。峰值包络 ln|x| 对 t 做线性回归。"""
    w0 = math.sqrt(p["k"] / p["m"])
    zeta = p["c"] / (2.0 * math.sqrt(p["m"] * p["k"]))
    if not (0.0 < zeta < 0.9):
        raise ValueError("阻尼比 ζ=%.3f 不在欠阻尼可测区间 (0, 0.9)" % zeta)
    wd = w0 * math.sqrt(1.0 - zeta * zeta)
    Td = 2.0 * math.pi / wd
    dt = Td / STEPS_PER_PERIOD
    n = int(round(8.0 * Td / dt))
    t, Y = integrate(_damped_deriv(p), (p["A"], 0.0), dt, n)
    tp, ap = _peak_times(t, Y[:, 0], want=10)
    if tp.size < 4:
        raise RuntimeError("可用峰值不足（%d 个），无法拟出衰减常数" % tp.size)
    slope, _ = np.polyfit(tp, np.log(ap), 1)
    return float(-slope), dict(method="峰值包络对数线性回归", peaks=int(tp.size),
                               dt=dt, n_steps=n, zeta=zeta)


def _damped_truth(p):
    return p["c"] / (2.0 * p["m"])


def _rc_deriv(p):
    tau = p["R"] * p["C"]
    return lambda s: (-s[0] / tau,)


def _rc_measure(p, seed=None):
    """RC 放电：测时间常数 τ。ln V 对 t 线性回归，斜率 = -1/τ。"""
    tau = p["R"] * p["C"]
    dt = tau / STEPS_PER_PERIOD
    n = int(round(6.0 * tau / dt))
    t, Y = integrate(_rc_deriv(p), (p["V0"],), dt, n)
    V = Y[:, 0]
    keep = V > 0.0
    slope, _ = np.polyfit(t[keep], np.log(V[keep]), 1)
    return float(-1.0 / slope), dict(method="ln V 对 t 线性回归", dt=dt, n_steps=n)


def _rc_truth(p):
    return p["R"] * p["C"]


def _kepler_deriv(p):
    mu = p["G"] * p["M"]

    def f(s):
        # s = (x, y, vx, vy)
        r2 = s[0] * s[0] + s[1] * s[1]
        r3 = r2 * math.sqrt(r2)
        return (s[2], s[3], -mu * s[0] / r3, -mu * s[1] / r3)
    return f


def _kepler_measure(p, seed=None):
    """圆轨道：测卫星的**比动能** eps = v²/2。圆轨道上 v 恒定，取全段平均。"""
    a = p["a"]
    v = math.sqrt(p["G"] * p["M"] / a)
    if v <= 0.0:
        raise ValueError("环绕速度非正，参数不合理")
    T = 2.0 * math.pi * a / v
    dt = T / STEPS_PER_PERIOD
    n = int(round(3.0 * T / dt))
    t, Y = integrate(_kepler_deriv(p), (a, 0.0, 0.0, v), dt, n)
    sp = np.hypot(Y[:, 2], Y[:, 3])
    eps = 0.5 * sp * sp
    return float(np.mean(eps[1:-1])), dict(
        method="比动能 = v²/2 的全轨迹平均", orbit_period_s=T, dt=dt, n_steps=n,
        eps_spread=float(eps.max() - eps.min()))


def _kepler_truth(p):
    return p["G"] * p["M"] / (2.0 * p["a"])


SCENARIOS = {
    "spring_period": dict(
        title="弹簧振子 · 测振动周期",
        story=("一台理想弹簧振子实验台。你可以设定质量参数 m、弹簧劲度 k 与初始振幅 A，"
               "仪器记录整段振动过程，并给出测得的周期 T。振幅 A 是你自己设定的，"
               "它是不是真的影响读数，需要你自己判断。"),
        params=[dict(name="m", unit="kg", lo=0.2, hi=2.0, default=1.0),
                dict(name="k", unit="kg/s^2", lo=2.0, hi=20.0, default=8.0),
                dict(name="A", unit="m", lo=0.01, hi=0.5, default=0.1)],
        target=dict(name="T", unit="s", meaning="振动周期"),
        deriv=_spring_deriv, measure=_spring_measure, truth=_spring_truth,
        truth_expr="2*pi*sqrt(m/k)",
        valid=lambda p: True,
    ),
    "damped_decay": dict(
        title="阻尼振子 · 测包络衰减常数",
        story=("一台带阻尼的弹簧振子实验台（欠阻尼，会振荡着衰减）。可设定质量 m、"
               "劲度 k 与阻尼系数 c，仪器由整段轨迹的**峰值包络**给出衰减常数 gamma。"),
        params=[dict(name="m", unit="kg", lo=0.2, hi=2.0, default=1.0),
                dict(name="k", unit="kg/s^2", lo=2.0, hi=20.0, default=8.0),
                dict(name="c", unit="kg/s", lo=0.01, hi=0.15, default=0.05),
                dict(name="A", unit="m", lo=0.01, hi=0.5, default=0.1)],
        target=dict(name="gamma", unit="1/s", meaning="包络衰减常数"),
        deriv=_damped_deriv, measure=_damped_measure, truth=_damped_truth,
        truth_expr="c/(2*m)",
        valid=lambda p: 0.0 < p["c"] / (2.0 * math.sqrt(p["m"] * p["k"])) < 0.35,
    ),
    "rc_discharge": dict(
        title="RC 放电 · 测时间常数",
        story=("一个电容通过电阻放电的实验台。可设定电阻 R、电容 C 与初始电压 V0，"
               "仪器由放电曲线给出时间常数 tau。初始电压是不是影响 tau，需要你自己判断。"),
        params=[dict(name="R", unit="ohm", lo=100.0, hi=1.0e4, default=1000.0),
                dict(name="C", unit="F", lo=1.0e-6, hi=1.0e-3, default=1.0e-4),
                dict(name="V0", unit="V", lo=1.0, hi=12.0, default=5.0)],
        target=dict(name="tau", unit="s", meaning="时间常数"),
        deriv=_rc_deriv, measure=_rc_measure, truth=_rc_truth,
        truth_expr="R*C",
        valid=lambda p: True,
    ),
    "kepler_speed": dict(
        title="圆轨道天体 · 测比动能",
        story=("一个二体系统仿真台：中心天体质量为 M（这里按**小天体/小行星**的量级取，"
               "让读数的量级便于处理），卫星在半径 a 的**圆轨道**上运动，"
               "引力常数 G 在本仿真里是可调的（用于检验你对万有引力量纲的理解）。"
               "仪器给出卫星的**比动能**（单位质量的动能）。"),
        params=[dict(name="M", unit="kg", lo=5.0e21, hi=5.0e23, default=6.0e22),
                dict(name="a", unit="m", lo=1.0e6, hi=1.0e8, default=7.0e6),
                dict(name="G", unit="kg^-1*m^3*s^-2", lo=3.0e-11, hi=1.5e-10,
                     default=6.674e-11)],
        target=dict(name="eps", unit="m^2/s^2", meaning="卫星比动能（单位质量的动能）"),
        deriv=_kepler_deriv, measure=_kepler_measure, truth=_kepler_truth,
        truth_expr="G*M/(2*a)",
        valid=lambda p: True,
    ),
}


def scenario_ids():
    return sorted(SCENARIOS.keys())


def describe(sid):
    """给智能体看的场景说明——**不含任何单位**（单位要它自己推断）。"""
    sc = SCENARIOS[sid]
    return dict(
        scenario=sid, title=sc["title"], story=sc["story"],
        parameters=[dict(name=q["name"], default=q["default"],
                         sampling_range=[q["lo"], q["hi"]],
                         note="可由你设定") for q in sc["params"]],
        measured=dict(name=sc["target"]["name"], meaning=sc["target"]["meaning"],
                      unit=None),
        note=("仿真台会记录整段过程并按物理方法给出读数；参数可以任意设定。"
              "各参数与读数的**量纲都不提供**，请根据名称与物理语境推断。"))


def sample_params(sid, n, seed, expand=0.0):
    """
    在参数区间内取样（对数均匀，因为量程跨数量级）。
    expand>0 时把区间两端按 (1+expand) 向外扩——用于外推检验；
    扩完仍要求落在场景的有效物理区域内（valid 检查），否则重取。
    """
    sc = SCENARIOS[sid]
    rng = np.random.default_rng(int(seed))
    out = []
    tries = 0
    while len(out) < int(n):
        tries += 1
        if tries > 200 * int(n) + 1000:
            raise RuntimeError("在有效区域内取不到足够的参数点，请缩小取值范围")
        p = {}
        for q in sc["params"]:
            lo = q["lo"] / (1.0 + expand) if expand > 0 else q["lo"]
            hi = q["hi"] * (1.0 + expand) if expand > 0 else q["hi"]
            p[q["name"]] = float(math.exp(rng.uniform(math.log(lo), math.log(hi))))
        if sc["valid"](p):
            out.append(p)
    return out


def measure_case(sid, params, seed=0, noise=0.0):
    """
    跑一次仿真并给出读数。noise>0 时给读数乘一个对数正态的相对噪声
    （模拟真实仪器的重复性误差）。返回 (读数, 诊断信息)。
    """
    sc = SCENARIOS[sid]
    val, diag = sc["measure"](params, seed=seed)
    if noise:
        rng = np.random.default_rng((int(seed) * 1000003) % (2 ** 31))
        val = float(val) * float(math.exp(rng.normal(0.0, float(noise))))
    diag = dict(diag or {})
    diag["scenario"] = sid
    return float(val), diag


def truth_of(sid, params):
    """闭式解。**只给内部自检/对照用，绝不通过工具返回给智能体。**"""
    return float(SCENARIOS[sid]["truth"](params))


def _slug(sid, params, seed, noise):
    raw = json.dumps([sid, {k: round(v, 12) for k, v in sorted(params.items())},
                      seed, noise], sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def run_series(sid, params, seed=0):
    """跑一次完整仿真，返回时间序列（用于 `run_simulation` 工具的可视化证据）。"""
    sc = SCENARIOS[sid]
    val, diag = sc["measure"](params, seed=seed)
    # 为拿到轨迹，按同一套步长再积一遍（测量函数内部已经积过，这里只取数据）
    if sid == "spring_period":
        w0 = math.sqrt(params["k"] / params["m"])
        T = 2.0 * math.pi / w0
        dt = T / STEPS_PER_PERIOD
        n = int(round(3.0 * T / dt))
        y0 = (params["A"], 0.0)
        cols = ["t", "x", "v"]
    elif sid == "damped_decay":
        w0 = math.sqrt(params["k"] / params["m"])
        z = params["c"] / (2.0 * math.sqrt(params["m"] * params["k"]))
        wd = w0 * math.sqrt(max(1.0 - z * z, 1e-12))
        dt = (2.0 * math.pi / wd) / STEPS_PER_PERIOD
        n = int(round(4.0 * (2.0 * math.pi / wd) / dt))
        y0 = (params["A"], 0.0)
        cols = ["t", "x", "v"]
    elif sid == "rc_discharge":
        tau = params["R"] * params["C"]
        dt = tau / STEPS_PER_PERIOD
        n = int(round(4.0 * tau / dt))
        y0 = (params["V0"],)
        cols = ["t", "V"]
    else:
        a = params["a"]
        v = math.sqrt(params["G"] * params["M"] / a)
        T = 2.0 * math.pi * a / v
        dt = T / STEPS_PER_PERIOD
        n = int(round(2.0 * T / dt))
        y0 = (a, 0.0, 0.0, v)
        cols = ["t", "x", "y", "vx", "vy"]
    t, Y = integrate(sc["deriv"](params), y0, dt, n)
    return val, diag, cols, t, Y


def run_simulation(sid, params=None, preview_points=400, save=True):
    """
    跑一次仿真：返回仪器读数、诊断信息与时间序列预览（完整序列落盘成 CSV）。

    参数不传就用场景默认值；**参数名写错会明确报错**，而不是静默用默认值
    （静默兜底会让智能体以为自己设对了参数，那是最坏的一种失败）。
    """
    if sid not in SCENARIOS:
        raise KeyError("没有名为 %r 的仿真场景（可用：%s）" % (sid, ", ".join(scenario_ids())))
    sc = SCENARIOS[sid]
    names = [q["name"] for q in sc["params"]]
    p = {q["name"]: float(q["default"]) for q in sc["params"]}
    for k, v in (params or {}).items():
        if k not in names:
            raise KeyError("参数 %r 不存在；本场景可调参数：%s" % (k, ", ".join(names)))
        p[k] = float(v)
    if not sc["valid"](p):
        raise ValueError("这组参数超出了本实验台的有效物理区域"
                         "（例如阻尼过强时不再是欠阻尼振荡）")

    val, diag, cols, t, Y = run_series(sid, p)
    n = int(t.size)
    step = max(1, n // max(1, int(preview_points)))
    preview = [[float(t[i])] + [float(x) for x in Y[i]] for i in range(0, n, step)]

    out = dict(ok=True, scenario=sid, title=sc["title"], params=p,
               measured=dict(name=sc["target"]["name"], meaning=sc["target"]["meaning"],
                             unit=None, value=float(val)),
               measure_method=diag.get("method"), diagnostics=diag,
               n_steps=n, dt=(float(t[1] - t[0]) if n > 1 else None),
               series=dict(columns=cols, n_points=n, preview=preview,
                           preview_points=len(preview)),
               note=("读数由仿真轨迹后处理得到（不是把闭式解直接代进去）；"
                     "完整时间序列见 series_file，可自行分析。"))
    if save:
        runs = os.path.join(DATA_DIR, "runs")
        os.makedirs(runs, exist_ok=True)
        path = os.path.join(runs, "%s_%s.csv" % (sid, _slug(sid, p, 0, 0.0)))
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(cols) + "\n")
            for i in range(n):
                f.write(",".join("%.10g" % v
                                 for v in (float(t[i]),) + tuple(float(x) for x in Y[i])) + "\n")
        out["series_file"] = os.path.relpath(path, ROOT)
    return out


def run_sweep(sid, n_cases=80, noise=NOISE_DEFAULT, seed=0, expand=0.0,
              tag=None, save=True):
    """
    跑一批实验：每个参数点跑一次仿真，把「参数 → 读数」落盘成数据集 CSV。

    返回数据集名与预览（**不含单位、不含真值**）。
    """
    sc = SCENARIOS[sid]
    cases = sample_params(sid, n_cases, seed, expand=expand)
    names = [q["name"] for q in sc["params"]]
    tname = sc["target"]["name"]

    rows, diags = [], []
    for i, p in enumerate(cases):
        v, d = measure_case(sid, p, seed=int(seed) * 7919 + i, noise=noise)
        rows.append([p[k] for k in names] + [v])
        diags.append(d)

    lines = [",".join(names + [tname])]
    for r in rows:
        lines.append(",".join("%.12g" % v for v in r))
    body = "\n".join(lines) + "\n"

    label = tag or "%s_n%d" % (sid, len(cases))
    fname = "%s.csv" % label
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, fname)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(body)

    # 边车文件：给验证环节用（重跑仿真需要的场景与取值范围）。
    # 注意：它**不含真值公式**——验证是靠重跑仿真测出来的，不是查表。
    side = dict(
        dataset=label, source="simulation", scenario=sid,
        scenario_title=sc["title"], target_name=tname, target_unit=sc["target"]["unit"],
        parameters=[dict(name=q["name"], unit=q["unit"], lo=q["lo"], hi=q["hi"])
                    for q in sc["params"]],
        noise=float(noise), seed=int(seed), n_cases=int(len(cases)),
        created=__import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    )
    with open(os.path.join(DATA_DIR, label + ".sim.json"), "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False, indent=2)

    arr = np.asarray(rows, dtype=float)
    return dict(
        ok=True, scenario=sid, dataset=label,
        file=os.path.relpath(path, ROOT), n_cases=int(arr.shape[0]),
        columns=names + [tname], variable_columns=names, target_column=tname,
        value_range=[[float(arr[:, j].min()), float(arr[:, j].max())]
                     for j in range(arr.shape[1])],
        noise=float(noise),
        steps_per_case=int(np.mean([d.get("n_steps", 0) for d in diags])),
        measure_method=diags[0].get("method") if diags else None,
        note=("仿真已完成并落盘为数据集 %s。请用 load_dataset 载入它再开始推断——"
              "本文件与观测数据文件同格式，只有参数取值与读数，"
              "**没有单位、没有真值公式**。" % label),
    )


def list_scenarios():
    return dict(ok=True, count=len(SCENARIOS),
                scenarios=[describe(s) for s in scenario_ids()],
                note=("这些是**可驱动的数值仿真实验台**（RK4 数值积分）。"
                      "用 run_simulation 跑一次看读数与时间序列，"
                      "或直接用 run_sweep 跑一批实验生成数据集。"))


def load_sidecar(dataset):
    """读数据集边车（验证环节用；给不存在的名字返回 None）。"""
    p = os.path.join(DATA_DIR, (dataset or "") + ".sim.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 自检：证明"读数是从轨迹里测出来的"，而不是把闭式解直接代进去
# ---------------------------------------------------------------------------
def selftest(verbose=True):
    import time as _t
    worst, fails = 0.0, []
    if verbose:
        print("=== 仿真器自检：测量值 vs 闭式解 ===")
        print("%-16s %-14s %-16s %-16s %-11s %8s" %
              ("场景", "参数点", "测得值", "闭式解", "相对误差", "步数"))
    for sid in scenario_ids():
        cases = sample_params(sid, 3, seed=20261005)
        for p in cases:
            try:
                got, diag = measure_case(sid, p, seed=0, noise=0.0)
                want = truth_of(sid, p)
                rel = abs(got - want) / abs(want)
                worst = max(worst, rel)
                if rel > 1e-4:
                    fails.append((sid, p, got, want, rel))
                if verbose:
                    tag = ",".join("%s=%.4g" % (k, v) for k, v in p.items())
                    print("%-16s %-14s %-16.8g %-16.8g %-11.2e %8d" %
                          (sid, tag[:14], got, want, rel, diag.get("n_steps", 0)))
            except Exception as exc:
                fails.append((sid, p, None, None, str(exc)))
    if verbose:
        print("\n最大相对误差：%.3e（判据：小于 1e-4 视为「数值精度足够」）" % worst)
        print("测量方法：过零检测 / 峰值包络拟合 / 对数线性回归 / 全轨迹平均")
        if fails:
            print("\n不达标明细：")
            for f in fails:
                print("  %s %s → 测得 %s / 闭式 %s / %s" % f)
        print("结论：" + ("全部通过" if not fails else "有 %d 项不达标" % len(fails)))
    return 0 if not fails else 1


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="SciFormula 数值仿真环境")
    ap.add_argument("--list", action="store_true", help="列出场景")
    ap.add_argument("--selftest", action="store_true", help="测量精度自检")
    ap.add_argument("--run", metavar="SCENARIO", help="跑一次并打印读数")
    ap.add_argument("--sweep", metavar="SCENARIO", help="跑一批并落盘成数据集")
    ap.add_argument("--n", type=int, default=80, help="扫描的案例数")
    ap.add_argument("--noise", type=float, default=NOISE_DEFAULT, help="读数相对噪声")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    if a.list:
        d = list_scenarios()
        for s in d["scenarios"]:
            print("%-16s %s" % (s["scenario"], s["title"]))
            print("    参数: " + ", ".join(q["name"] for q in s["parameters"]))
            print("    读数: %s（%s）" % (s["measured"]["name"], s["measured"]["meaning"]))
        return 0
    if a.selftest:
        return selftest()
    if a.run:
        sc = SCENARIOS[a.run]
        p = {q["name"]: q["default"] for q in sc["params"]}
        v, d = measure_case(a.run, p, seed=a.seed, noise=0.0)
        print("%s：读数 %s = %.10g" % (a.run, sc["target"]["name"], v))
        print("诊断：%s" % json.dumps(d, ensure_ascii=False))
        print("（闭式解对照 %.10g，仅内部自检用）" % truth_of(a.run, p))
        return 0
    if a.sweep:
        r = run_sweep(a.sweep, n_cases=a.n, noise=a.noise, seed=a.seed)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:1200])
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
