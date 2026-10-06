# -*- coding: utf-8 -*-
"""重新生成证据清单（两份）：results/EVIDENCE.sha256 与 results/SNAPSHOTS.sha256。

用法：
    python scripts/resign_evidence.py --reason "一句说明本次为什么重签"
    python scripts/resign_evidence.py --reason "..." --check      # 只核对不重签

【为什么分两份清单】（2026-10-06，由一次仓库外部审计推动）
清单保护的是「证据没被人动过」这件事。但项目里有两种东西性质完全不同：

  ① 不可变证据 —— 官方导出、轨迹摘要、评分卡、测试样例
     ＋ 评分卡的**判定输入固定副本**（`results/posthoc/data/*.npz` 及其 `.meta.json`）。
     它们一旦生成就不该再变，所以要求**逐字节**一致（容忍换行符，见 verify_evidence.py；
     二进制文件不做这层容忍）。

  ② 生成快照 —— 基准报告与明细表。
     `detailed*.csv` 有每条耗时列、`report*.html` 页脚有生成时间，
     **任何人重跑一次都会与清单不符**。把它们按逐字节管起来，
     等于自己宣布「下次重跑就会报错」——那不是严谨，是给评审埋雷。
     所以单独一份清单，按**规范形式**（换行符统一 + 抹掉那两个字段）比对。

【重签为什么要写原因】
我们自己的文档与报告是有意更新的，每次有意更新都要重签一次，并把原因写进清单头部。
写进头部而不是只说一句「更新了」，是为了让评审能看出「这次变更是有原因的、
有人负责的」，而不是「证据被悄悄换过」。
"""
import argparse
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from verify_evidence import canonical, sha256_of_bytes  # noqa: E402

# ① 不可变证据：逐字节要求一致
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
    # 会话后验评分卡（posthoc_eval 自动生成，会话不变则内容不变）
    "results/posthoc/agh-session.md",
    "results/posthoc/agh-session-feynman.md",
    "results/posthoc/agh-session-unitinfer.md",
    "results/posthoc/agh-session-demo.md",
    "results/posthoc/agh-session-data.md",
    "results/posthoc/agh-session-data2.md",
    "results/posthoc/agh-session-sim.md",
    # 单位推断评测（无时间戳，重跑结果确定）
    "results/unit_infer_report.md",
    "results/unit_infer_answers.json",
    # 测试样例文档
    "docs/test-cases.md",
    # 评分器的判定输入固定副本（2026-10-06 新增）
    # 【为什么必须进清单】posthoc_eval 判定公式要用拟合时的设计矩阵 X，
    # 它原本只存在于被 .gitignore 排除的 .agh_runs/ → 干净克隆后 6 份评分卡
    # 会静默翻转成 "no_formula"。把副本固定进仓库后，评分卡在任何人的机器上
    # 都能复现。既然"评分卡可复现"是这里承诺的事，这几份输入就必须同样受保护——
    # 否则改一个字节就能悄悄改掉评分结论。
    "results/posthoc/data/I.39.22_csv_n200.npz",
    "results/posthoc/data/I.39.22_csv_n200.meta.json",
    "results/posthoc/data/I.39.22_n200_nz0.01_s0.npz",
    "results/posthoc/data/I.39.22_n200_nz0.01_s0.meta.json",
    "results/posthoc/data/I.34.8_n200_nz0.01_s0.npz",
    "results/posthoc/data/I.34.8_n200_nz0.01_s0.meta.json",
    "results/posthoc/data/I.13.12_n200_nz0.05_s0.npz",
    "results/posthoc/data/I.13.12_n200_nz0.05_s0.meta.json",
    "results/posthoc/data/P03_n100_nz0.05_s0.npz",
    "results/posthoc/data/P03_n100_nz0.05_s0.meta.json",
    "results/posthoc/data/P20_n100_nz0.05_s0.npz",
    "results/posthoc/data/P20_n100_nz0.05_s0.meta.json",
    "results/posthoc/data/rc_discharge_n60_csv_n60.npz",
    "results/posthoc/data/rc_discharge_n60_csv_n60.meta.json",
]

# ② 生成快照：按规范形式比对（换行符统一 + 抹掉耗时列/生成时间）
SNAPSHOTS = [
    "results/report.html",
    "results/report_feynman.html",
    "results/detailed.csv",
    "results/detailed_feynman.csv",
]

HEADER_EVIDENCE = """# SciFormula 证据文件校验清单（sha256）—— ① 不可变证据
#
# 用途：证明提交材料中的「运行证据」是官方原件、未被任何编辑器或工具改动。
#   · 行为 sha256sum 兼容格式（`sha256sum -c results/EVIDENCE.sha256` 可直接用）
#   · 也可用项目自带脚本核对：python verify_evidence.py
#   · 校验对**换行符差异**留了容错（Windows/Linux 的 LF↔CRLF 会造成同内容两种字节），
#     但会把这类情况显式打印为 `OK(换行)`，不掩盖；`--strict` 可关掉容错。
#
# 生成时间（北京时间）· **每次重签都不同，因此它不进本清单**：{when}
# 本次重签原因：{reason}
#
# ⚠️ 这七个 agh-session-*.html 是 AGH 官方导出的原件。
#    请勿用任何「文档编辑器 / 富文本编辑器」打开后保存——编辑器会注入
#    data-page-node-id 之类的属性，破坏原件字节一致性。
#    只想看内容时，用浏览器打开即可（浏览器不会写文件）。
"""

HEADER_SNAPSHOT = """# SciFormula 生成快照校验清单（sha256）—— ② 生成快照
#
# 与 EVIDENCE.sha256 的区别：这些文件**每次重跑都会变**，所以按「规范形式」比对：
#   · 换行符统一为 LF
#   · results/detailed*.csv  去掉 seconds 列（每条耗时，随机器负载浮动）
#   · results/report*.html   抹掉页脚的「生成时间」（墙上时钟）
# 除这两处之外的一切（题目、公式、各项指标、判定结论）仍逐字节受保护。
#
# ⚠️ 本清单存的是**规范形式**的哈希，因此 `sha256sum -c` 对它不适用；
#    请用 `python verify_evidence.py`（它会分别按两种严格度核对两份清单）。
#
# 生成时间（北京时间）：{when}
# 本次重签原因：{reason}
"""


def write_manifest(path, header, entries, rel_hashes):
    """写清单。newline="\\n" 是必须的：Windows 上默认会写成 CRLF，
    而仓库里存的是 LF —— 清单自身的换行符也不能各平台不一。"""
    body = "\n".join("%s  %s" % (h, p) for p, h in entries) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + "\n" + body)
    return len(entries)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="（未填写原因）")
    ap.add_argument("--check", action="store_true", help="只核对，不重签")
    a = ap.parse_args(argv)

    if a.check:
        return subprocess.call([sys.executable, "verify_evidence.py"], cwd=ROOT)

    missing = [p for p in EVIDENCE + SNAPSHOTS
               if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        print("以下文件不存在，请先补齐：")
        for p in missing:
            print("   ", p)
        return 1

    when = time.strftime("%Y-%m-%d %H:%M:%S")
    kw = dict(when=when, reason=a.reason)

    # ① 不可变证据：直接对文件字节取哈希
    # ② 生成快照：对**规范形式**取哈希（与 verify_evidence.canonical 同一实现）
    ev = [(p, sha256_of_bytes(open(os.path.join(ROOT, p), "rb").read())) for p in EVIDENCE]
    sn = [(p, sha256_of_bytes(canonical(p, open(os.path.join(ROOT, p), "rb").read())))
          for p in SNAPSHOTS]

    n1 = write_manifest(os.path.join(ROOT, "results", "EVIDENCE.sha256"),
                        HEADER_EVIDENCE.format(**kw), ev, True)
    n2 = write_manifest(os.path.join(ROOT, "results", "SNAPSHOTS.sha256"),
                        HEADER_SNAPSHOT.format(**kw), sn, False)
    print("清单已重签：① 不可变证据 %d 个，② 生成快照 %d 个" % (n1, n2))
    return subprocess.call([sys.executable, "verify_evidence.py"], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
