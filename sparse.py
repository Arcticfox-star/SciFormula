# -*- coding: utf-8 -*-
"""
sparse.py —— 稀疏回归：从候选项里挑出少数几个

【这个文件负责什么】
features.py 已经准备好一大堆候选项（并且用量纲筛过一遍）。
现在要做的是：从这些候选项里**挑出少数几个**，配上系数，让它们的和尽量逼近观测数据。

这里放了两种策略，而且**我们把两种都真的跑了**——这是本项目一次真实的踩坑与修正：

  ── 策略一：STLSQ（序贯阈值最小二乘）──
     先用全部候选项做最小二乘，把系数绝对值小于阈值的项强行置零，再用剩下的重做一次，
     反复迭代。它是最经典的做法，在候选项彼此独立时表现很好。

  ── 策略二：OMP（正交匹配追踪，贪心前向选择）──
     一个一个地挑：每一轮选出"最能解释当前残差"的那一项，把它固定下来，
     再对残差重复。挑几项停下？由留出验证集决定。

【为什么最后改用 OMP —— 实测的失败记录】
STLSQ 在本题上**失效了**，而且失效得很隐蔽：
    当很多候选项是同一个变量的函数时（例如目标无量纲、所有候选都是 t/tau 的函数），
    它们之间高度共线，最小二乘的解会膨胀到 1e8 量级。
    结果就是"系数阈值"完全失去意义——阈值从 0.005 一路调到 0.35，
    选中的项数一个都没变，全是 59 项，训练集被完美插值、验证集上彻底崩掉。
    雪上加霜的是，我们最初还用 BIC 在训练集上选阈值，而残差能被压到 0 时
    BIC 会单调下降，于是它必然选最稠密的解——与"稀疏"的目标正好相反。

改用 OMP 后就稳了：它不看系数大小，只看"加进这一项能不能真的降低残差"，
天生对共线不敏感；再加上用留出验证集决定项数，就不会为了拟合训练数据而堆项。

这段经历我们在报告里如实写了，没有藏起来。它同时说明了本项目的验证流程
确实能发现问题——这正是"验证严谨性"想要展示的东西。
"""

import os

import numpy as np

# 无截距 OMP 的排名判据，用环境变量切换，便于做 A/B 实测：
#     不设（默认）         用"去均值后的相关系数"排名（A/B 实测胜出的一方）
#     SCIFORMULA_OMP_CENTERED=0  退回教科书式 |Anᵀr|（列按范数归一化）
# A/B 实测结论（2026-10-03，Feynman 官方 100 题全量基准，三档噪声）：
#     centered=True  通过双重检验 53 / 53 / 56
#     centered=False 通过双重检验 48 / 47 / 47
# 选定哪种是拿全量基准比出来的，不靠单题直觉——两种判据在不同题目上各有所长。
_OMP_CENTERED = os.environ.get("SCIFORMULA_OMP_CENTERED", "1").strip() in ("1", "true", "yes")



# ===========================================================================
# 策略一：STLSQ
# ===========================================================================
def stlsq_core(An, yn, threshold, max_iter=15):
    """在标准化后的特征矩阵上跑 STLSQ。保留下来用于和 OMP 做对照。"""
    p = An.shape[1]
    w = np.zeros(p)
    active = np.ones(p, dtype=bool)
    for _ in range(max_iter):
        if not active.any():
            break
        w_new = np.zeros(p)
        w_new[active] = np.linalg.lstsq(An[:, active], yn, rcond=None)[0]
        new_active = np.abs(w_new) > threshold
        w = w_new
        if np.array_equal(new_active, active):
            break
        active = new_active
    return w, active


# ===========================================================================
# 策略二：OMP（本项目实际采用的策略）
# ===========================================================================
def omp_path(An, yn, max_terms):
    """
    正交匹配追踪：贪心地一项一项选。
    每一轮选"与当前残差相关性最强"的那一列（已经选过的除外），
    然后用已选列整体做一次最小二乘、更新残差，继续下一轮。

    返回一条"路径"：每加入一项就记录一次当前的选择集、系数和残差平方和。
    这条路径让"到底该选几项"变成一个可以拿到验证集上去比的问题。
    """
    n, p = An.shape
    residual = yn.copy()
    selected = []
    path = []
    for _ in range(min(max_terms, p)):
        c = np.abs(An.T @ residual)
        c[selected] = -1.0                 # 已经选过的排除掉
        j = int(np.argmax(c))
        if c[j] <= 0:
            break
        selected.append(j)
        w = np.linalg.lstsq(An[:, selected], yn, rcond=None)[0]
        residual = yn - An[:, selected] @ w
        path.append(dict(k=len(selected), idx=list(selected),
                         coef=w.copy(), rss=float(np.sum(residual ** 2))))
    return path


def _column_norm(A):
    """
    只按**列范数**归一化（不减均值）。

    保留下来是因为它说明了"为什么不能什么都不做"：各列的**均值**会主导内积，
    贪心判据 |Aᵀr| 会退化成"谁的均值更接近残差均值"。
    （现已被 omp_path_raw 取代，不再直接用于拟合。）
    """
    nrm = np.sqrt(np.sum(A ** 2, axis=0))
    nrm = np.where(nrm < 1e-300, 1e-300, nrm)
    return nrm, A / nrm


def omp_path_raw(A, y, max_terms, centered=None):
    """
    **无截距**情形的 OMP：模型是 y = A·c，不允许常数项。

    这里有两个版本，是实测 A/B 之后才定下来的，把过程写清楚：

    ── 版本一（centered=False）：教科书式 OMP ──
       先把每列按**列范数**归一化，再用 `|Anᵀr|` 当贪心判据。
       因为列是单位长度，`|Anᵀr|` 就是 r 在该列上的投影长度，
       这正是"无截距最小二乘"的贪心准则。

    ── 版本二（centered=True，当前默认）：用"去均值后的相关系数"当判据 ──

    选择过程（两次结论翻转，如实记录）：

    ① 单题测试曾判版本二"更差"：在 Feynman I.12.11 上，真值两项都在候选库里，
       版本二却选出了 {B*q*v*sin(theta), Ef*q*tanh(theta)}（k=2 残差 15.6），
       而正确的两项 {Ef*q, B*q*v*sin(theta)} 残差是 0。
       原因是**排名和拟合用了不同的度量**：拟合是无截距的（不中心化），
       排名却中心化了。具体到那一步：第一项拟合出的系数不是 1，
       残差里混进了另一列的成分，此时"中心化相关"给 Ef*q*tanh(theta) 打了 627 分、
       给真正该选的 Ef*q 打了 625 分 —— 差 0.3%，输了。
       当时据此把默认定为版本一。

    ② 全量基准 A/B（2026-10-03，官方 Feynman 100 题 × 三档噪声）翻转了结论：
           版本二（centered） 通过双重检验 53 / 53 / 56
           版本一（plain）    通过双重检验 48 / 47 / 47
       单题输赢不等于全量输赢——版本一在 I.12.11 这类题上更稳，
       但在更多题上被"各列均值很大"带偏；版本二全量净胜 5~9 题。

    结论：**默认用版本二（centered），环境变量 SCIFORMULA_OMP_CENTERED=0 可退回版本一**。
    教训也如实写在这里：启发式的取舍只能靠全量基准说话，单题直觉会骗人；
    且排名度量与拟合度量不一致时，边界情况下会失效（I.12.11 就是代价）。
    """
    if centered is None:
        centered = _OMP_CENTERED
    n, p = A.shape
    if centered:
        Ac = A - A.mean(axis=0)
        sd = A.std(axis=0)
        sd = np.where(sd < 1e-12, 1e-12, sd)
        Rn = Ac / sd
    else:
        nrm = np.sqrt(np.sum(A ** 2, axis=0))
        nrm = np.where(nrm < 1e-300, 1e-300, nrm)
        Rn = A / nrm

    residual = y.copy()
    selected = []
    path = []
    for _ in range(min(int(max_terms), p)):
        rc = (residual - residual.mean()) if centered else residual
        c = np.abs(Rn.T @ rc)
        c[selected] = -1.0                       # 已选过的排除
        j = int(np.argmax(c))
        if c[j] <= 0:
            break
        selected.append(j)
        coef = np.linalg.lstsq(A[:, selected], y, rcond=None)[0]   # 无截距最小二乘
        residual = y - A[:, selected] @ coef
        full = np.zeros(p)
        full[selected] = coef
        path.append(dict(k=len(selected), idx=list(selected),
                         coef=full.copy(), rss=float(np.sum(residual ** 2))))
    return path


def _standardize(A):
    """每列减均值、除标准差。标准差近零的列会被保护性放大，避免除零。"""
    mu = A.mean(axis=0)
    sd = A.std(axis=0)
    sd = np.where(sd < 1e-12, 1e-12, sd)
    return mu, sd, (A - mu) / sd


def fit_terms(A, y, k, allow_intercept=True):
    """
    在数据 (A, y) 上用 OMP 选 k 项并拟合。
    返回 dict(coef, intercept, active, k, rss, r2) —— 系数是**原始尺度**的，
    所以可以直接拿去对别的、同样列顺序的矩阵做预测。

    allow_intercept=False 时**强制截距为 0**，模型退化成 y = A @ coef。
    为什么要这个开关：截距是一个"自由参数"，它不经过候选库、也就没经过量纲检查。
    目标是带量纲的量（例如能量 J）时，`0.5*m*v**2 + 常数` 里的常数没有任何物理含义
    ——量纲上不自洽的东西本来就不该出现。所以规则是：
        **目标量有无量纲 → 允许截距；目标量有量纲 → 强制截距为 0。**
    这和候选库的量纲剪枝是同一条原则，只是补上了原先漏掉的一个口子。
    """
    n, p = A.shape
    ybar = float(y.mean())
    if p == 0 or k <= 0:
        rss = float(np.sum((y - (ybar if allow_intercept else 0.0)) ** 2))
        return dict(coef=np.zeros(p), intercept=(ybar if allow_intercept else 0.0),
                    active=np.zeros(p, bool), k=0, rss=rss, r2=0.0)

    if allow_intercept:
        mu, sc, An = _standardize(A)
        path = omp_path(An, y - ybar, k)
        if not path:
            rss = float(np.sum((y - ybar) ** 2))
            return dict(coef=np.zeros(p), intercept=ybar,
                        active=np.zeros(p, bool), k=0, rss=rss, r2=0.0)
        step = path[-1]
        coef = np.zeros(p)
        coef[step["idx"]] = step["coef"] / sc[step["idx"]]
        intercept = ybar - float(np.dot(coef, mu))
    else:
        # 无截距：排名与拟合分开做，细节见 omp_path_raw 的说明
        path = omp_path_raw(A, y, k)
        if not path:
            rss = float(np.sum(y ** 2))
            return dict(coef=np.zeros(p), intercept=0.0,
                        active=np.zeros(p, bool), k=0, rss=rss, r2=0.0)
        step = path[-1]
        coef = step["coef"].copy()          # 已经是原始尺度
        intercept = 0.0

    pred = A @ coef + intercept
    rss = float(np.sum((y - pred) ** 2))
    tss = float(np.sum((y - ybar) ** 2))
    r2 = 1.0 - rss / tss if tss > 0 else 0.0
    active = np.zeros(p, bool)
    active[step["idx"]] = True
    return dict(coef=coef, intercept=intercept, active=active,
                k=int(step["k"]), rss=rss, r2=r2)


def fit_stlsq(A, y, threshold, max_iter=15, allow_intercept=True):
    """用 STLSQ 拟合一次（留着做对照，不是主路线）。allow_intercept 语义同 fit_terms。"""
    n, p = A.shape
    ybar = float(y.mean())
    if p == 0:
        rss = float(np.sum((y - ybar) ** 2))
        return dict(coef=np.zeros(0), intercept=ybar,
                    active=np.zeros(0, bool), k=0, rss=rss, r2=0.0)
    if allow_intercept:
        mu, sc, An = _standardize(A)
        yn = y - ybar
    else:
        # 无截距：用"中心化+标准化"只是为了让系数阈值有意义（挑列），
        # 挑完之后再按无截距最小二乘重新拟合所选列——模型该守的规则不能破。
        mu, sc, An = _standardize(A)
        yn = y - ybar
    w, active = stlsq_core(An, yn, threshold, max_iter)
    if allow_intercept:
        coef = w / sc
        intercept = ybar - float(np.dot(coef, mu))
    else:
        idx = np.where(active)[0]
        coef = np.zeros(p)
        if len(idx):
            coef[idx] = np.linalg.lstsq(A[:, idx], y, rcond=None)[0]
        intercept = 0.0
    pred = A @ coef + intercept
    rss = float(np.sum((y - pred) ** 2))
    tss = float(np.sum((y - ybar) ** 2))
    r2 = 1.0 - rss / tss if tss > 0 else 0.0
    return dict(coef=coef, intercept=intercept, active=active,
                k=int(active.sum()), rss=rss, r2=r2)


# ===========================================================================
# 选几项？由留出验证集决定
# ===========================================================================
def fit_best(A, y, max_terms=10, val_frac=0.3, seed=0, rss_tol=0.01,
             allow_intercept=True):
    """
    用**留出验证集**决定该选几项，然后用这个项数在**全量数据**上重新拟合。

    流程：
      1. 把数据按 7:3 切成训练集 / 验证集
      2. 对 k = 1, 2, ..., max_terms：在训练集上选 k 项，到没参与拟合的验证集上算残差
      3. 取验证残差最小的 k
      4. 在"验证残差不比最优差多少"的窄带内（默认 1%），选**项数最少**的那个
         —— 经典的一倍标准误思路：统计上区分不出来的解里，挑最简单的
      5. 用选中的 k 在全量数据上重新拟合，作为最终结果

    第 4 步很关键：噪声数据上"1 项"和"3 项"的验证残差往往差不多，
    这时候必须果断选 1 项。

    allow_intercept=False 时全程不让截距参与（含验证集残差的计算），
    见 fit_terms 的说明——截距是唯一绕过量纲检查的自由参数。

    返回 dict(..., n_terms=选中的项数, val_rss=..., all_trials=[...])
    """
    n, p = A.shape
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    nv = int(round(val_frac * n))
    vi, ti = idx[:nv], idx[nv:]
    if len(ti) < 10 or len(vi) < 10:            # 样本太少就不切，退化成全量
        ti, vi = idx, idx

    tss_v = float(np.sum((y[vi] - y[vi].mean()) ** 2))
    trials = []
    for k in range(1, min(max_terms, p) + 1):
        r = fit_terms(A[ti], y[ti], k, allow_intercept=allow_intercept)
        pred_v = A[vi] @ r["coef"] + r["intercept"]
        vrss = float(np.sum((y[vi] - pred_v) ** 2))
        trials.append(dict(k=k, val_rss=vrss, train_rss=r["rss"],
                           val_r2=(1.0 - vrss / tss_v) if tss_v > 0 else 0.0))
    if not trials:
        r = fit_terms(A, y, 1, allow_intercept=allow_intercept)
        r.update(n_terms=1, val_rss=np.nan, all_trials=[])
        return r

    best_v = min(t["val_rss"] for t in trials)
    # 窄带的绝对底线：残差已经小到"数值上等于完美"的解，一律视为并列。
    # 没有这条线会出问题——无噪声时各个 k 的验证残差都在 1e-32 这种量级，
    # 纯按相对比例比较，反而会把最稀疏的那个解排除在外（实测踩过）。
    floor = 1e-10 * tss_v if tss_v > 0 else 0.0
    limit = max(best_v * (1.0 + rss_tol), floor)
    band = [t for t in trials if t["val_rss"] <= limit]
    pick = min(band, key=lambda t: (t["k"], t["val_rss"]))
    k = pick["k"]

    r = fit_terms(A, y, k, allow_intercept=allow_intercept)   # 全量数据上重新拟合
    r["n_terms"] = k
    r["val_rss"] = pick["val_rss"]
    r["val_r2"] = pick["val_r2"]
    r["all_trials"] = trials
    return r


def to_expression(coef, intercept, names, sig=4, y_ref=None):
    """
    把回归结果拼成人能读的公式字符串。
    系数很小（相对最大系数不到 1e-4）的项不写。
    y_ref 传入目标量的典型量级；只有截距相对它不可忽略时才把截距写进公式，
    这样输出的公式干净，但截距本身仍会被单独记录，方便核对。
    """
    scale = max(float(np.max(np.abs(coef))), 1e-30) if len(coef) else 1.0
    parts = []
    for c, nm in sorted(zip(coef, names), key=lambda z: -abs(z[0])):
        if abs(c) < 1e-4 * scale:
            continue
        cs = ("%%.%dg" % sig) % c
        parts.append(cs if str(nm) == "1" else "%s*(%s)" % (cs, nm))
    if y_ref is not None and abs(intercept) > 1e-3 * abs(y_ref):
        parts.append(("%%.%dg" % sig) % intercept)
    return " + ".join(parts) if parts else "0"


# ===========================================================================
# 自检 + 两种策略的对比（这段对比结果可以直接写进报告）
# ===========================================================================
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import equations as EQ
    from dims import parse_unit
    from features import build_library, make_matrix
    from equivalence import evaluate

    def prep(eq_id, noise):
        eq = EQ.get(eq_id)
        names = EQ.varnames(eq)
        dims = [parse_unit(eq["vars"][k]) for k in names]
        X = np.random.default_rng(0).uniform  # placeholder, replaced below
        rng = np.random.default_rng(0)
        cols = []
        for nm in names:
            spec = eq["ranges"][nm]
            mode = spec[2] if len(spec) > 2 else "linear"
            if mode == "log":
                cols.append(np.exp(rng.uniform(np.log(spec[0]), np.log(spec[1]), 100)))
            else:
                cols.append(rng.uniform(spec[0], spec[1], 100))
        X = np.column_stack(cols)
        truth = evaluate(eq["expr"], X, names)
        y = truth if noise <= 0 else truth + noise * truth.std() * rng.standard_normal(100)
        kept, st = build_library(names, dims, parse_unit(eq["target_unit"]))
        A, used = make_matrix(kept, X, names)
        return eq, names, X, y, A, used

    print("=== 两种稀疏化策略对比（同一批数据、同一批候选项）===")
    print("说明：STLSQ 用的是它最好的表现——阈值从 0.005 扫到 0.35，"
          "按验证集挑最好的一档；OMP 由验证集决定项数。\n")
    print("%-6s %-6s | %-28s | %-28s" % ("题目", "噪声", "STLSQ", "OMP"))
    print("-" * 82)
    for eq_id in ["P03", "P11", "P15", "P20", "P21"]:
        for noise in [0.0, 0.01]:
            eq, names, X, y, A, used = prep(eq_id, noise)
            n = A.shape[0]
            rng = np.random.default_rng(0)
            idx = rng.permutation(n)
            vi, ti = idx[:30], idx[30:]

            best = None
            for th in np.arange(0.005, 0.36, 0.005):
                r = fit_stlsq(A[ti], y[ti], float(th))
                pv = A[vi] @ r["coef"] + r["intercept"]
                v = float(np.sum((y[vi] - pv) ** 2))
                if best is None or v < best[0]:
                    best = (v, r["k"])
            s_txt = "项数 %-3d 验证残差 %.4g" % (best[1], best[0])

            r = fit_best(A, y)
            o_txt = "项数 %-3d 验证残差 %.4g" % (r["n_terms"], r["val_rss"])
            print("%-6s %-6s | %-28s | %-28s"
                  % (eq_id, ("0%%" if noise == 0 else "1%%"), s_txt, o_txt))

    print("\n=== 逐条看回归结果（OMP，无噪声）===")
    for eq_id in ["P03", "P11", "P15", "P20", "P21"]:
        eq, names, X, y, A, used = prep(eq_id, 0.0)
        r = fit_best(A, y)
        expr = to_expression(r["coef"], r["intercept"], [t.expr for t in used],
                             y_ref=float(np.mean(np.abs(y))))
        print("  %-5s 真值 %-26s → 选出 %d 项：%s"
              % (eq_id, eq["expr"], r["n_terms"], expr))
