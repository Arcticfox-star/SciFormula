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
