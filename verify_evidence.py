# -*- coding: utf-8 -*-
"""
verify_evidence.py —— 核对提交证据文件是否与清单一致

【为什么需要它】
评测要求「上传可供人工核查的 AGH 执行记录」，前提是这些记录**没被改过**。
实际踩过一次坑：用文档编辑器打开 `results/agh-session-unitinfer.html` 再保存，
编辑器往标签里注入了 `data-page-node-id="..."` 属性，文件字节就变了
（内容肉眼一样，但已不是官方原件）。这类改动极难肉眼发现，所以用哈希兜底。

【怎么用】
    python verify_evidence.py
输出每个文件 OK / MISSING / CHANGED，任一异常时退出码为 1。

清单文件：results/EVIDENCE.sha256（sha256sum 兼容格式，`#` 开头是注释）
"""

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "results", "EVIDENCE.sha256")


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


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


def main():
    if not os.path.exists(MANIFEST):
        print("找不到清单文件：%s" % MANIFEST)
        return 2

    rows = load_manifest(MANIFEST)
    bad = 0
    print("核对 %d 个证据文件（清单：results/EVIDENCE.sha256）\n" % len(rows))
    for expect, rel in rows:
        full = os.path.join(HERE, rel)
        if not os.path.exists(full):
            print("  MISSING  %s" % rel)
            bad += 1
            continue
        got = sha256_of(full)
        if got == expect:
            print("  OK       %s" % rel)
        else:
            print("  CHANGED  %s" % rel)
            print("           清单: %s" % expect)
            print("           实际: %s" % got)
            bad += 1

    print()
    if bad:
        print("发现 %d 处异常：文件缺失或被改动过。" % bad)
        print("若是误用编辑器保存所致，可用 `git checkout -- <文件>` 从仓库恢复到官方原件。")
        return 1
    print("全部一致：这些文件与清单记录的原件逐字节相同，可直接作为运行证据提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
