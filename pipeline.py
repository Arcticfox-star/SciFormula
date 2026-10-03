# -*- coding: utf-8 -*-
"""
pipeline.py —— SciFormula 的「原子能力层」

【这个文件为什么必须存在】
原来的 experiment.py 把所有步骤写死在一个函数里：
读数据 → 建候选库 → 量纲剪枝 → 稀疏回归 → 等价判定 → 外推检验，一次调用跑完 21 题。
跑实验时很方便，但它有一个致命问题：**中间没有任何一步需要"做决定"**。
用哪档噪声、建多大的库、目标单位是什么、选几项、算不算通过——全部写死在代码里。

而赛事规程 3.1 要求：
    「参赛作品必须以 AGH 作为智能体运行与执行底座，
      并经由 AGH 完成至少三个连续步骤（任务规划、能力调用、反馈处理与结果验证）。」
如果编排层没有任何要决策的东西，AGH 就只是一个"执行了一条命令"的外壳，
那三个连续步骤名存实亡，20% 的执行闭环分就无从谈起。

所以本文件把流程切成 8 个**只做一件事、输入输出都是纯数据**的能力：

    1. list_problems          列出可用的基准问题
    2. load_problem           生成一批观测数据（discovery 模式下不给真值与单位）
    3. check_units            校验物理单位字符串写得对不对
    4. build_candidates       按给定单位构造候选库 + 量纲剪枝  ★核心创新点★
    5. fit_sparse             稀疏回归，从候选里挑出少数几项
    6. verify_formula         独立验证：新采样点 + 外推区间
    7. compare_strategies     同一问题下 OMP 与 STLSQ 的对照
    8. score_prediction       与真值对比打分（仅基准模式）

编排层（AGH 智能体）要做的事就变成：
    load_problem
      → 自己读变量名与物理语境，推断每个变量的量纲、以及目标量的量纲
      → check_units 校验写法
      → build_candidates，看剪枝结果是否合理（候选剩 0 项 = 单位一定推错了）
      → fit_sparse → verify_formula
      → 不通过 → 换策略 / 改单位 / 放宽候选复杂度 → 重来
      → 输出结论 + 失败记录

【一条必须如实说明的边界】
本文件里确实保存着"真值公式与真值单位"，因为这是一套**基准评测**：
没有真值就无法计算恢复率，也就无法证明方法有效。
工具层在 discovery 模式下**不会**把它们返回给智能体（存在 <data_id>.meta.json 里，
只有 benchmark 模式下的 score_prediction 才读）。
这不是作弊，是基准评测的常规做法（SRBench 等公开基准同样如此），但必须在报告里写清楚。
"""

import os
import json
import time
import hashlib

import numpy as np

import equations as EQ
from dims import UNITS, ZERO, fmt, parse_unit, same
from features import build_library as _build_library, make_matrix
from sparse import fit_best, fit_stlsq, to_expression
from equivalence import (VERDICT_LABEL, build_expr_string, classify, evaluate,
                         is_recovered, nmse, r2)


# ---------------------------------------------------------------------------
# 运行目录：所有中间产物落在这里，方便 AGH 执行记录之外再留一份可核查的痕迹
# ---------------------------------------------------------------------------
RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agh_runs")

DISCOVERY = "discovery"      # 真实使用场景：只给观测数据，单位靠推断
BENCHMARK = "benchmark"      # 基准评测：额外保存真值，用于打分


def _ensure_dir():
    os.makedirs(RUN_DIR, exist_ok=True)


def _nz(noise):
    """把噪声水平变成文件名安全的小数写法：0 -> "0"，0.01 -> "0.01" """
    return (("%.4f" % float(noise)).rstrip("0").rstrip(".")) or "0"


# ---------------------------------------------------------------------------
# 采样与加噪（与改造前的 experiment.py 完全一致，保证结果可复现）
# ---------------------------------------------------------------------------
def _ranges_of(eq, expand=0.0):
    """按变量顺序返回 (lo, hi, sampling_mode)；expand>0 时把区间向外扩张用于外推"""
    out = {}
    for name in EQ.varnames(eq):
        spec = eq["ranges"][name]
        lo, hi = float(spec[0]), float(spec[1])
        mode = spec[2] if len(spec) > 2 else "linear"
        if expand > 0:
            if lo >= 0:
                lo, hi = lo * (1 - expand), hi * (1 + expand)
            else:
                span = hi - lo
                lo, hi = lo - expand * span, hi + expand * span
        out[name] = (lo, hi, mode)
    return out


def sample_X(eq, n, seed, expand=0.0):
    """按方程定义的区间采一批自变量（expand>0 用于外推检验）"""
    rng = np.random.default_rng(seed)
    cols = []
    for name, (lo, hi, mode) in _ranges_of(eq, expand).items():
        if mode == "log":
            cols.append(np.exp(rng.uniform(np.log(max(lo, 1e-12)), np.log(hi), n)))
        else:
            cols.append(rng.uniform(lo, hi, n))
    return np.column_stack(cols)


def add_noise(y_clean, noise, seed):
    """加高斯噪声，标准差 = noise × 真值的标准差；noise=0 原样返回"""
    if noise <= 0:
        return y_clean.copy()
    rng = np.random.default_rng(seed + 999)
    return y_clean + noise * float(np.std(y_clean)) * rng.standard_normal(len(y_clean))


# ---------------------------------------------------------------------------
# 能力 1：列出可用问题
# ---------------------------------------------------------------------------
def list_problems(library=None):
    """
    列出基准问题清单。

    参数 library：不传（默认）= 当前主库（环境变量决定的 mini）；
    传 "feynman" = 官方 Feynman 基准 100 题。两套库可在同一进程里共存。

    注意：这里**故意不返回目标量的单位**。目标单位属于"要推断的东西"，
    由智能体根据问题名称、目标量名称和变量名来判断。
    """
    if library and str(library).strip().lower() not in ("mini", ""):
        eqs = EQ.ensure_library(library)
        lib_name = "feynman" if str(library).strip().lower() in ("feynman", "official", "benchmark") else "mini"
    else:
        eqs = EQ.EQUATIONS
        lib_name = EQ.LIBRARY
    rows = []
    for eq in eqs:
        names = EQ.varnames(eq)
        rows.append(dict(
            problem_id=eq["id"],
            name=eq["name"],
            source=eq["ref"],
            target_variable=eq["target"],
            n_variables=len(names),
            variables=names,
        ))
    return dict(ok=True, library=lib_name, count=len(rows), problems=rows,
                note="选取一个 problem_id 调 load_problem 取得观测数据；"
                     "目标量的量纲未提供，需要自行推断，再用 check_units 校验。")


# ---------------------------------------------------------------------------
# 能力 2：载入/生成观测数据
# ---------------------------------------------------------------------------
def load_problem(problem_id, n_samples=100, noise=0.0, seed=0, mode=DISCOVERY):
    """
    生成一批观测数据并落盘，返回可交给智能体看的"观测信息"。

    discovery 模式：只给变量名、采样区间、观测值的统计与预览——不给真值公式、不给单位。
    benchmark 模式：额外返回真值公式与各单位，供打分使用。
    """
    eq = EQ.get(problem_id)
    names = EQ.varnames(eq)

    X = sample_X(eq, int(n_samples), int(seed), expand=0.0)
    y_clean = evaluate(eq["expr"], X, names)
    y = add_noise(y_clean, float(noise), int(seed))

    data_id = "%s_n%d_nz%s_s%d" % (problem_id, int(n_samples), _nz(noise), int(seed))
    _ensure_dir()
    np.savez(os.path.join(RUN_DIR, data_id + ".npz"), X=X, y=y)

    variables = []
    for name, (lo, hi, smode) in _ranges_of(eq, 0.0).items():
        variables.append(dict(name=name, sampled_range=[lo, hi], sampling=smode))

    meta = dict(
        data_id=data_id, problem_id=problem_id, name=eq["name"], ref=eq["ref"],
        target_variable=eq["target"], n_samples=int(n_samples),
        noise=float(noise), seed=int(seed), mode=mode,
        variables=variables,
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
        reference=dict(expr=eq["expr"], target_unit=eq["target_unit"],
                       variables_units={k: eq["vars"][k] for k in names}),
    )
    with open(os.path.join(RUN_DIR, data_id + ".meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    k = min(5, len(y))
    out = dict(
        ok=True, data_id=data_id, mode=mode,
        problem=dict(problem_id=problem_id, name=eq["name"], source=eq["ref"]),
        target=dict(name=eq["target"], unit=None if mode == DISCOVERY else eq["target_unit"]),
        variables=variables,
        observations=dict(
            n=int(n_samples),
            noise_level=float(noise),
            y_mean=float(np.mean(y)), y_std=float(np.std(y)),
            y_min=float(np.min(y)), y_max=float(np.max(y)),
            y_preview=[float(v) for v in y[:k]],
            x_preview=[[float(v) for v in row] for row in X[:k]],
        ),
        saved=os.path.join(RUN_DIR, data_id + ".npz"),
    )
    if mode == DISCOVERY:
        out["note"] = ("目标量的量纲与各变量的量纲均未提供。请根据变量名与物理语境"
                       "推断它们，再用 check_units 校验、用 build_candidates 检验推断是否自洽。")
    else:
        out["reference"] = meta["reference"]
        out["note"] = "benchmark 模式：真值已一并返回，可用 score_prediction 打分。"
    return out


def _load_data(data_id):
    """读回一批已生成的观测数据"""
    path = os.path.join(RUN_DIR, data_id + ".npz")
    if not os.path.exists(path):
        raise FileNotFoundError("找不到数据 %s，请先调用 load_problem 生成" % data_id)
    z = np.load(path)
    with open(os.path.join(RUN_DIR, data_id + ".meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    return z["X"], z["y"], meta


# ---------------------------------------------------------------------------
# 能力 3：校验单位字符串
# ---------------------------------------------------------------------------
_UNIT_HINT = ("写法：单位名用 * 连接、/ 表示除、^ 表示幂，且必须是**左结合**，"
              "不要写括号——写 W/m/K 而不是 W/(m*K)，写 kg/A/s^2 而不是 kg/(A*s^2)。"
              "无量纲写 空字符串 或 \"1\"。")


def check_units(variables_units, target_unit, data_id=None):
    """
    校验一组单位推断是否写对。

    只做两件事：① 每个单位串能不能解析；② 变量集合是否和数据对得上。
    **不判断物理上是否正确**——一个能解析但物理上错误的单位，会在这里通过，
    然后在 build_candidates 里表现为"候选被剪到几乎没有"。这正是编排层要观察的反馈。
    """
    variables_units = variables_units or {}
    rows, ok_all = [], True
    for name, unit in variables_units.items():
        rec = dict(name=name, input=unit)
        try:
            d = parse_unit(unit)
            rec.update(parsed_ok=True, normalized=fmt(d) or "1", is_dimensionless=bool(same(d, ZERO)))
        except Exception as exc:
            rec.update(parsed_ok=False, error=str(exc))
            ok_all = False
        rows.append(rec)

    tgt = dict(input=target_unit)
    try:
        d = parse_unit(target_unit)
        tgt.update(parsed_ok=True, normalized=fmt(d) or "1", is_dimensionless=bool(same(d, ZERO)))
    except Exception as exc:
        tgt.update(parsed_ok=False, error=str(exc))
        ok_all = False

    notes = []
    if data_id:
        _, _, meta = _load_data(data_id)
        want = [v["name"] for v in meta["variables"]]
        missing = [n for n in want if n not in variables_units]
        extra = [n for n in variables_units if n not in want]
        if missing:
            notes.append("还缺这些变量的单位：%s" % ", ".join(missing))
            ok_all = False
        if extra:
            notes.append("多出了数据里不存在的变量：%s" % ", ".join(extra))
            ok_all = False

    return dict(ok=ok_all, variables=rows, target=tgt,
                known_units=sorted(UNITS.keys()), syntax_hint=_UNIT_HINT,
                notes=notes)


# ---------------------------------------------------------------------------
# 能力 4：构造候选库 + 量纲剪枝  ★核心创新点★
# ---------------------------------------------------------------------------
def build_candidates(data_id, variables_units, target_unit, max_terms=250):
    """
    用给定的单位推断构造候选特征库，并做量纲一致性剪枝。

    这是量化创新点的那一步：剪枝比例（prune_ratio）就是"量纲先验省下了多少搜索空间"。
    同时它也是单位推断的**检验器**——如果单位推错，量纲自洽的候选项会急剧减少甚至归零。
    """
    X, y, meta = _load_data(data_id)
    names = [v["name"] for v in meta["variables"]]

    variables_units = variables_units or {}
    missing = [n for n in names if n not in variables_units]
    if missing:
        return dict(ok=False, error="缺少变量 %s 的单位" % ", ".join(missing),
                    hint="先调 check_units 补全后再建库。")

    try:
        vardims = [parse_unit(variables_units[n]) for n in names]
        target_dim = parse_unit(target_unit)
    except Exception as exc:
        return dict(ok=False, error="单位解析失败：%s" % exc,
                    hint=_UNIT_HINT)

    kept, st = _build_library(names, vardims, target_dim, max_terms=int(max_terms))
    A, used = make_matrix(kept, X, names)

    # lib_id 用内容摘要生成：同样的"数据 + 单位 + 目标 + 上限"必然得到同一个 id，
    # 跨进程、跨天都可复现（注意不能用内置 hash()，它对字符串是按进程随机化的）。
    _fingerprint = json.dumps(
        dict(data_id=data_id, names=names,
             units=[variables_units[n] for n in names],
             target_unit=target_unit, max_terms=int(max_terms)),
        sort_keys=True, ensure_ascii=False)
    lib_id = "lib_" + hashlib.sha1(_fingerprint.encode("utf-8")).hexdigest()[:16]
    spec = dict(lib_id=lib_id, data_id=data_id, names=names,
                variables_units={n: variables_units[n] for n in names},
                target_unit=target_unit, max_terms=int(max_terms))
    _ensure_dir()
    with open(os.path.join(RUN_DIR, lib_id + ".json"), "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    diagnostics, hint = [], None
    if st["kept"] == 0:
        hint = ("剪枝后一个候选项都不剩，说明这组单位让目标量「无法由任何候选项拼出」。"
                "最常见的原因是目标量的量纲推错了，或某个变量的量纲推错了。"
                "建议重新审读变量名与物理语境，再试一组（check_units 只能查写法，查不出这种物理错误）。")
    elif st["kept"] < 5:
        hint = ("剪枝后候选只剩 %d 项，数量偏少。若后续拟合效果差，可以怀疑单位推断有误；"
                "若拟合很好也要警惕：候选太少可能是碰巧。" % st["kept"])
    if st["truncated"]:
        diagnostics.append("保留项超过 max_terms=%d，已按「表达式最简优先」截断。" % int(max_terms))
    if len(used) < st["kept"]:
        diagnostics.append("有 %d 个候选在数据上求值后数值不健康（非有限值/量级过大），已剔除。"
                           % (st["kept"] - len(used)))
    if hint:
        diagnostics.append(hint)

    return dict(
        ok=True, lib_id=lib_id, data_id=data_id,
        target_dimension=fmt(target_dim) or "1",
        candidate_total=st["total"],
        candidate_kept=st["kept"],
        prune_ratio=round(st["prune_ratio"], 4),
        prune_percent=round(100 * st["prune_ratio"], 1),
        usable_columns=len(used),
        truncated=bool(st["truncated"]),
        n_dimensionless_bases=st["n_units"],
        sample_candidates=[t.expr for t in kept[:8]],
        diagnostics=diagnostics,
    )


def _get_library(lib_id):
    """读回候选库（按 spec 确定性重建，避免序列化 Term 对象）"""
    path = os.path.join(RUN_DIR, lib_id + ".json")
    if not os.path.exists(path):
        raise FileNotFoundError("找不到候选库 %s，请先调用 build_candidates 建立" % lib_id)
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    vardims = [parse_unit(spec["variables_units"][n]) for n in spec["names"]]
    kept, _ = _build_library(spec["names"], vardims, parse_unit(spec["target_unit"]),
                             max_terms=spec["max_terms"])
    return kept, spec


# ---------------------------------------------------------------------------
# 能力 5：稀疏回归
# ---------------------------------------------------------------------------
def fit_sparse(lib_id, data_id, strategy="omp", max_terms=10, threshold=0.05):
    """
    从候选库里挑出少数几项，配系数，拼成一条公式。

    strategy = "omp"   贪心前向选择，按"能不能真正降低残差"挑项，对共线不敏感（本项目主路线）
             = "stlsq" 序贯阈值最小二乘，经典做法，在候选项高度共线时会失效（留作对照）
    """
    terms, spec = _get_library(lib_id)
    X, y, meta = _load_data(data_id)
    A, used = make_matrix(terms, X, spec["names"])

    if A.shape[1] == 0:
        return dict(ok=False, error="候选库在该数据上没有任何可用列，无法拟合。",
                    hint="通常是单位推断有误，建议回到 build_candidates 重新检验。")

    strategy = (strategy or "omp").lower()
    # 截距是唯一"绕过量纲检查"的自由参数，所以它必须服从同一条规则：
    # 目标量有量纲时不许出现加性常数，否则会拟合出 `0.5*m*v**2 - 1.2` 这种
    # 在物理上无意义、在小量级处严重错的公式（实测在 AGH 里就这么发生过）。
    target_dim = parse_unit(spec["target_unit"])
    allow_intercept = same(target_dim, ZERO)
    if strategy == "omp":
        r = fit_best(A, y, max_terms=int(max_terms), allow_intercept=allow_intercept)
    elif strategy == "stlsq":
        r = fit_stlsq(A, y, threshold=float(threshold), allow_intercept=allow_intercept)
        r.setdefault("val_rss", float("nan"))
    else:
        return dict(ok=False, error="未知策略 %r，只支持 omp 或 stlsq" % strategy)

    coef, intercept = r["coef"], r["intercept"]
    used_names = [t.expr for t in used]
    formula = build_expr_string(coef, intercept, used_names)
    readable = to_expression(coef, intercept, used_names, y_ref=float(np.mean(np.abs(y))))

    order = np.argsort(-np.abs(coef))[:8]
    selected = [dict(expression=used_names[i], coefficient=float(coef[i]))
                for i in order if abs(coef[i]) > 1e-10]

    notes = []
    if r["k"] >= int(max_terms):
        notes.append("选中的项数已达到 max_terms=%d 上限，可能是候选表达力不足；"
                     "可以考虑放宽 max_terms，或检查单位推断。" % int(max_terms))
    if strategy == "stlsq":
        notes.append("STLSQ 在候选项共线时系数会膨胀、阈值失去区分力，"
                     "对照结论请以 compare_strategies 的输出为准。")

    return dict(
        ok=True, lib_id=lib_id, data_id=data_id, strategy=strategy,
        formula=formula,                 # 全精度，供 verify_formula 使用
        formula_readable=readable,       # 便于阅读的写法
        n_terms=int(r["k"]),
        selected_terms=selected,
        intercept=float(intercept),
        r2_fit=round(float(r["r2"]), 6),
        rss=float(r["rss"]),
        val_rss=None if not np.isfinite(r.get("val_rss", np.nan)) else float(r["val_rss"]),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 能力 6：独立验证
# ---------------------------------------------------------------------------
def verify_formula(formula, data_id, expand=0.3, n_samples=100, seed_offset=7,
                   tol_in_range=0.05, tol_extrap=0.20):
    """
    独立验证一条公式。

    做法：**不拿训练数据当依据**，另开两批全新的采样点来查：
      ① 训练区间内、换一个随机种子重新采点 → 检验它是不是只在拟合噪声
      ② 训练区间之外（expand=0.3 向外扩 30%）重新采点 → 检验它是"找到定律"还是"插值"
    判据是归一化 RMSE（残差除以观测值的标准差）。

    除了 RMSE，还做一道**符号合理性检查**：观测值在整段区间上同号时，
    预测值不应在区间内反号。这道检查是实测被自己的数据打脸后补上的——
    曾有一条公式在真值 E≈0.14 处预测出 -1.07（负动能），
    归一化 RMSE 仍只有 0.0034、被判 passed。原因是整体 RMSE 由大量级点主导。
    所以现在小量级处的相对误差（max_rel_err_small）会单独报出来。

    诚实说明：本函数**需要参考解**（用于在全新点上算误差），
    参考解取自内置方程库，**不返回给智能体**。所以它检查的是通用化能力，
    不是"完全无真值的自洽检验"。这一点在报告里如实写明。
    benchmark 模式下额外给出与真值的对比指标，但那只是给评测用的，不参与通过/不通过判定。
    """
    X, y, meta = _load_data(data_id)
    names = [v["name"] for v in meta["variables"]]
    eq = EQ.get(meta["problem_id"])
    noise = float(meta["noise"])

    def _score(Xn, tag):
        try:
            yn = evaluate(eq["expr"], Xn, names)
            pred = evaluate(formula, Xn, names)
        except Exception as exc:
            return dict(tag=tag, ok=False, error=str(exc))
        good = np.isfinite(yn) & np.isfinite(pred)
        if good.sum() < 10:
            return dict(tag=tag, ok=False, error="有效测试点不足（%d 个）" % int(good.sum()))
        yn, pred = yn[good], pred[good]
        rmse = float(np.sqrt(np.mean((pred - yn) ** 2)))
        sd = float(np.std(yn)) or 1.0

        # ---- 符号合理性 ----
        # 观测值在整段区间上同号（例如动能、质量、时间恒为正）时，
        # 预测值出现在区间内反号的一侧，是与数据直接矛盾的，不必看 RMSE 就能判错。
        # 加这一条的原因：归一化 RMSE 由大量级点主导，会掩盖小量级处的严重错误。
        # 实测踩过——`0.5017*m*v**2 - 1.204` 在真值 E≈0.14 处预测出 -1.07（负动能），
        # 归一化 RMSE 仍只有 0.0034、被判 passed。
        sign_violation = False
        if yn.size and pred.size:
            if np.all(yn > 0) and float(np.min(pred)) < 0:
                sign_violation = True
            elif np.all(yn < 0) and float(np.max(pred)) > 0:
                sign_violation = True

        # ---- 分段相对误差：把大量级与小量级分开看 ----
        # 只报整体 RMSE 会掩盖"小量级处错得离谱"的情形，所以这里额外给出两段诊断。
        amax = float(np.max(np.abs(yn))) or 1.0
        small = np.abs(yn) < 0.05 * amax
        large = np.abs(yn) >= 0.20 * amax
        def _maxrel(mask):
            if mask.sum() < 3:
                return None
            r = np.abs(pred[mask] - yn[mask]) / np.abs(yn[mask])
            return float(np.max(r))
        return dict(tag=tag, ok=True, n=int(good.sum()),
                    rmse=rmse, rmse_normalized=rmse / sd,
                    max_abs_err=float(np.max(np.abs(pred - yn))),
                    min_pred=float(np.min(pred)), min_obs=float(np.min(yn)),
                    sign_violation=bool(sign_violation),
                    max_rel_err_large=_maxrel(large),
                    max_rel_err_small=_maxrel(small),
                    small_scale_note=("真值最小的 5%% 区间里有 %d 点" % int(small.sum())))

    # ① 训练区间内、新种子重采
    Xi = sample_X(eq, int(n_samples), int(meta["seed"]) + int(seed_offset), expand=0.0)
    s_in = _score(Xi, "in_range_resample")

    # ② 区间外推
    Xe = sample_X(eq, int(n_samples), int(meta["seed"]) + int(seed_offset), expand=float(expand))
    s_ex = _score(Xe, "extrapolation")

    passes_in = bool(s_in.get("ok") and s_in["rmse_normalized"] <= tol_in_range)
    passes_ex = bool(s_ex.get("ok") and s_ex["rmse_normalized"] <= tol_extrap)
    violates_sign = bool(s_in.get("sign_violation") or s_ex.get("sign_violation"))
    passed = bool(passes_in and passes_ex and not violates_sign)

    verdict, reason = None, None
    if not s_in.get("ok"):
        verdict, reason = "invalid", "公式在训练区间的新采样点上无法求值：%s" % s_in.get("error")
    elif violates_sign:
        who = []
        if s_in.get("sign_violation"):
            who.append("区间内")
        if s_ex.get("sign_violation"):
            who.append("外推区间")
        verdict, reason = "implausible", (
            "%s 违反符号合理性：观测值在整段区间上同号，而公式预测出了相反的符号"
            "（预测最小值 %.4g，观测最小值 %.4g）。这说明公式在小量级处严重偏离，"
            "即使归一化 RMSE 很小也不能认为找到了定律——建议检查是否混入了非法的"
            "加性常数（目标量有量纲时截距必须是 0）。"
            % ("与".join(who), s_in.get("min_pred", float("nan")), s_in.get("min_obs", float("nan"))))
    elif not passes_in:
        verdict, reason = "underfit", ("训练区间新点上误差偏大（归一化 RMSE %.4g > %.4g），"
                                       "说明公式连区间内都不稳。" % (s_in["rmse_normalized"], tol_in_range))
    elif not passes_ex:
        verdict, reason = "overfit", ("区间内拟合良好但外推崩了（归一化 RMSE %.4g > %.4g），"
                                      "这是典型的「只拟合数据、没找到定律」，建议增加项或调整候选。" %
                                      (s_ex["rmse_normalized"], tol_extrap))
    else:
        verdict, reason = "passed", "区间内与区间外都通过，可以认为找到了可外推的关系。"

    out = dict(ok=True, data_id=data_id, passed=passed, verdict=verdict, reason=reason,
               in_range=s_in, extrapolation=s_ex,
               noise_level=noise,
               thresholds=dict(tol_in_range=tol_in_range, tol_extrap=tol_extrap),
               checks=dict(sign_consistency=not violates_sign),
               note=("检验方式：在**全新的采样点**上把公式与参考解逐点对比，并额外在向外扩张 "
                     "%.0f%% 的区间外推区上再比一次。判据是归一化 RMSE（残差 ÷ 观测值标准差）。"
                     "注意这是与参考解对比的通用化检验，不是与训练数据比——"
                     "所以小量级处的相对误差要单独看 max_rel_err_small，"
                     "整体 RMSE 会被大量级点主导而掩盖它。"
                     % (100 * float(expand))))

    if meta.get("mode") == BENCHMARK:
        ref = meta["reference"]["expr"]
        out["against_truth"] = dict(
            nmse_train=round(float(nmse(formula, ref, X, names)), 8),
            r2_extrapolation=round(float(r2(formula, ref, Xe, names)), 6),
        )
    return out


# ---------------------------------------------------------------------------
# 能力 7：两种稀疏化策略对照
# ---------------------------------------------------------------------------
def compare_strategies(lib_id, data_id, max_terms=10, threshold=0.05):
    """
    在同一份数据、同一个候选库上跑 OMP 与 STLSQ，给出对照表。

    这张表是"验证严谨性"的直接证据：我们不是随手选了 OMP，
    而是把两种策略都跑了、并把 STLSQ 失效的现象记录下来。
    """
    rows, fails = [], []
    for name, kw in (("omp", dict(max_terms=int(max_terms))),
                     ("stlsq", dict(threshold=float(threshold)))):
        r = fit_sparse(lib_id, data_id, strategy=name, **kw)
        if not r.get("ok"):
            fails.append(dict(strategy=name, error=r.get("error")))
            continue
        rows.append(dict(strategy=name, n_terms=r["n_terms"], r2_fit=r["r2_fit"],
                         rss=r["rss"], val_rss=r["val_rss"],
                         formula=r["formula_readable"]))

    notes = []
    if len(rows) == 2:
        a, b = rows
        if b["n_terms"] > a["n_terms"]:
            notes.append("STLSQ 选出了更多项（%d 项 vs OMP 的 %d 项）："
                         "在候选项高度共线时，STLSQ 的系数阈值会失去区分力，"
                         "无法有效稀疏化——这正是本项目改用 OMP 的实测理由。"
                         % (b["n_terms"], a["n_terms"]))
    return dict(ok=bool(rows), lib_id=lib_id, data_id=data_id,
                comparison=rows, failures=fails, notes=notes)


# ---------------------------------------------------------------------------
# 能力 8：与真值打分（仅 benchmark 模式）
# ---------------------------------------------------------------------------
def score_prediction(formula, data_id, ext_samples=100, expand=0.3):
    """
    把候选公式与真值公式对比打分。**只用于基准评测**，discovery 模式下会直接拒绝。

    判定口径沿用符号回归领域通行的四级：
        exact            与真值完全一致
        up_to_constant   结构一致、只差一个常数因子（算恢复成功）
        approx           形式不一致但 NMSE 很小
        wrong            错误
    """
    X, y, meta = _load_data(data_id)
    if meta.get("mode") != BENCHMARK:
        return dict(ok=False, error="当前数据是 discovery 模式，没有真值可对比",
                    hint="用 mode=benchmark 重新 load_problem，或改用 verify_formula 做无真值验证。")

    names = [v["name"] for v in meta["variables"]]
    eq = EQ.get(meta["problem_id"])
    ref = meta["reference"]["expr"]

    c = classify(formula, ref, X, names)
    Xe = sample_X(eq, 100, int(meta["seed"]) + 7, expand=0.3)
    te = evaluate(ref, Xe, names)
    ok = np.isfinite(te)
    if ok.sum() >= 10:
        Xe = Xe[ok]
        r2_ex, nmse_ex = float(r2(formula, ref, Xe, names)), float(nmse(formula, ref, Xe, names))
    else:
        r2_ex, nmse_ex = float("nan"), float("nan")

    return dict(
        ok=True, data_id=data_id,
        verdict=c["verdict"], verdict_label=VERDICT_LABEL[c["verdict"]],
        recovered_symbolically=bool(is_recovered(c["verdict"])),
        max_relative_error=c["max_rel"], ratio=c["ratio"],
        nmse_train=c["nmse"],
        r2_extrapolation=r2_ex, nmse_extrapolation=nmse_ex,
        truth_formula=ref,
    )


# ---------------------------------------------------------------------------
# 自检：把 8 个能力按"最理想的顺序"走一遍，确认接线正确
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("=== 原子能力自检（以 P03 动能为例）===")

    probs = list_problems()
    print("1. list_problems           → %d 个问题，例如 %s"
          % (probs["count"], probs["problems"][2]["problem_id"] + " " + probs["problems"][2]["name"]))

    # 注意：变量名不要用 r2，会遮蔽同名的 r2() 函数
    data = load_problem("P03", n_samples=100, noise=0.0, seed=0, mode=DISCOVERY)
    print("2. load_problem(P03)       → data_id=%s  目标量=%s（单位未提供）"
          % (data["data_id"], data["target"]["name"]))
    print("     观测值预览：%s" % [round(v, 3) for v in data["observations"]["y_preview"]])

    units = {"m": "kg", "v": "m/s"}
    chk = check_units(units, "J", data_id=data["data_id"])
    print("3. check_units             → ok=%s  目标量纲=%s" % (chk["ok"], chk["target"]["normalized"]))

    lib = build_candidates(data["data_id"], units, "J")
    print("4. build_candidates        → 候选 %d → %d 项，砍掉 %.1f%%，可用列 %d"
          % (lib["candidate_total"], lib["candidate_kept"],
             lib["prune_percent"], lib["usable_columns"]))
    print("     样例候选：%s" % lib["sample_candidates"][:4])

    fitted = fit_sparse(lib["lib_id"], data["data_id"], strategy="omp")
    print("5. fit_sparse              → %s（%d 项，R²=%.6f）"
          % (fitted["formula_readable"], fitted["n_terms"], fitted["r2_fit"]))

    ver = verify_formula(fitted["formula"], data["data_id"])
    print("6. verify_formula          → passed=%s  区间内 %.2e  外推 %.2e"
          % (ver["passed"], ver["in_range"]["rmse_normalized"],
             ver["extrapolation"]["rmse_normalized"]))

    cmp = compare_strategies(lib["lib_id"], data["data_id"])
    print("7. compare_strategies      → " + " | ".join(
        "%s: %d 项" % (x["strategy"], x["n_terms"]) for x in cmp["comparison"]))

    # 故意推错单位，看反馈是否够清楚（这是「失败类测试样例」的来源）
    print()
    print("=== 故意推错单位：把 v 写成 m/s^2 ===")
    bad_units = {"m": "kg", "v": "m/s^2"}
    lib_bad = build_candidates(data["data_id"], bad_units, "J", max_terms=250)
    print("  候选 %d → %d 项；诊断：%s"
          % (lib_bad["candidate_total"], lib_bad["candidate_kept"],
             lib_bad["diagnostics"][-1] if lib_bad["diagnostics"] else "(无)"))

    print()
    print("=== benchmark 模式打分 ===")
    dbench = load_problem("P03", n_samples=100, noise=0.01, seed=0, mode=BENCHMARK)
    lbench = build_candidates(dbench["data_id"], units, "J")
    fbench = fit_sparse(lbench["lib_id"], dbench["data_id"])
    sc = score_prediction(fbench["formula"], dbench["data_id"])
    print("  预测 %s → %s（%s），外推 R²=%.4f"
          % (fbench["formula_readable"], sc["verdict"], sc["verdict_label"],
             sc["r2_extrapolation"]))
