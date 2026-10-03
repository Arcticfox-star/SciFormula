# -*- coding: utf-8 -*-
"""
equivalence.py —— 判定"回归出的公式"和"真值公式"是不是同一个东西

【这个文件负责什么】
回归给出一个公式字符串，比如 0.5*(m*v**(2))。真值是 m*v**2/2。
这两者其实是一回事，但我们得让程序自动判断出来，而且要判得可靠。

【怎么判：在定义域内随机撒点，逐点比对】
这不是形式化证明，是数值等价判定：
    1. 在一批测试点上同时算出预测值和真值
    2. 逐点算相对误差
    3. 看误差有多小，以及"预测值 / 真值"这个比值是不是一个常数

比值的那个标准差非常有用：
    - 比值恒等于 1  →  两个式子完全一样        → exact
    - 比值恒定但 ≠ 1 →  两者只差一个常数因子   → up_to_constant
       （物理上这往往是"系数没归一化"，比如算出 2*m*v²/2 却写成 1*m*v²）
    - 比值忽大忽小   →  结构就不一样           → wrong

【必须如实声明的能力边界】
这是**数值等价**，不是**形式化证明**。它等价于"在测试点的覆盖范围内两个式子一致"，
覆盖不到的地方它给不出保证。本项目把它如实写进限制说明里，不装作是数学证明。
（顺带说一句：想做成真正的形式化证明，需要用 Lean 这类证明助手——
那是另一条技术路线，我们已经评估过并主动放弃了。）
"""

import numpy as np

from features import _NUMPY_ENV


def evaluate(expr_str, X, varnames):
    """
    在数据 X 上求一个表达式字符串的值，返回 ndarray。

    这里用 np.errstate 关掉了浮点警告。原因很实在：
    表达式里难免出现 sqrt(负数)、除零这类情况（比如外推时某个变量超出物理合理范围），
    而 numpy 发警告的过程本身需要 __import__，跟下面受限的命名空间冲突，会直接报错。
    反正我们在 classify 里会自己过滤掉非有限值，不如在这里把警告静默掉。
    """
    env = dict(_NUMPY_ENV)
    env.update(dict(zip(varnames, X.T)))
    with np.errstate(all="ignore"):
        val = eval(expr_str, {"__builtins__": {}}, env)
    val = np.asarray(val, dtype=float)
    if val.ndim == 0:
        val = np.full(X.shape[0], float(val))
    return val


def build_expr_string(coef, intercept, names, tol_ratio=1e-4):
    """
    把回归系数拼成一个可求值的表达式字符串（全精度，不用四舍五入的结果）。
    这一串同时用于数值判定和写进报告，保证"报告里的公式"和"实际算的公式"是同一个。
    """
    coef = np.asarray(coef, dtype=float)
    scale = float(np.max(np.abs(coef))) if coef.size else 1.0
    scale = max(scale, 1e-30)
    parts = []
    for c, nm in zip(coef, names):
        if abs(c) < tol_ratio * scale:
            continue
        parts.append("(%r)*(%s)" % (float(c), nm))
    if abs(intercept) > 0:
        parts.append("(%r)" % float(intercept))
    return " + ".join(parts) if parts else "0"


def classify(pred_str, truth_str, X, varnames,
             tol_exact=1e-6, nmse_approx=1e-2, rel_floor=1e-3, tol_ratio=5e-2):
    """
    判定预测表达式与真值表达式的关系。

    判定思路分两层，各管一件事：
      第一层看**结构**：预测值/真值 这个比值是不是恒定的？恒定就说明形式对上了。
      第二层看**精度**：归一化均方误差（NMSE）有多大？用来区分"近似对"和"完全不对"。

    为什么不用"最大相对误差"当精度门槛：当真值本身接近 0 时，相对误差会爆炸，
    哪怕绝对误差极小也会被判失败。实测踩过这个坑，所以改用 NMSE。

    tol_ratio 是"比值是否恒定"的门槛，默认 5%。这个值改过一次，原因值得记下来：
    最初写的是 1e-6，等于要求系数误差近乎为零。但只要有噪声，回归出的系数就必然有偏差——
    实测里 1% 噪声下系数只差 0.06%（R²=0.99992），结构明明完全正确，
    却因为 1e-6 这个门槛被一律判成"近似恢复"，把"结构恢复率"严重低估了。
    改成 5% 之后，结构判定的含义恢复成它本来该有的样子：
    比值恒定 = 形式对上（哪怕倍数不是 1，例如算出 2·truth 也是 up_to_constant）。
    而真正结构不同的公式，比值会随自变量大幅波动，rcv 远大于 5%，不会被误判。

    返回 dict(
        verdict  : exact / up_to_constant / approx / wrong / error
        max_rel, med_rel, nmse : 三个精度指标
        ratio    : 预测值/真值的平均比值（结构对上时，它等于那个常数因子）
        ratio_cv : 该比值的变异系数（越接近 0 说明结构越像）
        note     : 出错时的原因
    )
    """
    try:
        p = evaluate(pred_str, X, varnames)
        t = evaluate(truth_str, X, varnames)
    except Exception as exc:                     # 表达式里出现无穷/未定义
        return dict(verdict="error", max_rel=np.inf, med_rel=np.inf, nmse=np.inf,
                    ratio=np.nan, ratio_cv=np.nan, note=str(exc))

    good = np.isfinite(p) & np.isfinite(t)
    tmax = float(np.max(np.abs(t[good]))) if np.any(good) else 0.0
    if tmax == 0 or good.sum() < 10:
        return dict(verdict="error", max_rel=np.inf, med_rel=np.inf, nmse=np.inf,
                    ratio=np.nan, ratio_cv=np.nan, note="有效测试点不足")

    # 真值接近 0 的点会让相对误差失去意义，剔掉
    keep = good & (np.abs(t) > rel_floor * tmax)
    if keep.sum() < 10:
        keep = good
    p, t = p[keep], t[keep]

    rel = np.abs(p - t) / np.abs(t)
    max_rel, med_rel = float(np.max(rel)), float(np.median(rel))

    r = p / t
    rmean = float(np.mean(r))
    rcv = float(np.std(r) / max(abs(rmean), 1e-300))

    den = float(np.sum(t ** 2))
    nm = float(np.sum((p - t) ** 2) / den) if den > 0 else np.inf

    if rcv < tol_ratio:                          # 比值恒定 → 结构对上（只差一个常数因子）
        verdict = "exact" if abs(rmean - 1.0) < tol_exact else "up_to_constant"
    elif nm < nmse_approx:                       # 结构不完全一致，但拟合得很准
        verdict = "approx"
    else:
        verdict = "wrong"

    return dict(verdict=verdict, max_rel=max_rel, med_rel=med_rel, nmse=nm,
                ratio=rmean, ratio_cv=rcv, note="")


# 判定结果的显示名与是否算"恢复成功"
VERDICT_LABEL = {
    "exact": "完全恢复",
    "up_to_constant": "差一个常数因子",
    "approx": "近似恢复",
    "wrong": "错误",
    "error": "无法判定",
}


def is_recovered(verdict, allow_constant=True):
    """判定为"成功恢复"的集合——这是算恢复率时的口径"""
    ok = {"exact"}
    if allow_constant:
        ok.add("up_to_constant")
    return verdict in ok


def nmse(pred_str, truth_str, X, varnames):
    """归一化均方误差：残差平方和 / 真值平方和。越小越好（0 是完美）。"""
    try:
        p = evaluate(pred_str, X, varnames)
        t = evaluate(truth_str, X, varnames)
    except Exception:
        return np.inf
    good = np.isfinite(p) & np.isfinite(t)
    if not np.any(good):
        return np.inf
    p, t = p[good], t[good]
    den = float(np.sum(t ** 2))
    if den <= 0:
        return np.inf
    return float(np.sum((p - t) ** 2) / den)


def r2(pred_str, truth_str, X, varnames):
    """决定系数，用来衡量"能解释多少"（外推检验里尤其有用）"""
    try:
        p = evaluate(pred_str, X, varnames)
        t = evaluate(truth_str, X, varnames)
    except Exception:
        return -np.inf
    good = np.isfinite(p) & np.isfinite(t)
    if not np.any(good):
        return -np.inf
    p, t = p[good], t[good]
    tss = float(np.sum((t - t.mean()) ** 2))
    if tss <= 0:
        return -np.inf
    return float(1.0 - np.sum((p - t) ** 2) / tss)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    rng = np.random.default_rng(0)
    X = np.column_stack([rng.uniform(1, 5, 200), rng.uniform(1, 5, 200)])
    names = ["m", "v"]

    print("=== 判定逻辑自测（真值 m*v**2/2）===")
    cases = [
        ("0.5*(m*v**(2))",        "完全相同"),
        ("1.0*(m*v**(2))",        "只差常数因子 2"),
        ("0.5001*(m*v**(2))",     "系数差 0.02%（结构仍对）"),
        ("0.5*(m*v**(2)) + 0.4",  "多了个很小的偏移"),
        ("0.5*(m*(v**(2)))",      "写法不同但等价"),
        ("0.5*(m*v)",             "结构错（少了一次方）"),
        ("m*v**(2)/2 + 3*m/v",    "多了一个错误的项"),
    ]
    for expr, note in cases:
        r = classify(expr, "m*v**2/2", X, names)
        print("  %-24s → %-14s 比值 %.6f  NMSE %-10.3g  (%s)"
              % (expr, VERDICT_LABEL[r["verdict"]], r["ratio"], r["nmse"], note))

    print("\n=== 判定逻辑自测（真值 sqrt(ell/g)，注意分数次幂）===")
    X2 = np.column_stack([rng.uniform(0.1, 5, 200), rng.uniform(1, 20, 200)])
    names2 = ["ell", "g"]
    for expr in ["sqrt(ell/g)", "sqrt(Abs(ell))*Abs(g)**(-1/2)", "sqrt(ell)/g"]:
        r = classify(expr, "sqrt(ell/g)", X2, names2)
        print("  %-34s → %-12s 最大相对误差 %.3g"
              % (expr, VERDICT_LABEL[r["verdict"]], r["max_rel"]))
