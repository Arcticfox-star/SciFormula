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
PY = sys.executable


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
    """
    js = json.dumps(payload, ensure_ascii=False)
    print("\n$ python agh_tools.py call %s --json '%s'" % (tool, js))
    if note:
        print("  （%s）" % note)
    print("-" * 78)
    r = subprocess.run([PY, "agh_tools.py", "call", tool, "--json", js],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
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
    print("\n$ python %s" % " ".join(args))
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
    ap = argparse.ArgumentParser(description="把需要截图的测试样例逐条跑出来")
    ap.add_argument("case", choices=["N1", "B1", "F1", "all"],
                    help="要跑哪条；all 依次跑三条")
    a = ap.parse_args(argv)

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
