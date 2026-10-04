# -*- coding: utf-8 -*-
"""
agh_tools.py —— 把 pipeline 的原子能力注册成「AGH 可以调用的工具」

【这个文件的角色】
AGH 的规程要求：作品必须以 AGH 作为智能体运行与执行底座，并经由 AGH 完成
「任务规划 → 能力调用 → 反馈处理与结果验证」至少三个连续步骤。

在这个分工里：
    AGH（Agnes 模型 + Agent Loop） = 大脑，负责规划、选工具、读反馈、决定重试
    本文件注册的工具               = 能力，只负责"给我参数，我算完告诉你结果"
    pipeline.py                    = 能力的实现

所以本文件**绝不替智能体做决定**：不自动选策略、不自动判定通过、不自动重试。
它只做三件事：
    ① 声明每个工具的名字、用途、参数 schema（智能体据此决定调不调）
    ② 校验参数并把调用转发给 pipeline
    ③ 把每次调用写进执行日志（.agh_runs/execution_log.jsonl），作为可核查的过程证据

【两种接线方式，同一个注册表】
  方式一（推荐）：mcp_server.py 把它暴露成 MCP 服务器，在 AGH 里执行
                 agnes.mjs mcp add sciformula --stdio <python> --arg <mcp_server.py>
  方式二（备选）：AGH 后端插件通过命令行调用本文件的 CLI 入口
                 python agh_tools.py call fit_sparse --json '{"library_id":"...","data_id":"..."}'

【命令行用法（也是本地自检入口）】
    python agh_tools.py list                      # 列出工具名
    python agh_tools.py schema                    # 输出完整的 MCP 工具定义（JSON）
    python agh_tools.py call list_problems
    python agh_tools.py call load_problem --json '{"problem_id":"P03"}'
    echo '{"problem_id":"P03"}' | python agh_tools.py call load_problem
"""

import os
import sys
import json
import time

import datasets as DS
import pipeline as P


# ---------------------------------------------------------------------------
# 工具定义：name / description / inputSchema
# description 是智能体唯一的判断依据，所以要写清「什么时候该用」和
# 「拿到这个结果之后该怎么想」，而不只是"这个工具做什么"。
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    dict(
        name="list_datasets",
        description=(
            "列出**磁盘上可用的观测数据文件**（数据集名、文件路径、行数、列名）。"
            "这是接入外部数据的地方：每个 CSV 就是一次真实/仿真的采样记录，"
            "只含变量取值与目标量观测值——**不含真值公式、也不含任何单位**。"
            "先调用它看看有什么数据，再用 load_dataset 载入。"
            "提示：数据文件是可替换的，换成你自己的测量 CSV 也能走同一套流程。"),
        inputSchema=dict(type="object", properties=dict(), additionalProperties=False),
        handler=lambda a: DS.list_datasets(),
    ),
    dict(
        name="load_dataset",
        description=(
            "载入一个数据文件（CSV），返回变量名、采样范围、观测值统计与预览，"
            "以及后续步骤要用的 data_id。"
            "与 load_problem 一样**不返回真值公式与单位**——目标量的量纲需要你根据"
            "列名与物理语境自行推断；若该数据集对应官方题库中的题目，"
            "独立验证（verify_formula）可用，否则只能用留出法自行检验。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                name=dict(type="string",
                          description="list_datasets 返回的数据集名（可带 .csv 后缀），例如 I.39.22"),
            ),
            required=["name"], additionalProperties=False),
        handler=lambda a: DS.load_dataset(a["name"]),
    ),
    dict(
        name="list_problems",
        description=(
            "列出可用于评测的物理定律基准问题（编号、名称、目标量名、变量名）。"
            "第一步调用它来挑选一个 problem_id。注意：本工具不返回任何物理单位，"
            "目标量的量纲需要你根据问题名称与变量名自行推断。"
            "可选两套题库：library 不传 = 教学迷你库（21 题，P01~P21，变量少、适合演示流程）；"
            "library=\"feynman\" = 官方 Feynman 符号回归基准（100 题，编号如 I.12.11，"
            "变量多、难度高，正式成绩在这套库上测出）。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                library=dict(type="string", enum=["mini", "feynman"], default="mini",
                             description="题库：mini=21 题迷你库（默认）；feynman=官方 100 题基准"),
            ),
            additionalProperties=False),
        handler=lambda a: P.list_problems(a.get("library")),
    ),

    dict(
        name="load_problem",
        description=(
            "载入某个问题的观测数据（自变量采样值与带噪声的目标量观测值），"
            "返回变量名、采样区间、观测值统计与预览。"
            "discovery 模式（默认）不会返回真值公式与各单位——这正是要你推断的部分；"
            "benchmark 模式会一并返回真值，仅用于评测打分。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                problem_id=dict(type="string",
                                description="list_problems 返回的问题编号，例如 P03（迷你库）或 I.12.11（官方库）"),
                n_samples=dict(type="integer", default=100, description="采样点数，默认 100"),
                noise=dict(type="number", default=0.0,
                           description="噪声水平：加到真值上的高斯噪声标准差 = noise × 真值标准差。0 表示无噪声"),
                seed=dict(type="integer", default=0, description="随机种子，决定采到哪些点"),
                mode=dict(type="string", enum=["discovery", "benchmark"], default="discovery",
                          description="discovery=只给观测数据；benchmark=额外给真值用于打分"),
            ),
            required=["problem_id"], additionalProperties=False),
        handler=lambda a: P.load_problem(a["problem_id"], a.get("n_samples", 100),
                                         a.get("noise", 0.0), a.get("seed", 0),
                                         a.get("mode", P.DISCOVERY)),
    ),

    dict(
        name="check_units",
        description=(
            "校验一组单位推断的「写法」是否正确（每个单位串能否解析、变量是否与数据对得上），"
            "并返回可用单位名清单与写法提示。每个单位都会回显**无歧义的指数形式**"
            "（如 kg/m/s 会显示为 kg^1*m^-1*s^-1），并对多斜杠写法给出左结合警告——"
            "务必核对这一项，因为它能暴露"
            "「量纲看着自洽、实际推错」的写法问题：含多个斜杠的单位按左结合解析，"
            "kg/m/s 是 kg/(m*s)（动量应当写 kg*m/s）。"
            "注意它查不出物理错误——一个能解析但在物理上写错的单位会通过这里，"
            "然后在 build_candidate_library 里表现为候选被剪到几乎没有。"
            "所以流程上应当：先 check_units 过写法，再用 build_candidate_library 验物理。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                variables_units=dict(type="object", additionalProperties=dict(type="string"),
                                     description="变量名到单位字符串的映射，例如 {\"m\":\"kg\",\"v\":\"m/s\"}"),
                target_unit=dict(type="string",
                                 description="你推断的目标量单位，例如 J、N、m/s、空字符串表示无量纲"),
                data_id=dict(type="string",
                             description="可选。传入后还会检查变量集合能否与数据对齐"),
            ),
            required=["variables_units", "target_unit"], additionalProperties=False),
        handler=lambda a: P.check_units(a["variables_units"], a["target_unit"],
                                        a.get("data_id")),
    ),

    dict(
        name="build_candidate_library",
        description=(
            "按给定的单位推断构造候选公式库，并做量纲一致性剪枝——只保留量纲与目标量相同的候选项。"
            "返回候选总数、剪枝后保留数、剪枝比例。"
            "「这一步同时是单位推断的检验器」：如果保留数为 0，说明这组单位让目标量无法被任何候选拼出，"
            "几乎一定是单位推错了，需要重新推断后再试；如果保留数很少（<5）也要警惕。"
            "返回的 library_id 供 fit_sparse 与 compare_strategies 使用。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                data_id=dict(type="string", description="load_problem 返回的 data_id"),
                variables_units=dict(type="object", additionalProperties=dict(type="string"),
                                     description="变量名到单位字符串的映射"),
                target_unit=dict(type="string", description="目标量的单位"),
                max_terms=dict(type="integer", default=250,
                               description="剪枝后保留项的上限，超出则按表达式最简优先截断，默认 250"),
            ),
            required=["data_id", "variables_units", "target_unit"], additionalProperties=False),
        handler=lambda a: P.build_candidates(a["data_id"], a["variables_units"],
                                             a["target_unit"], a.get("max_terms", 250)),
    ),

    dict(
        name="fit_sparse",
        description=(
            "从候选库中挑出少数几项并配上系数，拼成一条公式。"
            "strategy=omp（默认，本项目主路线，按「加入这一项能否真正降低残差」贪心挑选，对共线稳健）；"
            "strategy=stlsq（经典阈值法，在候选项高度共线时会失效，仅作对照）。"
            "返回 formula（全精度，直接传给 verify_formula）与 formula_readable（便于阅读的写法）。"
            "若 n_terms 顶到 max_terms 上限，说明候选表达力可能不足，应结合 verify_formula 的结果决定是否调整。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                library_id=dict(type="string", description="build_candidate_library 返回的 library_id"),
                data_id=dict(type="string", description="对应的 data_id"),
                strategy=dict(type="string", enum=["omp", "stlsq"], default="omp"),
                max_terms=dict(type="integer", default=10, description="最多允许选中的项数，默认 10"),
                threshold=dict(type="number", default=0.05,
                               description="仅 strategy=stlsq 时使用：系数阈值"),
            ),
            required=["library_id", "data_id"], additionalProperties=False),
        handler=lambda a: P.fit_sparse(a["library_id"], a["data_id"],
                                       a.get("strategy", "omp"), a.get("max_terms", 10),
                                       a.get("threshold", 0.05)),
    ),

    dict(
        name="verify_formula",
        description=(
            "独立验证一条公式，不需要你提供真值：在训练区间内用新种子重采一批点，"
            "再把区间向外扩张 30% 采一批，分别计算归一化 RMSE，并做一道符号合理性检查。"
            "判定结果有四种：invalid（公式在测试点上无法求值）、implausible（违反符号合理性）、"
            "underfit（区间内都不稳）、overfit（区间内好但外推崩了，说明只是拟合数据而不是找到定律）、"
            "passed。"
            "两种常见误判要小心：① 归一化 RMSE 由大量级点主导，会掩盖小量级处的严重错误——"
            "所以一并返回 max_rel_err_small（真值最小那 5% 区间内的最大相对误差），"
            "这个数字大就说明公式在小量级处不可信；② 符号合理性检查会直接拦下"
            "「观测恒正、公式却预测出负值」这类与数据直接矛盾的解。"
            "若返回 overfit / underfit / implausible，应由你决定下一步："
            "改单位、换策略、调整 max_terms。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                formula=dict(type="string", description="fit_sparse 返回的 formula（全精度版本）"),
                data_id=dict(type="string", description="对应的 data_id"),
                expand=dict(type="number", default=0.3, description="外推区间向外扩张的比例，默认 0.3"),
                n_samples=dict(type="integer", default=100, description="每批验证采样点数"),
                tol_in_range=dict(type="number", default=0.05, description="区间内归一化 RMSE 通过阈值"),
                tol_extrap=dict(type="number", default=0.20, description="外推归一化 RMSE 通过阈值"),
            ),
            required=["formula", "data_id"], additionalProperties=False),
        handler=lambda a: P.verify_formula(a["formula"], a["data_id"],
                                           a.get("expand", 0.3), a.get("n_samples", 100),
                                           tol_in_range=a.get("tol_in_range", 0.05),
                                           tol_extrap=a.get("tol_extrap", 0.20)),
    ),

    dict(
        name="compare_strategies",
        description=(
            "在同一份数据和同一个候选库上，把 omp 与 stlsq 两种稀疏化策略都跑一遍并对照，"
            "返回各自的项数、R²、残差与公式。用于回答「为什么选 OMP 而不是 STLSQ」这类质疑，"
            "也是「验证严谨性」的直接证据。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                library_id=dict(type="string", description="build_candidate_library 返回的 library_id"),
                data_id=dict(type="string", description="对应的 data_id"),
                max_terms=dict(type="integer", default=10, description="OMP 的项数上限"),
                threshold=dict(type="number", default=0.05, description="STLSQ 的系数阈值"),
            ),
            required=["library_id", "data_id"], additionalProperties=False),
        handler=lambda a: P.compare_strategies(a["library_id"], a["data_id"],
                                               a.get("max_terms", 10), a.get("threshold", 0.05)),
    ),

    dict(
        name="score_prediction",
        description=(
            "把预测公式与真值公式对比打分：exact（完全一致）/ up_to_constant（只差常数因子，算恢复成功）"
            "/ approx（形式不同但 NMSE 很小）/ wrong。同时给出与真值对比的外推 R²。"
            "「只用于基准评测」：data_id 必须是 mode=benchmark 载入的数据，discovery 模式下会拒绝。"),
        inputSchema=dict(
            type="object",
            properties=dict(
                formula=dict(type="string", description="待打分的公式"),
                data_id=dict(type="string", description="benchmark 模式载入的 data_id"),
            ),
            required=["formula", "data_id"], additionalProperties=False),
        handler=lambda a: P.score_prediction(a["formula"], a["data_id"]),
    ),
]

_BY_NAME = {d["name"]: d for d in TOOL_DEFS}


# ---------------------------------------------------------------------------
# 执行日志：每次工具调用留一行，作为 AGH 轨迹之外的第二份过程证据
# ---------------------------------------------------------------------------
LOG_PATH = os.path.join(P.RUN_DIR, "execution_log.jsonl")


def _json_safe(obj):
    """
    把工具返回值净化成**严格合法的 JSON**结构。这不是洁癖，是修过的一个真 bug：

    Python 的 json.dumps 默认会写出 `NaN` / `Infinity` —— 那是 Python 的扩展，
    **标准 JSON 不允许**。Python 自己读得进去，所以本地自检全绿；
    但浏览器与 Node 的 JSON.parse 会直接抛错。实测后果：
        AGH 侧看到「服务端 3ms 执行成功」，客户端却 60 秒后报
        `MCP error -32001: Request timed out` —— 因为响应根本没被解析成功。
    触发点：CSV 数据集的 meta 里 noise 是 NaN，而 verify_formula 会回显 noise_level。

    顺带一并处理的还有 numpy 标量/数组（json 也不认），以及兜底把不可序列化的
    对象转成字符串——宁可少给信息，也不能让工具因为序列化问题整条失败。
    """
    import math
    try:
        import numpy as np
    except Exception:                                   # numpy 不在也不该崩
        np = None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None     # NaN / ±Inf → null
    if np is not None:
        if isinstance(obj, np.generic):
            return _json_safe(obj.item())
        if isinstance(obj, np.ndarray):
            return _json_safe(obj.tolist())
    return str(obj)


def _summarize(name, res):
    """从结果里挑出最关键的数字，便于在日志里一眼看清"""
    if not isinstance(res, dict):
        return None
    if name == "load_problem":
        return dict(data_id=res.get("data_id"), target=res.get("target", {}).get("name"))
    if name == "check_units":
        return dict(ok=res.get("ok"), target_dimension=res.get("target", {}).get("normalized"))
    if name == "build_candidate_library":
        return dict(candidate_total=res.get("candidate_total"),
                    candidate_kept=res.get("candidate_kept"),
                    prune_percent=res.get("prune_percent"))
    if name == "fit_sparse":
        return dict(n_terms=res.get("n_terms"), formula=res.get("formula_readable"),
                    r2_fit=res.get("r2_fit"))
    if name == "verify_formula":
        return dict(passed=res.get("passed"), verdict=res.get("verdict"))
    if name == "compare_strategies":
        return dict(rows=[dict(s=x["strategy"], k=x["n_terms"]) for x in res.get("comparison", [])])
    if name == "score_prediction":
        return dict(verdict=res.get("verdict"), r2_extrapolation=res.get("r2_extrapolation"))
    return None


def _log(tool, args, res, ms, error=None):
    try:
        os.makedirs(P.RUN_DIR, exist_ok=True)
        rec = dict(ts=time.strftime("%Y-%m-%d %H:%M:%S"), tool=tool, args=args,
                   ok=bool(isinstance(res, dict) and res.get("ok", True)) and error is None,
                   ms=round(ms, 1), summary=_summarize(tool, res))
        if error:
            rec["error"] = error
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass          # 日志失败不允许影响工具本身的返回


# ---------------------------------------------------------------------------
# 调用入口：校验参数 → 转发 pipeline → 记录日志
# ---------------------------------------------------------------------------
def run_tool(name, args):
    """
    执行一次工具调用。永远返回一个 dict，绝不抛异常——
    错误要变成智能体可读的反馈，它才知道下一步怎么改。
    """
    t0 = time.time()
    args = args or {}

    if name not in _BY_NAME:
        res = dict(ok=False, error="没有名为 %r 的工具" % name,
                   available=sorted(_BY_NAME.keys()))
        _log(name, args, res, 0.0, error="unknown_tool")
        return res

    spec = _BY_NAME[name]
    props = spec["inputSchema"]["properties"]

    unknown = [k for k in args if k not in props]
    if unknown:
        res = dict(ok=False,
                   error="传了不认识的参数：%s" % ", ".join(unknown),
                   accepted_parameters=sorted(props.keys()))
        _log(name, args, res, 0.0, error="bad_arguments")
        return res

    missing = [k for k in spec["inputSchema"].get("required", []) if k not in args]
    if missing:
        res = dict(ok=False, error="缺少必需参数：%s" % ", ".join(missing),
                   required=spec["inputSchema"]["required"])
        _log(name, args, res, 0.0, error="missing_arguments")
        return res

    try:
        res = spec["handler"](args)
    except FileNotFoundError as exc:
        res = dict(ok=False, error=str(exc),
                   hint="检查 data_id / library_id 是否与前面步骤返回的一致。")
    except KeyError as exc:
        res = dict(ok=False, error="找不到对象：%s" % exc,
                   hint="检查 problem_id 或变量名是否拼写正确。")
    except Exception as exc:                       # noqa: BLE001 —— 必须兜住，否则智能体会拿到崩溃
        res = dict(ok=False, error="%s: %s" % (type(exc).__name__, exc))

    # 净化：NaN / Inf → null，numpy 类型 → 原生类型（见 _json_safe 的说明）
    res = _json_safe(res)

    ms = (time.time() - t0) * 1000.0
    if not isinstance(res, dict):
        res = dict(ok=True, result=res)
    _log(name, args, res, ms, error=None if res.get("ok", True) else "tool_returned_error")
    return res


def tool_definitions():
    """输出 MCP 形状的工具定义列表（name/description/inputSchema）"""
    return [dict(name=d["name"], description=d["description"], inputSchema=d["inputSchema"])
            for d in TOOL_DEFS]


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = argv[1] if len(argv) > 1 else "list"

    if cmd == "list":
        for d in TOOL_DEFS:
            print("%-26s %s" % (d["name"], d["description"].split("。")[0]))
        return 0

    if cmd == "schema":
        print(json.dumps(tool_definitions(), ensure_ascii=False, indent=2))
        return 0

    if cmd == "log":
        if not os.path.exists(LOG_PATH):
            print("(还没有执行日志)")
            return 0
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                print(line.rstrip())
        return 0

    if cmd == "call":
        if len(argv) < 3:
            print("用法：python agh_tools.py call <工具名> [--json '{...}']", file=sys.stderr)
            return 2
        name = argv[2]
        raw = None
        if "--json" in argv:
            i = argv.index("--json")
            raw = argv[i + 1] if i + 1 < len(argv) else None
        if raw is None and not sys.stdin.isatty():
            raw = sys.stdin.read().strip() or None
        try:
            args = json.loads(raw) if raw else {}
        except Exception as exc:
            print(json.dumps(dict(ok=False, error="参数不是合法 JSON：%s" % exc),
                             ensure_ascii=False), file=sys.stderr)
            return 2
        res = run_tool(name, args)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok", True) else 1

    print("未知子命令 %r。可用：list / schema / call / log" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
