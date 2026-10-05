# -*- coding: utf-8 -*-
"""端到端测试：AGH 工具视角，从"驱动仿真"一路走到"重跑仿真式验证"。

完全走 agh_tools.run_tool（也就是 MCP 里智能体能调用的那个入口），
不直接调 pipeline —— 这样才能证明"智能体看到的"和"我们声称的"是同一条路径。
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\projects\SciFormula")
import agh_tools as T                                          # noqa: E402


def call(tool, **kw):
    r = T.run_tool(tool, kw)
    if not r.get("ok"):
        print("  X %s 报错：%s" % (tool, r.get("error")))
    return r


UNITS = {
    "rc_discharge": ({"R": "ohm", "C": "F", "V0": "V"}, "s"),
    "spring_period": ({"m": "kg", "k": "kg/s^2", "A": "m"}, "s"),
    "damped_decay": ({"m": "kg", "k": "kg/s^2", "c": "kg/s", "A": "m"}, "Hz"),
    "kepler_speed": ({"M": "kg", "a": "m", "G": "kg^-1*m^3*s^-2"}, "m^2/s^2"),
}

# 每题的"正确项"预期（用于人工核对，不是给程序看的）
EXPECT = {
    "rc_discharge": "tau = R*C",
    "spring_period": "T = 2*pi*sqrt(m/k)",
    "damped_decay": "gamma = c/(2m)",
    "kepler_speed": "eps = G*M/(2a)",
}

print("=" * 78)
ls = call("list_simulations")
print("① list_simulations → %d 个场景：%s" % (
    ls["count"], ", ".join(s["scenario"] for s in ls["scenarios"])))
print("   检查是否泄露单位：", json.dumps(ls["scenarios"][0]["parameters"][0],
                                  ensure_ascii=False))
print("   读数量是否泄露单位：", ls["scenarios"][0]["measured"]["unit"])

for sid in ("rc_discharge", "spring_period", "damped_decay", "kepler_speed"):
    print("-" * 78)
    units, tgt = UNITS[sid]

    sw = call("run_sweep", scenario=sid, n_cases=60, noise=0.005, seed=0)
    print("② run_sweep(%s) → 数据集 %s（%d 例，%d 步/例，测量法：%s）"
          % (sid, sw["dataset"], sw["n_cases"], sw["steps_per_case"], sw["measure_method"]))

    ld = call("load_dataset", name=sw["dataset"])
    print("③ load_dataset → data_id=%s | 变量=%s | 目标=%s | 单位泄露=%s | 验证方式=%s"
          % (ld["data_id"], [v["name"] for v in ld["variables"]],
             ld["target"]["name"], ld["target"]["unit"], ld.get("verification")))

    cu = call("check_units", data_id=ld["data_id"], target_unit=tgt,
              variables_units=units)
    print("④ check_units → ok=%s | warnings=%s" % (cu["ok"], cu.get("warnings") or "无"))

    bl = call("build_candidate_library", data_id=ld["data_id"], target_unit=tgt,
              variables_units=units)
    print("⑤ build_candidate_library → %s → %s（砍 %.1f%%）"
          % (bl["candidate_total"], bl["candidate_kept"], bl["prune_percent"]))

    fs = call("fit_sparse", library_id=bl["lib_id"], data_id=ld["data_id"], strategy="omp")
    print("⑥ fit_sparse → %s（%d 项，R²=%.6f）"
          % (fs["formula"], fs["n_terms"], fs["r2_fit"]))
    print("   预期正确项：%s" % EXPECT[sid])

    vf = call("verify_formula", formula=fs["formula"], data_id=ld["data_id"])
    print("⑦ verify_formula → %s | %s" % (vf["verdict"], vf["reason"][:60]))
    print("   参考解来源：%s" % vf["reference"])
    print("   区间内 nRMSE=%.2e | 外推 nRMSE=%.2e"
          % (vf["in_range"]["rmse_normalized"], vf["extrapolation"]["rmse_normalized"]))

print("=" * 78)
print("（每题都跑完整链路：驱动仿真 → 读文件 → 推断 → 剪枝 → 拟合 → 重跑仿真验证）")
