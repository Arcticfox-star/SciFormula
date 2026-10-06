# -*- coding: utf-8 -*-
"""重新生成 results/EVIDENCE.sha256（证据文件哈希清单）。

用法：
    python scripts/resign_evidence.py --reason "一句说明本次为什么重签"
    python scripts/resign_evidence.py --reason "..." --check      # 只核对不重签

【为什么要留一个脚本】
清单保护的是"官方导出没被编辑器动过"这件事（agh-session*.html 一旦被富文本编辑器
打开保存，就会被注入 data-page-node-id 属性、字节改变）。
但**我们自己的文档与自动生成的报告是有意更新的**——每次有意更新都要重签一次，
并把原因写进清单头部。写进头部而不是只说一句"更新了"，是为了让评审能看出
"这次变更是有原因的、有人负责的"，而不是"证据被悄悄换过"。
"""
import argparse
import hashlib
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EVIDENCE = [
    # AGH 官方导出（原件，只能用浏览器看）
    "results/agh-session.html",
    "results/agh-session-feynman.html",
    "results/agh-session-unitinfer.html",
    "results/agh-session-demo.html",
    "results/agh-session-data.html",
    "results/agh-session-data2.html",
    "results/agh-session-sim.html",
    # 会话轨迹摘要（人工整理）
    "results/agh-trace-summary.md",
    "results/agh-trace-feynman.md",
    "results/agh-trace-demo.md",
    "results/agh-trace-data.md",
    "results/agh-trace-sim.md",
    # 会话后验评分卡（posthoc_eval 自动生成）
    "results/posthoc/agh-session.md",
    "results/posthoc/agh-session-feynman.md",
    "results/posthoc/agh-session-unitinfer.md",
    "results/posthoc/agh-session-demo.md",
    "results/posthoc/agh-session-data.md",
    "results/posthoc/agh-session-data2.md",
    "results/posthoc/agh-session-sim.md",
    # 基准报告与明细
    "results/report.html",
    "results/report_feynman.html",
    "results/detailed.csv",
    "results/detailed_feynman.csv",
    "results/unit_infer_report.md",
    "results/unit_infer_answers.json",
    # 测试样例文档
    "docs/test-cases.md",
]

HEADER = """# SciFormula 证据文件校验清单（sha256）
#
# 用途：证明提交材料中的「运行证据」是官方原件、未被任何编辑器或工具改动。
#   · 行为 sha256sum 兼容格式（`sha256sum -c results/EVIDENCE.sha256` 可直接用）
#   · 也可用项目自带脚本核对：python verify_evidence.py
#
# 生成时间（北京时间）：{when}
# 本次重签原因：{reason}
# 生成方式：sha256sum results/... docs/...（AGH 官方导出 + 自动生成的报告/摘要/评分卡）
#
# ⚠️ 这七个 agh-session-*.html 是 AGH 官方导出的原件。
#    请勿用任何「文档编辑器 / 富文本编辑器」打开后保存——编辑器会注入
#    data-page-node-id 之类的属性，破坏原件字节一致性。
#    只想看内容时，用浏览器打开即可（浏览器不会写文件）。
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="（未填写原因）")
    ap.add_argument("--check", action="store_true", help="只核对，不重签")
    a = ap.parse_args(argv)

    missing = [p for p in EVIDENCE if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        print("以下文件不存在，请先补齐：")
        for p in missing:
            print("   ", p)
        return 1

    if a.check:
        rc = subprocess.call([sys.executable, "verify_evidence.py"], cwd=ROOT)
        return rc

    def h(p):
        with open(os.path.join(ROOT, p), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    lines = HEADER.format(when=time.strftime("%Y-%m-%d %H:%M:%S"), reason=a.reason)
    lines += "\n".join("%s  %s" % (h(p), p) for p in EVIDENCE) + "\n"
    with open(os.path.join(ROOT, "results", "EVIDENCE.sha256"), "w",
              encoding="utf-8") as f:
        f.write(lines)
    print("清单已重签：%d 个文件" % len(EVIDENCE))
    return subprocess.call([sys.executable, "verify_evidence.py"], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
