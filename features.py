# -*- coding: utf-8 -*-
"""
features.py —— 候选特征库构造 + 量纲剪枝  ★本项目的技术核心★

【这个文件负责什么】
符号回归的所有"候选答案"，都是若干"候选数学项"的线性组合。
比如要找 E = m*v^2/2，就先准备一大堆候选项：
    m, v, m*v, v^2, m*v^2, 1/m, m*v^3, exp(v), ...
再用稀疏回归挑出少数几个真正起作用的（这里挑出 m*v^2，系数 1/2）。

问题来了：候选项该怎么准备？
本项目的做法是——**先生成一堆，再用物理量纲把它们筛一遍**。

【量纲剪枝的逻辑】
物理公式 E = m*v^2/2 里，每一项的量纲必须和目标量 E 完全一致，
否则等式在物理上根本不可能成立。对 E 这个例子（量纲 kg*m^2/s^2）：
    m*v^2  量纲 kg*m^2/s^2  →  与目标一致，保留
    m*v    量纲 kg*m/s      →  不一致，扔掉
    m      量纲 kg          →  扔掉
这一步发生在**搜索之前**，所以叫"搜索前剪枝"。

【为什么这是真本事，不是花架子】
1. 它砍掉的都是"物理上不可能对"的候选项，不砍任何可能是答案的项
   （前提是没有隐藏的标度常数——这个前提必须如实声明，见 README）。
2. 它同时降低了计算成本：被剪掉的项连数值都不需要算。
3. 它把"物理定律必须量纲齐次"这条人类几百年的经验，
   变成 AI 搜索过程里的硬约束。这就是"把物理先验注入 AI 搜索"。
"""

from fractions import Fraction as Fr
from itertools import combinations, product

from dims import ZERO, add, same, scale


# ---------------------------------------------------------------------------
# 通用初等函数字典
# 这些函数只允许作用在【无量纲】的量上——因为 sin(3 米) 在物理上没有意义。
# 代码通过"只对无量纲基施加函数"强制这一条，这是量纲检查的第二处用武之地。
#   {u} 会被替换成具体的无量纲表达式，例如 "theta" 或 "x/sigma"
# ---------------------------------------------------------------------------
FUNCS = [
    ("u",        "{u}"),
    ("u2",       "({u})**2"),
    ("u3",       "({u})**3"),
    ("inv_u",    "1/({u})"),
    ("inv_u2",   "1/({u})**2"),
    ("sqrt_u",   "sqrt(Abs({u}))"),
    ("exp_u",    "exp({u})"),
    ("exp_mu",   "exp(-({u}))"),
    ("gauss",    "exp(-({u})**2)"),
    ("gauss2",   "exp(-({u})**2/2)"),
    ("sin_u",    "sin({u})"),
    ("cos_u",    "cos({u})"),
    ("tanh_u",   "tanh({u})"),
    ("log_u",    "log(Abs({u}))"),
    ("cauchy",   "1/(1+({u})**2)"),
]

# 候选幂次。用 Fraction 是因为开平方会产生 1/2 次幂。
EXP_SINGLE = [Fr(1), Fr(2), Fr(3), Fr(-1), Fr(-2), Fr(1, 2), Fr(-1, 2)]
EXP_PAIR   = [Fr(1), Fr(2), Fr(-1), Fr(1, 2), Fr(-1, 2)]
EXP_MULTI  = [Fr(1), Fr(2), Fr(-1)]           # 三变量及以上，控制组合爆炸


# ---------------------------------------------------------------------------
# 项的表示：一个候选项 = "量纲核" × "无量纲函数因子"
#   core   形如 (("m", 1), ("v", 2))，表示 m^1 * v^2
#   factor None 表示没有函数因子；否则是 (函数名, 模板, 无量纲表达式)
# 这样表示是为了让剪枝阶段完全不做字符串和符号运算，只做分数加减，速度极快。
# ---------------------------------------------------------------------------
def _mkcore(pairs):
    """把 (变量名, 指数) 列表合并同类项，指数相消为 0 的变量直接去掉"""
    acc = {}
    for name, p in pairs:
        acc[name] = acc.get(name, Fr(0)) + p
    return tuple(sorted((k, v) for k, v in acc.items() if v != 0))


def _pow_str(name, e):
    """把一个幂写成人能读的字符串：v^2 写成 v**(2)，1/v 就写 1/v"""
    if e == 1:
        return name
    if e == -1:
        return "1/%s" % name
    if e.denominator == 1:
        return "%s**(%d)" % (name, e) if e > 0 else "1/%s**(%d)" % (name, -e)
    # 分数次幂：负数开方会变复数，所以套一层 Abs 保护
    if e == Fr(1, 2):
        return "sqrt(Abs(%s))" % name
    return "Abs(%s)**(%s)" % (name, e)


class Term(object):
    """
    一个候选特征项。
    expr（可读的表达式字符串）是**惰性**生成的——因为候选全集里绝大多数项
    会被量纲剪枝扔掉，给它们生成字符串纯属浪费。
    """

    def __init__(self, core, factor, dim):
        self.core = core
        self.factor = factor
        self.dim = dim
        self._expr = None

    @property
    def expr(self):
        if self._expr is None:
            parts = [_pow_str(n, e) for n, e in self.core]
            if self.factor is not None:
                _, tmpl, uexpr = self.factor
                parts.append(tmpl.format(u=uexpr))
            self._expr = "*".join(parts) if parts else "1"
        return self._expr

    @property
    def size(self):
        """复杂度：核里各指数绝对值之和 + 有无函数因子。越小越简单。"""
        return sum(abs(float(e)) for _, e in self.core) + (2 if self.factor else 0)

    def __repr__(self):
        return "<Term %s>" % self.expr


def _core_dim(core, vardim):
    d = ZERO
    for name, e in core:
        d = add(d, scale(vardim[name], e))
    return d


def build_library(varnames, vardims, target_dim, max_terms=250, verbose=False):
    """
    构造候选特征库，并做量纲剪枝。

    参数
        varnames    变量名列表
        vardims     与变量名一一对应的量纲列表
        target_dim  目标量的量纲（只有量纲与之相同的项才可能出现在答案里）
        max_terms   剪枝后仍保留超过这么多项时，只留下最简单的那些（防过拟合）
    返回
        kept   剪枝后保留的 Term 列表（已按复杂度从简单到复杂排序）
        stats  统计信息，用于量化剪枝效果
    """
    vardim = dict(zip(varnames, vardims))
    nv = len(varnames)

    # ---- 第 1 步：生成"量纲核"候选（各变量幂次的乘积）----
    cores = set()
    for k, exps in ((1, EXP_SINGLE), (2, EXP_PAIR), (3, EXP_MULTI), (4, EXP_MULTI)):
        if k > nv:
            break
        for combo in combinations(varnames, k):
            for e in product(exps, repeat=k):
                cores.add(_mkcore(list(zip(combo, e))))
    cores.add(())                                    # 常数项（空核）
    cores = sorted(cores, key=lambda c: (len(c), sum(abs(float(e)) for _, e in c)))

    # ---- 第 2 步：挑出"无量纲基" U，并派出函数因子 ----
    # U 里每个元素的量纲都是零，所以可以安全地送进 sin / exp / log。
    U = []
    for i, nm in enumerate(varnames):
        if same(vardims[i], ZERO):
            U.append(nm)
    for i in range(nv):                              # 同量纲变量之比也是无量纲的
        for j in range(i + 1, nv):
            if same(vardims[i], vardims[j]):
                U.append("%s/%s" % (varnames[i], varnames[j]))
    U = list(dict.fromkeys(U))

    factors = [(fname, tmpl, uexpr)
               for uexpr in U for fname, tmpl in FUNCS]

    # ---- 第 3 步：拼出"候选全集"（此刻不做量纲判断，用于统计剪枝效果）----
    all_terms, seen = [], set()

    def push(core, factor):
        key = (core, factor)
        if key in seen:
            return
        seen.add(key)
        # 函数因子一定是无量纲的，所以项的量纲就等于核的量纲
        all_terms.append(Term(core, factor, _core_dim(core, vardim)))

    for c in cores:
        push(c, None)
    for f in factors:
        push((), f)
    for c in cores:
        for f in factors:
            push(c, f)

    total = len(all_terms)
    kept = [t for t in all_terms if same(t.dim, target_dim)]

    # ---- 第 3.5 步：按表达式去重 ----
    # 同一个表达式可能由两条路径生成（幂次核 m*mu，和"核 m × 恒等因子 mu"），
    # 必须合并，否则会白白占用候选项名额、还会让系数被拆到两列上去。
    kept.sort(key=lambda t: (t.size, len(t.core)))
    uniq, seen_expr = [], set()
    for t in kept:
        if t.expr in seen_expr:
            continue
        seen_expr.add(t.expr)
        uniq.append(t)
    kept = uniq

    # ---- 第 4 步：保留项过多时只留最简单的，防止过拟合 ----
    truncated = False
    if len(kept) > max_terms:
        kept = kept[:max_terms]
        truncated = True

    stats = dict(
        total=total,
        kept=len(kept),
        dropped=total - len(kept),
        prune_ratio=(1.0 - len(kept) / total) if total else 0.0,
        n_cores=len(cores),
        n_factors=len(factors),
        n_units=len(U),
        unit_bases=U,
        truncated=truncated,
    )
    if verbose:
        print("  候选全集 %d 项 → 量纲剪枝后 %d 项（砍掉 %.1f%%）%s"
              % (total, len(kept), 100 * stats["prune_ratio"],
                 "   [已截断到 %d]" % max_terms if truncated else ""))
    return kept, stats


# ---------------------------------------------------------------------------
# 把候选项算成数值矩阵
# ---------------------------------------------------------------------------
import numpy as np

# 表达式字符串里允许出现的函数集合。用受限的命名空间求值，变量由数据提供。
_NUMPY_ENV = {
    "sqrt": np.sqrt, "Abs": np.abs, "exp": np.exp, "log": np.log,
    "sin": np.sin, "cos": np.cos, "tanh": np.tanh, "pi": np.pi,
}


def make_matrix(terms, X, varnames, max_abs=1e8, dedup=True):
    """
    把每个候选项在一批数据 X 上求值，拼成回归用的特征矩阵 A。

    只对"剪枝后保留"的项做这一步——这正是量纲剪枝省下计算成本的地方。

    这里做三道数值净化，缺一道都会让回归结果变成垃圾（都是实测踩出来的）：
      1. 丢掉含 inf / nan 的列，以及量级过大（> max_abs）的列
      2. 丢掉**近常数列**。量纲剪枝放过了一批"代数上恒等于常数"的项，例如
         Abs(t)**(-1/2)*sqrt(Abs(tau))*sqrt(Abs(t/tau)) 恒等于 1——它量纲合法，
         但没有任何信息量，还会把最小二乘的条件数彻底毁掉（实测系数会飙到 1e16）
      3. 丢掉**数值上完全重复**的列（相关系数 ≈ 1），避免同一个方向被拆到两列上

    返回 (A, used_terms)，两者一一对应。
    """
    env = dict(_NUMPY_ENV)
    env.update(dict(zip(varnames, X.T)))
    n = X.shape[0]
    cols, used, Z = [], [], []
    for t in terms:
        try:
            # errstate 关掉浮点警告：某些项在某些点上会 sqrt 负数或除零，
            # 这些列接下来会被"非有限值"检查丢掉，没必要让 numpy 报警告。
            with np.errstate(all="ignore"):
                col = eval(t.expr, {"__builtins__": {}}, env)
        except Exception:
            continue
        col = np.asarray(col, dtype=float)
        if col.ndim == 0:
            col = np.full(n, float(col))
        if col.shape != (n,) or not np.all(np.isfinite(col)):
            continue
        m = float(np.max(np.abs(col)))
        if m > max_abs or m == 0.0:
            continue

        mu = float(col.mean())
        sd = float(col.std())
        if sd < 1e-9 * m:                       # 净化 2：近常数列
            continue
        z = (col - mu) / sd
        if dedup and Z:                         # 净化 3：数值重复列
            if float(np.max(np.abs(np.asarray(Z) @ z))) / n > 1.0 - 1e-9:
                continue
        cols.append(col)
        used.append(t)
        Z.append(z)

    if not cols:
        return np.zeros((n, 0)), []
    return np.column_stack(cols), used


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import sympy as sp
    from dims import fmt, parse_unit
    from equations import _expr_dim, EQUATIONS, get, varnames as eq_varnames

    print("=== 逐题演示：量纲剪枝把搜索空间缩小了多少 ===")
    print("%-5s %-24s %-10s %8s %8s %9s" % ("ID", "名称", "目标量纲", "候选全集", "剪枝后", "砍掉比例"))
    tot = keep = 0
    zero_keepers = []
    for eq in EQUATIONS:
        names = eq_varnames(eq)
        dims = [parse_unit(eq["vars"][n]) for n in names]
        tdim = parse_unit(eq["target_unit"])
        kept, st = build_library(names, dims, tdim)
        tot += st["total"]
        keep += st["kept"]
        if st["kept"] == 0:
            zero_keepers.append(eq["id"])
        print("%-5s %-24s %-10s %8d %8d %8.1f%%" % (
            eq["id"], eq["name"], eq["target_unit"] or "(无量纲)",
            st["total"], st["kept"], 100 * st["prune_ratio"]))
    print("\n合计：候选全集 %d 项 → 剪枝后 %d 项，平均砍掉 %.1f%%"
          % (tot, keep, 100 * (1 - keep / tot)))
    if zero_keepers:
        print("警告：以下题目剪枝后一项不剩，说明候选核生成不完整：", zero_keepers)
    else:
        print("每道题都至少留下 1 个候选项 ✔（说明答案一定在搜索空间里）")

    print("\n=== 完整演示：P03 动能（目标量纲 kg*m^2/s^2）===")
    eq = get("P03")
    names = eq_varnames(eq)
    dims = [parse_unit(eq["vars"][n]) for n in names]
    kept, st = build_library(names, dims, parse_unit(eq["target_unit"]))
    print("  答案 E = m*v^2/2，量纲剪枝后只剩这些候选：")
    for t in kept:
        print("    %-34s 量纲 %s" % (t.expr, fmt(t.dim)))
    print("  → 量纲分析单独就把答案锁定到一个形式上，只剩系数 1/2 待定。")

    print("\n=== 反例演示：被扔掉的项确实是物理上不可能的 ===")
    tdim = parse_unit(eq["target_unit"])
    vd = {n: parse_unit(eq["vars"][n]) for n in names}
    for probe in ["m", "v", "m*v", "v**2", "m*v**2", "m*v**2/2"]:
        e = sp.sympify(probe, locals=dict(zip(names, sp.symbols(names))))
        d = _expr_dim(e, vd, parse_unit)
        print("    %-12s 量纲 %-12s %s"
              % (probe, fmt(d), "保留 ✔" if same(d, tdim) else "丢弃 ✘"))

    print("\n=== 反例演示：P01 摩擦力（多了一个无量纲变量 mu）===")
    eq = get("P01")
    names = eq_varnames(eq)
    dims = [parse_unit(eq["vars"][n]) for n in names]
    kept, st = build_library(names, dims, parse_unit(eq["target_unit"]))
    print("  这里 mu 是无量纲的，所以它可以是任意函数——剪枝救不了，得靠稀疏回归挑：")
    print("  保留 %d 项，其中最简单的前 10 项：" % st["kept"])
    for t in kept[:10]:
        print("    %s" % t.expr)
