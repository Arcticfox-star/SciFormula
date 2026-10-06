# -*- coding: utf-8 -*-
"""verify_evidence.py —— 核对提交证据文件是否与清单一致

【为什么需要它】
评测要求「上传可供人工核查的 AGH 执行记录」，前提是这些记录**没被改过**。
实际踩过一次坑：用文档编辑器打开 `results/agh-session-unitinfer.html` 再保存，
编辑器往标签里注入了 `data-page-node-id="..."` 属性，文件字节就变了
（内容肉眼一样，但已不是官方原件）。这类改动极难肉眼发现，所以用哈希兜底。

【两类文件，两种严格度】（2026-10-06 重构）

  一、不可变证据（results/EVIDENCE.sha256）
      官方导出、会话轨迹摘要、评分卡、测试样例。它们的**每一个字节**都应当是
      原样的，所以按逐字节核对。唯一容错：**换行符差异**（LF ↔ CRLF）不判为异常——
      Windows 与本机 Python 写文件、各人 clone 时的 core.autocrlf 不同，
      都会让同一次内容呈现为两种字节。实测过：干净克隆后 15 个文件会「误报被改动」，
      而把 LF 换成 CRLF 重算哈希，15 个**全部**等于清单值，无一处是内容被改。
      这类情况标记为 `OK(换行)` 通过，但仍显式打印出来，不掩盖。
      → `--strict` 可关掉这个容错，要求逐字节完全一致。

  二、生成快照（results/SNAPSHOTS.sha256）
      基准报告与明细表。它们**每次重跑都会变**（`detailed*.csv` 含每条耗时列
      `seconds`、`report*.html` 页脚含墙上时钟），把它们按逐字节管起来等于宣布
      「谁重跑谁不符」。所以按**规范形式**核对：换行符统一 + 抹掉这两个已知会变的
      字段，其余（数字、公式、判定结论）仍然逐字节受保护。
      ⚠️ 本清单存的是规范形式的哈希，`sha256sum -c` 对它不适用。

【怎么用】
    python verify_evidence.py             # 两类都查
    python verify_evidence.py --strict    # 不可变证据也不容忍换行符差异
    python verify_evidence.py -v          # 列出每个文件

退出码：0 = 全部通过；1 = 有真异常（内容变了 / 文件缺失）。
"""

import argparse
import csv
import hashlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_EVIDENCE = os.path.join(HERE, "results", "EVIDENCE.sha256")
MANIFEST_SNAPSHOT = os.path.join(HERE, "results", "SNAPSHOTS.sha256")

# `report*.html` 页脚里那段会随墙上时钟变化的文字
_TIME_RE = re.compile(r"(生成时间：)[^　|]{1,40}")


def sha256_of_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_of(path, chunk=1 << 20):
    """文件字节的 sha256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def to_lf(data):
    """把 CRLF / 孤立 CR 统一成 LF。"""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def canonical(rel, data):
    """生成快照的规范形式：换行符统一 + 抹掉已知会变动的字段。

    抹掉的只有两处，且都**不承载结论**：
      · `detailed*.csv` 的 `seconds` 列 —— 每条耗时，随机器负载浮动；
      · `report*.html` 的「生成时间」 —— 墙上时钟。
    实测依据：重跑一次 experiment.py，这两个字段必变，其余全部不变。
    """
    data = to_lf(data)
    base = os.path.basename(rel)

    if base.startswith("detailed") and base.endswith(".csv"):
        text = data.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        if rows:
            head = rows[0]
            drop = set(i for i, c in enumerate(head) if c.strip() == "seconds")
            if drop:
                rows = [[c for i, c in enumerate(r) if i not in drop] for r in rows]
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        return buf.getvalue().encode("utf-8")

    if base.startswith("report") and base.endswith(".html"):
        text = _TIME_RE.sub(r"\1<每次重跑会变>", data.decode("utf-8-sig"))
        return text.encode("utf-8")

    return data


def load_manifest(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            digest, rel = parts
            rows.append((digest.lower(), rel.lstrip("*").strip()))
    return rows


def _check(rows, mode, label, verbose, strict):
    """mode: 'exact' 逐字节（容忍换行符） / 'canonical' 规范形式。"""
    bad, n_ok, n_nl = [], 0, 0
    print("\n== %s（%d 个文件）==" % (label, len(rows)))
    for expect, rel in rows:
        full = os.path.join(HERE, rel)
        if not os.path.exists(full):
            print("  MISSING  %s" % rel)
            bad.append(rel)
            continue
        raw = open(full, "rb").read()

        if mode == "canonical":
            got = sha256_of_bytes(canonical(rel, raw))
            if got == expect:
                n_ok += 1
                if verbose:
                    print("  OK       %s" % rel)
            else:
                print("  CHANGED  %s" % rel)
                print("           清单(规范形式): %s" % expect)
                print("           实际(规范形式): %s" % got)
                bad.append(rel)
            continue

        # 逐字节
        if sha256_of_bytes(raw) == expect:
            n_ok += 1
            if verbose:
                print("  OK       %s" % rel)
        elif sha256_of_bytes(to_lf(raw)) == expect:
            n_nl += 1
            if strict:
                print("  CHANGED  %s（仅换行符差异，--strict 下判为异常）" % rel)
                bad.append(rel)
            else:
                n_ok += 1
                print("  OK(换行) %s   仅换行符差异（内容逐字节相同）" % rel)
        else:
            print("  CHANGED  %s" % rel)
            print("           清单: %s" % expect)
            print("           实际: %s" % sha256_of_bytes(raw))
            bad.append(rel)

    print("  —— %d/%d 通过%s" % (n_ok, len(rows),
                                 ("，其中 %d 个仅换行符差异" % n_nl) if n_nl else ""))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description="核对证据文件与清单是否一致")
    ap.add_argument("--strict", action="store_true",
                    help="不可变证据也不容忍换行符差异（逐字节完全一致）")
    ap.add_argument("-v", "--verbose", action="store_true", help="列出每个文件")
    a = ap.parse_args(argv)

    manifests = [(MANIFEST_EVIDENCE, "exact", "一、不可变证据（逐字节核对）"),
                 (MANIFEST_SNAPSHOT, "canonical", "二、生成快照（按规范形式核对）")]
    total_bad = []
    for path, mode, label in manifests:
        if not os.path.exists(path):
            print("找不到清单文件：%s" % path)
            return 2
        rows = load_manifest(path)
        print("清单：%s" % os.path.relpath(path, HERE))
        total_bad += _check(rows, mode, label, a.verbose, a.strict)

    print()
    if total_bad:
        print("发现 %d 处异常：文件缺失或内容被改动过。" % len(total_bad))
        print("若是误用编辑器保存所致（会被注入 data-page-node-id 属性），")
        print("可用 `git checkout -- <文件>` 从仓库恢复到官方原件。")
        return 1
    print("全部一致：不可变证据逐字节相同，生成快照除耗时列与生成时间外逐字节相同，")
    print("可直接作为运行证据提交。（两类清单的差别见本脚本开头说明）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
