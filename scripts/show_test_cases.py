# -*- coding: utf-8 -*-
"""show_test_cases.py —— 把三条需要截图的测试样例逐条跑出来，方便截屏存档。

【为什么需要它】
赛事要求测试样例「各含输入、预期、实际、截图」。前四项在 docs/test-cases.md 里
已经写明，差的是截图。截图必须是自己终端上真跑出来的——所以这个脚本的作用是
**把命令和输出摆好**，让截图一次成功，不用来回翻文档抄命令。

它只做两件事：把命令原文打出来、把真实输出打出来。不加工、不美化、不重排。

【用法】
    python scripts/show_test_cases.py N1     # 正常样例：动能公式完整闭环
    python scripts/show_test_cases.py B1     # 边界样例：非法单位写法
    python scripts/show_test_cases.py F1     # 失败样例：过拟合解被外推检验拦下
    python scripts/show_test_cases.py all    # 依次跑三条

截图存到 docs/screenshots/，文件名分别为 N1.png / B1.png / F1.png
（该目录的 README.md 里有完整说明）。
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def pick_python():
    """挑一个**装了依赖**的解释器来跑子进程。

    为什么必须挑：本脚本要拉起 agh_tools.py / experiment.py，它们 import numpy。
    如果调用方是系统 Python（且没装 numpy），子进程会直接
    `ModuleNotFoundError: No module named 'numpy'` —— 报错发生在子进程里，
    看起来像"项目坏了"，其实只是用错了解释器。**这条是用户实测打回来的**：
    在 `PS D:\\projects\\SciFormula> python scripts/show_test_cases.py N1` 下
    三条样例全部 numpy 报错。所以这里自动优先用项目自带的虚拟环境。
    """
    cands = [
        os.path.join(ROOT, ".venv", "Scripts", "python.exe"),   # Windows venv
        os.path.join(ROOT, ".venv", "bin", "python"),           # Unix venv
        os.path.join(ROOT, "venv", "Scripts", "python.exe"),
    ]
    for p in cands:
        if os.path.exists(p):
            return p
    return sys.executable


PY = pick_python()
COMPACT = False          # 由 --compact 打开；只列关键字段，便于截成一屏（见 call）


def preflight():
    """开跑前先确认解释器能用。不检查的话，报错会以一堆 traceback 的形式散在各步里。"""
    r = subprocess.run([PY, "-c", "import numpy, sympy"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 0:
        return True
    print("!" * 78)
    print("当前用的解释器缺依赖，三条样例都会失败：")
    print("    %s" % PY)
    print("    %s" % (r.stderr or "").strip().splitlines()[-1])
    print()
    print("项目依赖装在自带的虚拟环境里。两种解法，任选一种：")
    print("  ① 直接用虚拟环境的解释器跑本脚本（推荐）：")
    print('       .venv\\Scripts\\python.exe scripts\\show_test_cases.py N1')
    print("  ② 给当前解释器装上依赖：")
    print("       python -m pip install -r requirements.txt")
    print("!" * 78)
    return False


def py_label():
    """命令回显该写什么解释器名。

    若脚本内部换了别的解释器（见 pick_python），就**如实显示换成了谁**——
    截图里的「输入」必须和「实际执行」一致，否则读者照抄截图里的命令会跑不通。
    """
    if PY == sys.executable:
        return "python"
    try:
        rel = os.path.relpath(PY, ROOT)
    except Exception:
        rel = PY
    if not rel.startswith(".."):
        return rel.replace("/", "\\")          # 项目内的虚拟环境，用相对路径更好读
    return PY


def compact_lines(tool, stdout_text):
    """把一条工具返回压成「关键字段」列表（值原样照抄）。

    为什么需要它：完整返回动辄上百行（如 check_units 会列出 22 个已登记单位），
    一屏装不下，而测试样例的截图需要「输入 + 实际结果」同框。
    紧凑模式把每条命令压到十几行，一屏可截。
    这里做的只是**字段提取**——提取出来的值全部来自真实返回，
    不含任何人工改写（输出里也会标明这是节选）。
    """
    try:
        d = json.loads(stdout_text)
    except Exception:
        return None
    out = []
    if tool == "load_problem":
        out.append(("ok", d.get("ok")))
        out.append(("data_id", d.get("data_id")))
        out.append(("mode", d.get("mode")))
        out.append(("变量名", ", ".join(v.get("name", "?") for v in d.get("variables", []))))
        out.append(("target", d.get("target")))          # unit 为 null —— 不给单位
        out.append(("note", d.get("note")))
    elif tool == "check_units":
        out.append(("ok", d.get("ok")))
        for v in d.get("variables", []):
            out.append(("%s = %s" % (v.get("name"), v.get("input")),
                        {k: v[k] for k in ("parsed_ok", "normalized", "error") if k in v}))
        if d.get("target"):
            out.append(("target = %s" % d["target"].get("input"),
                        {k: d["target"][k] for k in ("parsed_ok", "normalized")
                         if k in d["target"]}))
        out.append(("warnings", d.get("warnings")))
    elif tool == "build_candidate_library":
        for k in ("ok", "lib_id", "data_id", "target_dimension",
                  "candidate_total", "candidate_kept", "prune_percent",
                  "usable_columns", "sample_candidates"):
            if k in d:
                out.append((k, d[k]))
    elif tool == "fit_sparse":
        for k in ("ok", "lib_id", "strategy", "formula", "formula_readable",
                  "n_terms", "selected_terms", "r2_fit", "rss"):
            if k in d:
                out.append((k, d[k]))
    else:
        return None
    return out


def banner(title, subtitle=""):
    print("\n" + "#" * 78)
    print("#  " + title)
    if subtitle:
        for line in subtitle.splitlines():
            print("#  " + line)
    print("#" * 78)


def call(tool, payload, note="", expect_fail=False):
    """跑一条 agh_tools.py call，把命令与真实输出都打出来。

    expect_fail：该样例期望工具返回「不 ok」时置 True —— 退出码非 0 是**正确行为**
    （工具如实报告问题），不是程序出错。截图里要让人看懂这一点，否则会被误读成故障。
    COMPACT：只列关键字段（见 compact_lines），便于截成一屏。
    """
    js = json.dumps(payload, ensure_ascii=False)
    print("\n$ %s agh_tools.py call %s --json '%s'" % (py_label(), tool, js))
    if note:
        print("  （%s）" % note)
    print("-" * 78)
    r = subprocess.run([PY, "agh_tools.py", "call", tool, "--json", js],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    shown = False
    if COMPACT:
        rows = compact_lines(tool, r.stdout or "")
        if rows is not None:
            print("（紧凑模式：只列关键字段，取值原样来自真实返回；完整返回请去掉 --compact）")
            for k, v in rows:
                print("  %-20s %s" % (k, json.dumps(v, ensure_ascii=False)))
            shown = True
    if not shown:
        print((r.stdout or "").rstrip())
    if r.returncode != 0:
        if expect_fail:
            print("[退出码 %d —— 本样例期望就是如此：工具如实报告了问题，不是程序出错]"
                  % r.returncode)
        else:
            print("[退出码 %d]" % r.returncode)
        if r.stderr:
            print(r.stderr.rstrip())
    return r


def run_script(args, note=""):
    """跑一个普通脚本，把命令与真实输出都打出来。"""
    print("\n$ %s %s" % (py_label(), " ".join(args)))
    if note:
        print("  （%s）" % note)
    print("-" * 78)
    r = subprocess.run([PY] + args, cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    print((r.stdout or "").rstrip())
    if r.returncode != 0:
        print("[退出码 %d]" % r.returncode)
        if r.stderr:
            print(r.stderr.rstrip())
    return r


def n1():
    banner("N1　无噪声下精确恢复动能公式（正常样例）",
           "期望：推断出 m=kg, v=m/s, E=J；量纲剪枝后回归出 0.5*(m*v**2)，判定 exact")
    call("load_problem",
         {"problem_id": "P03", "n_samples": 100, "noise": 0.0, "seed": 0},
         "注意 target.unit 为 null —— 不给单位，要智能体自己推")
    call("check_units",
         {"variables_units": {"m": "kg", "v": "m/s"},
          "target_unit": "J", "data_id": "P03_n100_nz0_s0"},
         "单位写法都合法")
    r = call("build_candidate_library",
             {"data_id": "P03_n100_nz0_s0",
              "variables_units": {"m": "kg", "v": "m/s"}, "target_unit": "J"},
             "40 项候选 → 剪枝后只剩 1 项，正是正确答案")
    # lib_id 由本步返回，动态取出来给下一步用（它其实是确定的）
    lib_id = ""
    try:
        lib_id = json.loads(r.stdout).get("lib_id", "")
    except Exception:
        pass
    call("fit_sparse",
         {"library_id": lib_id, "data_id": "P03_n100_nz0_s0"},
         "拟合出 0.5*(m*v**(2))，判定 exact")
    print("\n>> 截图存为 docs/screenshots/N1.png")


def b1():
    banner("B1　单位写法不合法（边界样例）",
           "期望：不崩溃，逐变量返回解析结果，指明哪个写法不认识、该怎么改")
    call("check_units",
         {"variables_units": {"m": "kg", "v": "米每秒"}, "target_unit": "J"},
         "v 用的是中文『米每秒』——不在登记表里；本样例期望工具报告解析失败",
         expect_fail=True)
    print("\n>> 截图存为 docs/screenshots/B1.png")


def f1():
    banner("F1　过拟合解被外推检验拦下：P19 相对论因子（失败样例）",
           "期望：训练集上拟合极好（R²=1.0）但结构错误，被外推检验拦住\n"
           "看点：训练 R² 1.00000 对外推 R² -5.20476")
    run_script(["experiment.py", "P19"],
               "注意：这条会重写 results/ 下的报告与明细，截完图想还原就 git checkout -- results/")
    print("\n>> 截图存为 docs/screenshots/F1.png")


CASES = {"N1": n1, "B1": b1, "F1": f1}


def main(argv=None):
    global COMPACT
    ap = argparse.ArgumentParser(description="把需要截图的测试样例逐条跑出来")
    ap.add_argument("case", choices=["N1", "B1", "F1", "all"],
                    help="要跑哪条；all 依次跑三条")
    ap.add_argument("--compact", action="store_true",
                    help="只列关键字段（值仍取自真实返回），便于截成一屏")
    a = ap.parse_args(argv)
    COMPACT = a.compact

    # 开跑前先确认解释器可用——否则三条样例会各自抛一堆 numpy traceback，
    # 看起来像项目坏了，实际只是解释器选错（用户实测踩过）。
    if not preflight():
        return 1
    if py_label() != "python":
        print("（子进程改用 %s —— 它的依赖是齐的；命令回显里也会如实写成它）\n"
              % py_label())

    names = ["N1", "B1", "F1"] if a.case == "all" else [a.case]
    for n in names:
        CASES[n]()

    print("\n" + "=" * 78)
    if a.case == "all":
        print("三条跑完。")
    else:
        print("%s 跑完。" % a.case)
    print("把截图放进 docs/screenshots/（N1.png / B1.png / F1.png），")
    print("docs/test-cases.md 里对应条目下的『截图』一行即可指向它们。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
