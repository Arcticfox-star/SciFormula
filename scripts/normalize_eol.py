# -*- coding: utf-8 -*-
"""normalize_eol.py —— 把工作区里**文本类**文件的换行符统一成 LF。

【为什么需要】
仓库带一份 sha256 证据清单，用来证明「官方导出没被改过」。而 Windows 上
Python 写文件会把 `\\n` 写成 `\\r\\n`（`csv.writer` 的默认行尾也是 `\\r\\n`），
于是「工作区字节」与「仓库里的字节」长期不一致 —— 干净克隆后跑校验就会误报。
本脚本把工作区统一成 LF，让三处字节一致：工作区 = 仓库 = 任何人的克隆。

【做法】
以 `git ls-files` 为准（只处理已跟踪文件），按 `.gitattributes` 判定每个文件
是否为文本：文本 → 换行符统一为 LF；`-text`/binary 的（AGH 官方导出）→ 跳过，
绝不动它的字节。

用法：
    python scripts/normalize_eol.py            # 就地规范（打印改动的文件）
    python scripts/normalize_eol.py --check    # 只报告需要规范的文件，不改
"""
import argparse
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, nul=False):
    out = subprocess.run(["git"] + list(args), cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    if nul:
        # `-z` 用 NUL 分隔且路径原样输出（不加引号、不把中文转义成 \346\216\245）
        return out.stdout.split("\0")
    return out.stdout


def tracked_text_files():
    """返回已跟踪的文本文件（相对路径）。用 `git check-attr text` 判定，
    这样与 .gitattributes 保持单一信息源，不在这里另写一套规则。

    两个必须注意的细节（都实测遇到过）：
      · 用 `-z`（NUL 分隔）：默认按行读时，路径会带上行尾的回车，导致匹配不到文件；
      · `-z` 同时让路径原样输出，中文文件名不会被转义。

    `text` 的取值语义：
        set        → 明确是文本，规范
        auto       → 由 git 按内容判断（查头部有没有 NUL 字节）
        unset      → 明确非文本（如 AGH 官方导出），**绝不碰**
        unspecified→ 无规则，视同非文本，不动
    """
    files = [f for f in git("ls-files", "-z", nul=True) if f.strip()]
    out = []
    for i in range(0, len(files), 200):
        batch = files[i:i + 200]
        res = subprocess.run(["git", "check-attr", "--stdin", "-z", "text"],
                             cwd=ROOT, input="\0".join(batch) + "\0",
                             capture_output=True, text=True, encoding="utf-8").stdout
        parts = res.split("\0")
        # -z 的输出是连续三元组：<path>\0text\0<value>\0
        for j in range(0, len(parts) - 2, 3):
            path, attr, val = parts[j], parts[j + 1], parts[j + 2]
            if attr != "text":
                continue
            if val == "set":
                out.append(path)
            elif val == "auto":
                full = os.path.join(ROOT, path)
                if os.path.exists(full) and b"\x00" not in open(full, "rb").read(8000):
                    out.append(path)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告，不改")
    a = ap.parse_args(argv)

    changed = checked = 0
    for rel in tracked_text_files():
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            continue
        raw = open(full, "rb").read()
        checked += 1
        if b"\r\n" not in raw and b"\r" not in raw:
            continue
        lf = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        print("  %-46s CRLF %d → LF %d" % (rel, raw.count(b"\r\n"), lf.count(b"\n") - lf.count(b"\r\n")))
        changed += 1
        if not a.check:
            with open(full, "wb") as f:
                f.write(lf)

    print("\n检查 %d 个文本文件；%s %d 个。" % (
        checked, "需要规范" if a.check else "已规范", changed))
    if a.check and changed:
        print("执行 python scripts/normalize_eol.py 就地规范。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
