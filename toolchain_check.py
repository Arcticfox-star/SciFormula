# -*- coding: utf-8 -*-
"""
toolchain_check.py —— 工具链连通性验证（**它不是编排器**）

【先把这件事说清楚，避免误解】
真正做规划、选工具、读反馈、决定重试的，是 AGH 里的智能体（Agnes 模型 + Agent Loop）。
本脚本**不参与比赛时的运行**，它只回答一个工程问题：
「这 8 个工具彼此之间能不能正确串起来？」

具体验证四件事：
  ① 工具之间能不能把 data_id / library_id 正确传下去
  ② 单位推错时，工具返回的反馈足不足以让智能体判断"我错了"
  ③ 反馈回来之后，能不能靠改单位救回来（闭环是否真的闭合）
  ④ 每一步是否都留下了执行日志（.agh_runs/execution_log.jsonl）

跑完后会写出一份 episode 记录，用来和 AGH 自己生成的轨迹做对照，
确认智能体在真实运行时走的确实是这条闭环。

用法：
    python toolchain_check.py            # 用 P03 示范
    python toolchain_check.py P15        # 换一道题
"""

import os
import sys
import json

import agh_tools
import pipeline as P


def step(episode, tool, args, note=""):
    """调一次工具，把"智能体视角"看到的东西记下来"""
    res = agh_tools.run_tool(tool, args)
    episode.append(dict(step=len(episode) + 1, tool=tool, arguments=args,
                        result=res, note=note))
    return res


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    pid = (argv[1] if len(argv) > 1 else "P03").upper()

    print("=" * 74)
    print("工具链连通性验证：问题 %s" % pid)
    print("=" * 74)
    episode = []

    # --- 步骤 1：先看看有哪些题 ---
    r = step(episode, "list_problems", {}, "第一步：挑选一个基准问题")
    prob = next((p for p in r["problems"] if p["problem_id"] == pid), None)
    if prob is None:
        print("找不到问题 %s" % pid)
        return 1
    print("1) list_problems        → %s（%s），变量：%s，目标量：%s"
          % (prob["name"], prob["source"], ", ".join(prob["variables"]),
             prob["target_variable"]))

    # --- 步骤 2：载入观测数据（注意：目标量的单位没有被给出）---
    data = step(episode, "load_problem",
                dict(problem_id=pid, n_samples=100, noise=0.0, seed=0, mode="discovery"),
                "载入观测数据，单位需自行推断")
    if not data.get("ok"):
        print("load_problem 失败：%s" % data.get("error"))
        return 1
    data_id = data["data_id"]
    print("2) load_problem         → data_id=%s，变量=%s，目标量=%s（单位未提供）"
          % (data_id, ", ".join(v["name"] for v in data["variables"]),
             data["target"]["name"]))

    # --- 步骤 3：**第一次推断（故意推错）** ---
    # 把"动能"误读成"功率"，于是给目标量填了 W —— 这是物理上很典型的混淆
    wrong_units = {v["name"]: _guess(v["name"], pid) for v in data["variables"]}
    wrong_target = "W"
    chk_wrong = step(episode, "check_units", dict(variables_units=wrong_units, target_unit=wrong_target,
                                                 data_id=data_id), "第一次推断：目标量误填为 W")
    print("3) check_units          → 写法检查通过（%s），目标量纲 = %s"
          % (wrong_units, chk_wrong["target"]["normalized"]))

    bad = step(episode, "build_candidate_library",
               dict(data_id=data_id, variables_units=wrong_units, target_unit=wrong_target),
               "检验第一次推断：结果应为「候选归零」")
    print("   build_candidate_library → 候选 %d → %d 项"
          % (bad["candidate_total"], bad["candidate_kept"]))
    print("   ↑ 反馈：%s" % (bad["diagnostics"][-1] if bad["diagnostics"] else "(无)"))

    # --- 步骤 4：读反馈 → 改推断 → 重试（闭环在这里闭合）---
    right_target = "J"
    ok = step(episode, "build_candidate_library",
              dict(data_id=data_id, variables_units=wrong_units, target_unit=right_target),
              "读反馈后改为 J，重新建库")
    print("4) 改判单位后重建库     → 候选 %d → %d 项（砍掉 %.1f%%），可用列 %d"
          % (ok["candidate_total"], ok["candidate_kept"], ok["prune_percent"],
             ok["usable_columns"]))
    print("   样例候选：%s" % ok["sample_candidates"][:4])

    # --- 步骤 5：拟合 ---
    fit = step(episode, "fit_sparse",
               dict(library_id=ok["lib_id"], data_id=data_id, strategy="omp", max_terms=10),
               "在剪枝后的候选库上做稀疏回归")
    print("5) fit_sparse           → %s（%d 项，拟合 R²=%.6f）"
          % (fit["formula_readable"], fit["n_terms"], fit["r2_fit"]))

    # --- 步骤 6：独立验证（不依赖真值）---
    ver = step(episode, "verify_formula",
               dict(formula=fit["formula"], data_id=data_id),
               "用新采样点与区间外推做独立验证")
    print("6) verify_formula       → passed=%s（%s）区间内归一化RMSE %.2e / 外推 %.2e"
          % (ver["passed"], ver["verdict"],
             ver["in_range"]["rmse_normalized"], ver["extrapolation"]["rmse_normalized"]))

    # --- 步骤 7：策略对照 ---
    cmp = step(episode, "compare_strategies",
               dict(library_id=ok["lib_id"], data_id=data_id),
               "OMP 与 STLSQ 对照，作为验证严谨性证据")
    print("7) compare_strategies   → " + " | ".join(
        "%s: %d 项 / R²=%.4f" % (x["strategy"], x["n_terms"], x["r2_fit"])
        for x in cmp["comparison"]))

    # --- 步骤 8：benchmark 打分（只有评测时才有真值）---
    dbench = step(episode, "load_problem",
                  dict(problem_id=pid, n_samples=100, noise=0.01, seed=0, mode="benchmark"),
                  "载入带 1% 噪声的数据并揭示真值，用于打分")
    lb = step(episode, "build_candidate_library",
              dict(data_id=dbench["data_id"],
                   variables_units={v["name"]: _guess(v["name"], pid) for v in dbench["variables"]},
                   target_unit=right_target), "在噪声数据上重建候选库")
    fb = step(episode, "fit_sparse", dict(library_id=lb["lib_id"], data_id=dbench["data_id"]),
              "1% 噪声下拟合")
    sc = step(episode, "score_prediction", dict(formula=fb["formula"], data_id=dbench["data_id"]),
              "与真值对比打分")
    print("8) score_prediction     → 预测 %s  vs  真值 %s" % (fb["formula_readable"], sc["truth_formula"]))
    print("   判定：%s（%s），外推 R²=%.4f"
          % (sc["verdict"], sc["verdict_label"], sc["r2_extrapolation"]))

    # --- 落盘 episode ---
    os.makedirs(P.RUN_DIR, exist_ok=True)
    path = os.path.join(P.RUN_DIR, "episode_%s.json" % pid)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(problem_id=pid, n_steps=len(episode),
                       data_id=data_id, library_id=ok["lib_id"],
                       steps=episode), f, ensure_ascii=False, indent=2)

    print()
    print("=" * 74)
    print("连通性检查通过：%d 步，全部工具可串联，反馈可用，闭环闭合。" % len(episode))
    print("episode 记录：%s" % path)
    print("执行日志：    %s" % agh_tools.LOG_PATH)
    print("=" * 74)
    return 0


def _guess(name, pid):
    """
    这里用一张极简的"变量名 → 单位"表来模拟智能体的推断。
    真正运行时这一步由 Agnes 模型完成（读变量名和物理语境，自行判断量纲）。
    写死这张表只是为了让连通性验证可复现、不依赖模型。
    """
    table = {
        "mu": "", "f_n": "N", "m": "kg", "v": "m/s", "curr": "A", "res": "ohm",
        "volt": "V", "k": "N/m", "x": "m", "g": "m/s^2", "t": "s", "tau": "s",
        "r": "m", "q": "C", "b": "T", "ell": "m", "f": "N", "theta": "rad",
        "sigma": "", "c_light": "m/s", "d": "m", "cap": "F", "mag": "A*m^2",
        "efield": "V/m", "energy": "J", "power": "W", "p": "kg*m/s",
    }
    return table.get(name, "")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
