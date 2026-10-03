# -*- coding: utf-8 -*-
"""
dims.py —— 量纲系统

【这个文件负责什么】
把"物理量的单位"变成可以计算的数字，从而能自动判断一个候选公式
在量纲上是否合法。这是本项目区别于"普通符号回归"的核心。

【基本思路】
任何物理量的量纲，都可以只用 7 个基本量纲的指数来表示：

    质量 kg | 长度 m | 时间 s | 电流 A | 温度 K | 物质量 mol | 发光强度 cd

例如"力"的单位牛顿 N = kg*m/s^2，写成 7 元组就是：
    (1, 1, -2, 0, 0, 0, 0)

这样，量纲的乘除就变成了指数的加减，开方就是指数乘 1/2。

【为什么指数要用分数 Fraction 而不是整数】
因为开平方会出现 1/2 次幂。比如单摆周期 T = sqrt(L/g)：
    L/g 的量纲是 s^2，开方后本该是 s^(2 * 1/2) = s。
如果指数只支持整数，这一步就算不出来，量纲剪枝就会把正确的项误杀。
"""

from fractions import Fraction as Fr
import re

# 7 个基本量纲的显示名称（顺序固定，所有量纲元组都按这个顺序）
BASE_SYMBOLS = ("kg", "m", "s", "A", "K", "mol", "cd")

# 量纲为零 = 无量纲（纯数、比值、角度等）
ZERO = (Fr(0),) * 7


def _mk(*xs):
    """把 7 个整数打包成量纲元组，例如 _mk(1, 1, -2) -> (1, 1, -2, 0, 0, 0, 0)"""
    xs = tuple(Fr(x) for x in xs) + (Fr(0),) * (7 - len(xs))
    return xs[:7]


# ---------------------------------------------------------------------------
# 常用单位表：单位名 -> 量纲
# 只列本项目会用到的。要加新单位，在这里补一行即可。
# ---------------------------------------------------------------------------
UNITS = {
    # 基本单位
    "kg":  _mk(1, 0, 0, 0, 0, 0, 0),
    "m":   _mk(0, 1, 0, 0, 0, 0, 0),
    "s":   _mk(0, 0, 1, 0, 0, 0, 0),
    "A":   _mk(0, 0, 0, 1, 0, 0, 0),
    "K":   _mk(0, 0, 0, 0, 1, 0, 0),
    "mol": _mk(0, 0, 0, 0, 0, 1, 0),
    "cd":  _mk(0, 0, 0, 0, 0, 0, 1),
    # 无量纲
    "rad": ZERO,
    "1":   ZERO,
    # 力学
    "N":   _mk(1, 1, -2),        # 牛顿 = kg*m/s^2
    "J":   _mk(1, 2, -2),        # 焦耳 = N*m
    "W":   _mk(1, 2, -3),        # 瓦特 = J/s
    "Pa":  _mk(1, -1, -2),       # 帕斯卡 = N/m^2
    "Hz":  _mk(0, 0, -1),        # 赫兹 = 1/s
    # 电磁
    "C":   _mk(0, 0, 1, 1),      # 库仑 = A*s
    "V":   _mk(1, 2, -3, -1),    # 伏特 = W/A
    "F":   _mk(-1, -2, 4, 2),    # 法拉 = C/V
    "ohm": _mk(1, 2, -3, -2),    # 欧姆 = V/A
    "S":   _mk(-1, -2, 3, 2),    # 西门子 = 1/ohm
    "T":   _mk(1, 0, -2, -1),    # 特斯拉 = kg/(A*s^2)
    "Wb":  _mk(1, 2, -2, -1),    # 韦伯 = V*s
    "H":   _mk(1, 2, -2, -2),    # 亨利 = Wb/A
}

_CACHE = {}


def parse_unit(s):
    """
    把单位字符串解析成量纲元组。

    支持：单位名、`*` 相乘、`/` 相除、`^` 取整数或分数幂。
    为了避免括号解析，请写成左结合形式，例如：
        "W/m/K"       而不是 "W/(m*K)"
        "kg/A/s^2"    而不是 "kg/(A*s^2)"
        "m/s^2"、"m^3"、"s^-1"、"m^(1/2)" 都可以。
    """
    s = (s or "").strip()
    if s in ("", "1", "-", "none", "None"):
        return ZERO
    if s in _CACHE:
        return _CACHE[s]

    total = ZERO
    sign = 1                      # +1 表示这一项是乘上去的，-1 表示除以
    for part in re.findall(r"[*/]|[^*/]+", s.replace(" ", "")):
        if part == "*":
            sign = 1
        elif part == "/":
            sign = -1
        else:
            name, _, exp = part.partition("^")
            exponent = Fr(exp) if exp else Fr(1)
            if name not in UNITS:
                raise KeyError("未知单位：%r（请先在 dims.py 的 UNITS 里登记）" % name)
            total = add(total, scale(UNITS[name], exponent * sign))

    _CACHE[s] = total
    return total


# ---------------------------------------------------------------------------
# 量纲的三种运算：乘（加指数）、除（减指数）、取幂（乘指数）
# ---------------------------------------------------------------------------
def add(a, b):
    """量纲相乘：指数相加"""
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    """量纲相除：指数相减"""
    return tuple(x - y for x, y in zip(a, b))


def scale(d, k):
    """量纲取 k 次幂：指数同乘 k（k 可以是分数，如 1/2 表示开方）"""
    k = Fr(k)
    return tuple(e * k for e in d)


def same(a, b):
    """两个量纲是否完全一致（剪枝的判据）"""
    return tuple(a) == tuple(b)


def fmt(d):
    """把量纲元组打印成人能读的字符串，例如 'kg*m^2/s^-3' 的反面形式 'kg*m^2/s^3'"""
    pos, neg = [], []
    for sym, e in zip(BASE_SYMBOLS, d):
        if e == 0:
            continue
        if e > 0:
            pos.append(sym if e == 1 else "%s^%s" % (sym, e))
        else:
            a = -e
            neg.append(sym if a == 1 else "%s^%s" % (sym, a))
    head = "*".join(pos) if pos else "1"
    return head if not neg else head + "/" + "/".join(neg)


def check_homogeneous_add(a, b):
    """
    量纲一致性检查：两个量能不能相加。
    物理上只有量纲相同的量才能相加（"3 米 + 2 秒"没有意义）。
    本函数在需要时用于给候选公式做合法性把关。
    """
    return same(a, b)


# ---------------------------------------------------------------------------
# 从一个符号表达式里算出量纲
# 放在 dims.py 而不是别处，是为了让「方程库」和「方程库的自检」都能用它，
# 而不产生循环依赖。
# ---------------------------------------------------------------------------

# 这些函数的参数必须是无量纲量（角度、比值、指数）
TRANSCENDENTAL = ("sin", "cos", "tan", "asin", "acos", "atan", "arcsin", "arccos",
                  "arctan", "sinh", "cosh", "tanh", "exp", "log", "ln", "Log")


def expr_dim(e, dims):
    """
    递归计算 sympy 表达式 e 的量纲。

    dims 是 {变量名: 量纲元组}。
    规则：
      乘法    -> 指数相加
      除法    -> 指数相减
      幂      -> 指数相乘（指数必须是有理数；x**y 而 y 带量纲是物理上无意义的）
      加法    -> 要求各项量纲相同，返回第一项的量纲
      三角函数/指数/对数 -> 参数必须无量纲，结果无量纲
      sqrt    -> 指数乘 1/2
    """
    import sympy as sp

    if e.is_Symbol:
        name = str(e)
        if name not in dims:
            raise KeyError("表达式里的符号 %r 不在单位表里" % name)
        return dims[name]
    if e.is_Number:
        return ZERO

    if e.func is sp.Add:
        d0 = expr_dim(e.args[0], dims)
        for a in e.args[1:]:
            if not same(expr_dim(a, dims), d0):
                raise ValueError("加法两侧量纲不一致：%s 对 %s" % (
                    fmt(expr_dim(a, dims)), fmt(d0)))
        return d0

    if e.func is sp.Mul:
        d = ZERO
        for a in e.args:
            d = add(d, expr_dim(a, dims))
        return d

    if e.func is sp.Pow:
        base = expr_dim(e.args[0], dims)
        expo = e.args[1]
        if not expo.is_Number:
            raise ValueError("指数 %s 不是常数，无法判断量纲" % expo)
        return scale(base, fraction_of(expo))

    if e.func is sp.sqrt:
        return scale(expr_dim(e.args[0], dims), Fr(1, 2))

    fname = getattr(e.func, "__name__", str(e.func))
    if fname in TRANSCENDENTAL:
        arg = expr_dim(e.args[0], dims)
        if not same(arg, ZERO):
            raise ValueError("%s() 的参数必须是无量纲量，实际是 %s" % (fname, fmt(arg)))
        return ZERO

    # 其余未登记的函数：保守地要求参数无量纲
    for a in e.args:
        if not same(expr_dim(a, dims), ZERO):
            raise ValueError("未登记的函数 %s 带了有量纲的参数" % fname)
    return ZERO


def fraction_of(x):
    """把 sympy 的数（可能是 1/2、-3 这样的有理数）转成 Python 的 Fraction"""
    import sympy as sp
    r = sp.Rational(x)
    return Fr(int(r.p), int(r.q))


def expr_dim_str(expr_str, dims):
    """便捷入口：把公式字符串 + 变量量纲表 直接算成量纲元组"""
    import sympy as sp
    names = sorted(dims)
    syms = sp.symbols(names)
    e = sp.sympify(expr_str, locals=dict(zip(names, syms)))
    return expr_dim(e, dims)



# ---------------------------------------------------------------------------
# 自检：直接运行这个文件时，会跑一遍小测试，确认量纲运算正确
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        ("N",              "kg*m/s^2"),
        ("J",              "N*m"),
        ("W",              "J/s"),
        ("V",              "W/A"),
        ("F",              "C/V"),
        ("T",              "kg/A/s^2"),
    ]
    print("=== 单位等价性自检 ===")
    ok = True
    for a, b in tests:
        da, db = parse_unit(a), parse_unit(b)
        good = same(da, db)
        ok &= good
        print("  %-4s = %-12s  %s" % (a, b, "OK" if good else "FAIL  %s vs %s" % (fmt(da), fmt(db))))

    print("\n=== 量纲运算自检 ===")
    L = parse_unit("m")
    g = parse_unit("m/s^2")
    period = scale(sub(L, g), Fr(1, 2))
    print("  sqrt(L/g) 的量纲 = %s   （期望 s）%s" % (fmt(period), "OK" if same(period, parse_unit("s")) else "FAIL"))

    print("\n=== 一致性检查自检 ===")
    print("  N 与 kg*m/s^2 可相加：", check_homogeneous_add(parse_unit("N"), parse_unit("kg*m/s^2")))
    print("  N 与 m 可相加：      ", check_homogeneous_add(parse_unit("N"), parse_unit("m")), "（期望 False）")

    print("\n总计：", "全部通过" if ok else "有失败项")
