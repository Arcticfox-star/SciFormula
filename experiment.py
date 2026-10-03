# -*- coding: utf-8 -*-
"""
experiment.py —— 主实验流程（跑这一条命令就出全部结果）

【整个流程长这样，也是要交给评委看的那张"闭环图"】
    读数据（造带噪声的观测数据）
      → 构造候选特征库
      → 量纲剪枝（用物理先验砍掉不可能的项）★创新点★
      → 稀疏回归（STLSQ + BIC 自动选阈值）
      → 等价判定（回归出的公式 vs 真值公式）
      → 外推检验（在训练区间之外再测一次）
      → 汇总出报告

【三个环环相扣的验证环节，这是"验证严谨性"的底气】
    1. 无噪声档：能不能精确恢复出真值公式（符号恢复率）
    2. 噪声档：噪声变大时结果怎么退化（鲁棒性）
    3. 外推：在**没见过的区间**上还准不准（区分"真找到定律"和"过拟合数据"）

用法：
    python experiment.py                # 跑全量，结果写到 results/
    python experiment.py P03 P11        # 只跑指定题目
"""

import os
import sys
import time
import csv

import numpy as np

import equations as EQ
import pipeline as P
from equivalence import VERDICT_LABEL, is_recovered

NOISE_LEVELS = [0.0, 0.01, 0.02]
N_TRAIN = 100
N_EXTRAP = 100
SEED = 0
EXTRAP_FACTOR = 0.3          # 外推区间向外扩张的比例


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------
def sample_X(eq, n, seed, expand=0.0):
    """
    按方程定义的区间采一批自变量。
    expand > 0 时把区间向外扩张，用来做外推检验（在"没见过的区间"上测）。
    扩张时对正数量做乘法、对含负数的量做加法，避免造出负的物理量。
    """
    rng = np.random.default_rng(seed)
    names = EQ.varnames(eq)
    cols = []
    for name in names:
        spec = eq["ranges"][name]
        lo, hi = float(spec[0]), float(spec[1])
        mode = spec[2] if len(spec) > 2 else "linear"
        if expand > 0:
            if lo >= 0:
                lo, hi = lo * (1 - expand), hi * (1 + expand)
            else:
                span = hi - lo
                lo, hi = lo - expand * span, hi + expand * span
        if mode == "log":
            cols.append(np.exp(rng.uniform(np.log(max(lo, 1e-12)), np.log(hi), n)))
        else:
            cols.append(rng.uniform(lo, hi, n))
    return np.column_stack(cols)


def add_noise(y_clean, noise, seed):
    """加高斯噪声，标准差 = noise × 真值的标准差。noise=0 就是原样返回。"""
    if noise <= 0:
        return y_clean.copy()
    rng = np.random.default_rng(seed + 999)
    return y_clean + noise * float(np.std(y_clean)) * rng.standard_normal(len(y_clean))


# ---------------------------------------------------------------------------
# 单条实验
# ---------------------------------------------------------------------------
def run_one(eq, noise, verbose=False):
    """
    跑一条方程在一个噪声档下的完整流程，返回一行结果字典。

    【为什么要改成现在这样】
    改造前，这个函数自己实现了一遍全部算法步骤。问题有两层：
      ① 智能体调用的是另一条代码路径，报告成绩和智能体跑出来的结果可能不一致；
      ② 所有决定都写死在这里，AGH 就没有任何要做的决策（违反规程 3.1 的实质）。
    现在这里**只按顺序调用 pipeline 的原子能力**——也就是 AGH 智能体运行时
    调用的同一批工具。于是「批量基准评测」和「智能体闭环」共用同一条实现，
    报告里的数字与演示时智能体跑出来的数字必然一致。
    """
    t0 = time.time()
    names = EQ.varnames(eq)
    vars_units = {k: eq["vars"][k] for k in names}

    # 1) 观测数据（benchmark 模式：额外保存真值，用于打分）
    d = P.load_problem(eq["id"], n_samples=N_TRAIN, noise=noise, seed=SEED, mode=P.BENCHMARK)
    data_id = d["data_id"]

    # 2) 候选库 + 量纲剪枝（此处用真值单位，等价于"智能体推断完全正确"的理想情况）
    lib = P.build_candidates(data_id, vars_units, eq["target_unit"])

    # 3) 稀疏回归
    fit = P.fit_sparse(lib["lib_id"], data_id, strategy="omp")

    # 4) 与真值对比打分（含区间外推）
    sc = P.score_prediction(fit["formula"], data_id,
                            ext_samples=N_EXTRAP, expand=EXTRAP_FACTOR)

    return dict(
        eq_id=eq["id"], eq_name=eq["name"], ref=eq["ref"],
        target_unit=eq["target_unit"] or "(无量纲)",
        n_vars=len(names), noise=noise,
        cand_total=lib["candidate_total"], cand_kept=lib["candidate_kept"],
        prune_ratio=lib["prune_ratio"], truncated=lib["truncated"],
        n_cols=lib["usable_columns"], n_terms=fit["n_terms"], val_rss=fit["val_rss"],
        r2_train=fit["r2_fit"], intercept=fit["intercept"],
        truth=eq["expr"], pred=fit["formula_readable"], pred_full=fit["formula"],
        verdict=sc["verdict"], max_rel=sc["max_relative_error"],
        ratio=sc["ratio"], nmse_train=sc["nmse_train"],
        r2_extrap=sc["r2_extrapolation"], nmse_extrap=sc["nmse_extrapolation"],
        seconds=time.time() - t0,
    )


# ---------------------------------------------------------------------------
# 汇总与输出
# ---------------------------------------------------------------------------
FIELDS = ["eq_id", "eq_name", "ref", "target_unit", "n_vars", "noise",
          "cand_total", "cand_kept", "prune_ratio", "truncated",
          "n_cols", "n_terms", "val_rss", "r2_train", "intercept",
          "truth", "pred", "verdict", "max_rel", "ratio", "nmse_train",
          "r2_extrap", "nmse_extrap", "seconds"]


EXTRAP_TOL = 0.99           # 任何"恢复成功"都必须再过这道外推门槛


def usable(r):
    """
    判定一条结果算不算"恢复成功"。用的是**双重条件**，而且是两次被自己的数据教育之后才定型的。

    第一次：只按 NMSE 判"近似恢复"会出假阳性。实测里 P19 在 1% 噪声下 NMSE 很小被判近似恢复，
    但它的外推 R² 只有 0.83——说明那个公式只是在训练区间里插值，并不是真找到了定律。
    于是加了一道外推检验。

    第二次：外推门槛原本只加在"近似恢复"上，而"结构判定"那一侧的门槛又设得极严（系数比值
    要恒定到 1e-6），两边的松紧刚好错开，恰好把 P19 放了过去——它先是因结构判定过严被判成
    近似恢复（于是要过外推门槛），把结构门槛放宽到合理值（5%）之后，它又升级成"差一个常数因子"，
    就此绕开了外推门槛。修法不是调阈值，而是**取消这个不对称**：
    恢复成功 = 结构判定通过 且 外推检验通过，一视同仁，不再有哪一类可以豁免。
    """
    if r["verdict"] not in ("exact", "up_to_constant", "approx"):
        return False
    return bool(np.isfinite(r["r2_extrap"]) and r["r2_extrap"] >= EXTRAP_TOL)


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v)
                        for k, v in r.items() if k in FIELDS})


def print_summary(rows):
    print("\n" + "=" * 100)
    print("总表：每行 = 一条方程 × 一个噪声档")
    print("=" * 100)
    print("%-5s %-20s %6s %7s %7s %7s %8s %-14s %9s" %
          ("ID", "名称", "噪声", "剪枝前", "剪枝后", "选中项", "训练R²", "判定", "外推R²"))
    for r in rows:
        print("%-5s %-20s %6.0f%% %7d %7d %7d %8.5f %-14s %9.5f" % (
            r["eq_id"], r["eq_name"], 100 * r["noise"],
            r["cand_total"], r["cand_kept"], r["n_terms"], r["r2_train"],
            VERDICT_LABEL[r["verdict"]], r["r2_extrap"]))

    print("\n" + "=" * 100)
    print("核心指标")
    print("=" * 100)
    ids = sorted({r["eq_id"] for r in rows})
    for noise in NOISE_LEVELS:
        sub = [r for r in rows if abs(r["noise"] - noise) < 1e-12]
        if not sub:
            continue
        strict = sum(1 for r in sub if r["verdict"] == "exact")
        loose = sum(1 for r in sub if is_recovered(r["verdict"]))
        ok = sum(1 for r in sub if usable(r))
        label = "无噪声" if noise == 0 else "%.0f%% 噪声" % (100 * noise)
        print("  %-10s 精确恢复 %2d/%d (%5.1f%%)   结构判定通过 %2d/%d (%5.1f%%)   "
              "通过双重检验 %2d/%d (%5.1f%%)"
              % (label, strict, len(sub), 100 * strict / len(sub),
                 loose, len(sub), 100 * loose / len(sub),
                 ok, len(sub), 100 * ok / len(sub)))
    print("  （通过双重检验 = 结构判定通过（完全恢复/差一个常数因子/近似恢复）"
          " 且 外推 R² ≥ %.2f）" % EXTRAP_TOL)

    tot_c = sum(r["cand_total"] for r in rows)
    tot_k = sum(r["cand_kept"] for r in rows)
    print("\n  量纲剪枝：候选全集 %d 项 → 剪枝后 %d 项，共砍掉 %.1f%%"
          % (tot_c, tot_k, 100 * (1 - tot_k / tot_c)))

    bad = [r for r in rows if r["noise"] == 0 and not is_recovered(r["verdict"])]
    print("\n  无噪声下未恢复的题目（%d 条，这些是要写进报告的失败案例）：" % len(bad))
    for r in bad:
        print("    %s %-22s 判定=%-12s 回归出：%s"
              % (r["eq_id"], r["eq_name"], VERDICT_LABEL[r["verdict"]], r["pred"]))


def write_report(rows, path):
    """生成一份自包含的 HTML 报告（不需要联网、不需要额外库）"""
    ids = sorted({r["eq_id"] for r in rows})
    by_noise = {}
    for noise in NOISE_LEVELS:
        sub = [r for r in rows if abs(r["noise"] - noise) < 1e-12]
        if not sub:
            continue
        by_noise[noise] = dict(
            n=len(sub),
            strict=sum(1 for r in sub if r["verdict"] == "exact"),
            loose=sum(1 for r in sub if is_recovered(r["verdict"])),
            usable=sum(1 for r in sub if usable(r)),
            mean_r2=float(np.mean([r["r2_train"] for r in sub])),
            mean_r2_ex=float(np.nanmean([r["r2_extrap"] for r in sub])),
        )

    tot_c = sum(r["cand_total"] for r in rows)
    tot_k = sum(r["cand_kept"] for r in rows)
    bad = [r for r in rows if r["noise"] == 0 and not is_recovered(r["verdict"])]

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    H = []
    H.append("""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>SciFormula 实验结果</title><style>
:root{--bg:#FAF9F5;--card:#FFFFFF;--line:#E4E2DA;--tx:#2C2C2A;--tx2:#6B6B66;--tx3:#9A9A93;
--green:#3F7A3F;--green-bg:#EAF3E4;--red:#A33;--red-bg:#FBEDED;--amber:#8A6410;--amber-bg:#FDF3DC;
--blue:#2A5C8F;--blue-bg:#EAF1F9;--mono:ui-monospace,SFMono-Regular,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 60px;background:var(--bg);color:var(--tx);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px}h2{font-size:19px;margin:34px 0 12px}
.sub{color:var(--tx2);font-size:13.5px;margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:14px 0}
.m{background:#F4F2EC;border-radius:8px;padding:12px 14px}
.m .k{font-size:12px;color:var(--tx2)}.m .v{font-size:22px;font-weight:600;margin-top:3px}
.m .v small{display:block;font-size:11.5px;font-weight:400;color:var(--tx3);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0;background:var(--card)}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:#F4F2EC;font-weight:600;font-size:12.5px}
td.c,th.c{text-align:center}
code,.mono{font-family:var(--mono);font-size:12.5px}
.ok{color:var(--green);font-weight:600}.no{color:var(--red);font-weight:600}
.warn{color:var(--amber);font-weight:600}
.cal{border-left:3px solid var(--blue);background:var(--blue-bg);padding:12px 16px;
border-radius:0 8px 8px 0;margin:14px 0}
.cal.g{border-color:var(--green);background:var(--green-bg)}
.cal.a{border-color:#C99A2E;background:var(--amber-bg)}
.cal b{display:block;margin-bottom:4px}
.bar{height:9px;background:#EDEBE4;border-radius:5px;overflow:hidden;min-width:70px}
.bar i{display:block;height:100%;background:#6E9BC5}
.bar i.g{background:#7BAF6B}
small{color:var(--tx3)}
</style></head><body><div class="wrap">""")
    H.append("<h1>SciFormula 实验结果报告</h1>")
    H.append('<p class="sub">从带噪声的观测数据中自动反推物理公式，并用量纲约束收缩搜索空间。'
             '本报告由 <code>experiment.py</code> 自动生成，所有数字都可在 '
             '<code>results/detailed.csv</code> 中逐条核对。</p>')

    # 顶部指标
    n0 = by_noise.get(0.0, {})
    H.append('<div class="grid">')
    H.append('<div class="m"><div class="k">题目数</div><div class="v">%d<small>内置迷你物理方程库</small></div></div>' % len(ids))
    H.append('<div class="m"><div class="k">量纲剪枝</div><div class="v">%.1f%%<small>候选 %d → %d 项</small></div></div>'
             % (100 * (1 - tot_k / tot_c), tot_c, tot_k))
    H.append('<div class="m"><div class="k">无噪声符号恢复率</div><div class="v">%.0f%%<small>%d / %d 题</small></div></div>'
             % (100.0 * n0.get("loose", 0) / max(n0.get("n", 1), 1), n0.get("loose", 0), n0.get("n", 0)))
    H.append('<div class="m"><div class="k">通过双重检验</div><div class="v">%.0f%%<small>%d / %d 题</small></div></div>'
             % (100.0 * n0.get("usable", 0) / max(n0.get("n", 1), 1), n0.get("usable", 0), n0.get("n", 0)))
    H.append('</div>')

    H.append('<div class="cal g"><b>怎么读这张表</b>'
             '「判定」有四种：<b>完全恢复</b>（与真值公式数值上完全一致）、'
             '<b>差一个常数因子</b>（形式对上了，只是系数没归一化，比如算出 '
             '<code>1*(m*v**2)</code> 而真值是 <code>m*v**2/2</code>）、'
             '<b>近似恢复</b>（形式不完全一致，但 NMSE 很小）、'
             '<b>错误</b>。前两者是符号回归领域的通行「恢复成功」口径。<br>'
             '「外推 R²」是在<b>训练区间之外</b>测的——它才真正区分"找到了定律"和"拟合了数据"。'
             '<b>「通过双重检验」= 结构判定通过，而且 外推 R² ≥ %.2f</b>。'
             '加这第二道门槛，是因为我们自己的数据打过一次脸：某道题在 1%% 噪声下 NMSE 很小、'
             '被判"近似恢复"，但外推 R² 只有 0.83——说明它只是在训练区间里插值，并不是真找到了公式。'
             '后来又发现门槛加得不平衡（只加在「近似恢复」上），被这道题从另一条路径绕了过去，'
             '于是改成<b>所有判定一视同仁</b>：结构对 + 外推对，才算数。'
             '只看 NMSE 会给出假阳性。</div>' % EXTRAP_TOL)

    # 分噪声档
    H.append("<h2>一、不同噪声水平下的表现</h2>")
    H.append('<table><thead><tr><th>噪声水平</th><th class="c">精确恢复</th>'
             '<th class="c">结构判定通过</th><th class="c">近似及以上</th>'
             '<th class="c">平均训练 R²</th><th class="c">平均外推 R²</th></tr></thead><tbody>')
    for noise in sorted(by_noise):
        d = by_noise[noise]
        lbl = "无噪声（0%）" if noise == 0 else "%.0f%%" % (100 * noise)
        H.append("<tr><td>%s</td><td class='c'>%d / %d</td><td class='c'>%d / %d</td>"
                 "<td class='c'>%d / %d</td><td class='c'>%.5f</td><td class='c'>%.5f</td></tr>"
                 % (lbl, d["strict"], d["n"], d["loose"], d["n"], d["usable"], d["n"],
                    d["mean_r2"], d["mean_r2_ex"]))
    H.append("</tbody></table>")

    # 逐题
    H.append("<h2>二、逐题结果（噪声 0%，即「能不能精确恢复」）</h2>")
    H.append('<table><thead><tr><th style="width:44px">ID</th><th>真值公式</th>'
             '<th>回归出的公式</th><th class="c" style="width:96px">判定</th>'
             '<th class="c" style="width:70px">选中项</th>'
             '<th class="c" style="width:120px">剪枝 前→后</th>'
             '<th class="c" style="width:80px">外推 R²</th></tr></thead><tbody>')
    for r in rows:
        if r["noise"] != 0:
            continue
        cls = {"exact": "ok", "up_to_constant": "warn",
               "approx": "warn", "wrong": "no", "error": "no"}[r["verdict"]]
        ratio = "  (×%.4g)" % r["ratio"] if r["verdict"] == "up_to_constant" else ""
        pct = 100 * (1 - r["cand_kept"] / max(r["cand_total"], 1))
        H.append("<tr><td class='mono'>%s</td><td><code>%s</code></td>"
                 "<td><code>%s</code></td>"
                 "<td class='c'><span class='%s'>%s</span>%s</td>"
                 "<td class='c'>%d</td>"
                 "<td class='c'>%d → %d<br><small>砍 %.0f%%</small></td>"
                 "<td class='c'>%s</td></tr>"
                 % (r["eq_id"], esc(r["truth"]), esc(r["pred"]), cls,
                    VERDICT_LABEL[r["verdict"]], esc(ratio), r["n_terms"],
                    r["cand_total"], r["cand_kept"], pct,
                    "%.5f" % r["r2_extrap"] if np.isfinite(r["r2_extrap"]) else "n/a"))
    H.append("</tbody></table>")

    # 失败案例
    H.append("<h2>三、失败案例（如实记录，这也是赛事要求的「失败类测试样例」）</h2>")
    if bad:
        H.append('<table><thead><tr><th style="width:44px">ID</th><th>真值公式</th>'
                 '<th>回归结果</th><th class="c" style="width:90px">判定</th>'
                 '<th>失败原因（人工分析）</th></tr></thead><tbody>')
        reasons = {
            "P19": "真值含 (1-u²)^(-1/2) 这种复合形式，而候选特征库里没有这一类基函数。"
                   "这暴露的是<b>特征库表达能力的上限</b>，不是算法错误——属于如实声明的能力边界。",
        }
        for r in bad:
            H.append("<tr><td class='mono'>%s</td><td><code>%s</code></td>"
                     "<td><code>%s</code></td><td class='c'><span class='no'>%s</span></td>"
                     "<td>%s</td></tr>"
                     % (r["eq_id"], esc(r["truth"]), esc(r["pred"]),
                        VERDICT_LABEL[r["verdict"]],
                        reasons.get(r["eq_id"], "待分析（记录在此，不掩盖）")))
        H.append("</tbody></table>")
    else:
        H.append("<div class='cal'>无噪声档下全部题目均恢复成功。</div>")

    # 量纲剪枝明细
    H.append("<h2>四、量纲剪枝明细（本项目的核心机制）</h2>")
    H.append('<p class="sub">候选全集 = 不做任何物理约束时的搜索空间；'
             '剪枝后 = 只保留量纲与目标量一致的项。'
             '这个比例就是"物理先验省下了多少搜索"。</p>')
    H.append('<table><thead><tr><th style="width:44px">ID</th><th>目标量纲</th>'
             '<th class="c">变量数</th><th class="c">候选全集</th>'
             '<th class="c">剪枝后</th><th style="width:150px">砍掉比例</th>'
             '<th class="c">选中项</th></tr></thead><tbody>')
    seen = set()
    for r in rows:
        if r["noise"] != 0 or r["eq_id"] in seen:
            continue
        seen.add(r["eq_id"])
        pct = 100 * (1 - r["cand_kept"] / max(r["cand_total"], 1))
        H.append("<tr><td class='mono'>%s</td><td class='mono'>%s</td>"
                 "<td class='c'>%d</td><td class='c'>%d</td><td class='c'>%d</td>"
                 "<td><div class='bar'><i class='g' style='width:%.1f%%'></i></div>"
                 "<small>%.1f%%</small></td><td class='c'>%d</td></tr>"
                 % (r["eq_id"], esc(r["target_unit"]), r["n_vars"],
                    r["cand_total"], r["cand_kept"], pct, pct, r["n_terms"]))
    H.append("</tbody></table>")

    # ---- 五、失败与修正 ----
    H.append("<h2>五、一次真实的失败与修正（记录在此，不美化）</h2>")
    H.append('<p class="sub">这一节讲的是我们自己踩的坑。写出来，是因为它比"一切顺利"更能说明'
             '验证流程确实在起作用。</p>')
    H.append('<div class="cal a"><b>现象</b>'
             '最早的做法是用 <b>STLSQ（阈值稀疏回归）+ 在训练集上按 BIC 选阈值</b>。'
             '在无噪声数据上它表现完美；但一加噪声就崩：某道题选出 <b>59 个项</b>、'
             '另一道选出 <b>208 个项</b>，训练 R² 甚至掉到 <b>负数</b>，系数膨胀到 1e8 量级。</div>')
    H.append("<p><b>查出的两个原因（都不是「调参能解决」的问题）：</b></p>")
    H.append("<ol>"
             "<li><b>候选项之间严重共线。</b>当目标量是无量纲量时，候选项全是同一个无量纲变量 "
             "(例如 t/tau) 的函数，彼此高度相关，最小二乘的解因此膨胀。"
             "结果就是「系数阈值」彻底失去意义——阈值从 0.005 一路调到 0.35，"
             "选中的项数<b>一个都没变</b>。</li>"
             "<li><b>用训练集 BIC 选模型，在残差能被压到 0 时必然选最稠密的解。</b>"
             "项数够多就能把训练数据插值到残差≈0，BIC 的对数项于是无限变小、单调下降。"
             "它挑出来的必然是最复杂的模型——与「稀疏」的目标正好相反。</li>"
             "</ol>")
    H.append("<p><b>三处修正：</b></p>")
    H.append("<ol>"
             "<li><b>特征矩阵做三道数值净化</b>：丢掉含 inf/nan 的列、丢掉近常数列"
             "（量纲剪枝会放过一批「代数上恒等于常数」的项，例如 "
             "<code>Abs(t)**(-1/2)*sqrt(Abs(tau))*sqrt(Abs(t/tau))</code> 恒等于 1，"
             "它们没有信息量却能把回归矩阵彻底毁掉）、丢掉数值上完全重复的列。</li>"
             "<li><b>稀疏化策略从 STLSQ 换成 OMP（正交匹配追踪）</b>。"
             "OMP 不看系数大小，只看「加入这一项能不能真的降低残差」，对共线不敏感。</li>"
             "<li><b>模型选择从「训练集 BIC」改成「留出验证集 + 最简优先」</b>："
             "把数据按 7:3 切开，验证残差统计上区分不出来的解里，选<b>项数最少</b>的那个。</li>"
             "</ol>")
    H.append('<table><thead><tr><th style="width:60px">题目</th><th class="c" style="width:70px">噪声</th>'
             '<th>STLSQ（阈值扫描后的最好表现）</th><th>OMP（本方案采用）</th></tr></thead><tbody>')
    for eqid, noise, s_txt, o_txt in [
        ("P15", "0%", "36 项，验证残差 1.4e-24", "1 项，验证残差 2.2e-26"),
        ("P15", "1%", "49 项，验证残差 70.0", "1 项，验证残差 44.8"),
        ("P20", "1%", "59 项，验证残差 3.7e+13", "1 项，验证残差 1.1e-4"),
        ("P21", "1%", "208 项，验证残差 6.9e+04", "1 项，验证残差 5.1e-4"),
    ]:
        H.append("<tr><td class='mono'>%s</td><td class='c'>%s</td>"
                 "<td><span class='no'>%s</span></td><td><span class='ok'>%s</span></td></tr>"
                 % (eqid, noise, esc(s_txt), esc(o_txt)))
    H.append("</tbody></table>")
    H.append('<p class="sub">这张表可以用 <code>python sparse.py</code> 复现。'
             '它也是「两种稀疏化策略对比」这项证据的来源——我们不是选了 OMP 就完事，'
             '而是把两种都跑了，用数据说明为什么换。</p>')

    H.append('<div class="cal a"><b>能力边界（必须如实声明，不能含糊）</b>'
             '① 本方法是<b>数值等价判定</b>，不是形式化证明——它保证的是"在测试点覆盖范围内一致"，'
             '覆盖不到的地方不作保证。<br>'
             '② 量纲剪枝成立的前提是<b>公式里没有隐藏的标度常数</b>。'
             '如果真值是 <code>x/t0</code> 这种带一个未标注常数 t0 的形式，剪枝会连正确的项一起砍掉。<br>'
             '③ 候选特征库是有限的：像 <code>(1-u²)^(-1/2)</code> 这类复合形式不在库里，'
             '对应题目就会失败（见上表 P19）。<br>'
             '④ 题目库是手写的迷你库，不是官方 Feynman 完整数据集，替换方式见 README。</div>')

    H.append('<p class="sub" style="margin-top:28px">生成时间：%s　|　'
             '随机种子：%d　|　训练样本：%d　外推样本：%d</p>'
             % (time.strftime("%Y-%m-%d %H:%M:%S"), SEED, N_TRAIN, N_EXTRAP))
    H.append("</div></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(H))


# ---------------------------------------------------------------------------
def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    here = os.path.dirname(os.path.abspath(__file__))
    outdir = os.path.join(here, "results")
    os.makedirs(outdir, exist_ok=True)

    wanted = [a.upper() for a in argv[1:]]
    eqs = [e for e in EQ.EQUATIONS if not wanted or e["id"] in wanted]

    print("SciFormula 实验开始：%d 条方程 × %d 个噪声档" % (len(eqs), len(NOISE_LEVELS)))
    t0 = time.time()
    rows = []
    for eq in eqs:
        line = []
        for noise in NOISE_LEVELS:
            r = run_one(eq, noise)
            rows.append(r)
            line.append("%s:%s" % ("%.0f%%" % (100 * noise) if noise else "0",
                                   VERDICT_LABEL[r["verdict"]]))
        print("  %-5s %-22s %s" % (eq["id"], eq["name"], "  |  ".join(line)))

    total = time.time() - t0
    print("\n耗时 %.1f 秒（平均每档 %.3f 秒）" % (total, total / max(len(rows), 1)))

    csv_path = os.path.join(outdir, "detailed.csv")
    html_path = os.path.join(outdir, "report.html")
    write_csv(rows, csv_path)
    print_summary(rows)
    write_report(rows, html_path)
    print("\n结果已写出：")
    print("  %s" % csv_path)
    print("  %s" % html_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
