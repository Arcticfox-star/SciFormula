# -*- coding: utf-8 -*-
"""
report_notes.py —— 生成型报告的「人工标注区」保留机制

问题
----
项目里有四个脚本会**整文件重写**自己生成的报告：

    unit_infer_eval.py            → results/unit_infer_report.md
    exp_verify_ab.py              → results/verify_ab_report.md
    scripts/score_baseline.py     → results/baseline_report.md
    scripts/score_gp.py           → results/baseline_gp_report.md

原来它们各自 `open(path, "w")` 直接覆盖，于是**手工补在报告里的溯源信息会被重跑抹掉**——
而且不报错、不提示。被抹掉的可能正是「这份评分出自哪个会话、答案从哪来」这类
唯一的证据链，所以这不只是排版问题。

做法
----
每份产物末尾放一对标记，标记之间的内容由人写、脚本只负责原样搬运：

    <!-- NOTES:BEGIN -->
    ...人写的内容，重跑时被保留...
    <!-- NOTES:END -->

写入流程变成：**读旧文件 → 抠出标注区 → 拼新正文 + 原标注区 → 写回**。
如果旧文件里没有这对标记（例如首次生成，或旧版产物），就自动补一个空标注区，
不让流程中断。同时不再对正文做任何「猜人工行」的启发式处理——
**只认标记**，规则单一、可预期。

参考
----
`results/agh-trace-summary.md` 早就用文字声明了「只有末尾的分析段是人写的」，
但没有机制保证这一点。本模块就是把那句话变成机制。
"""

import os
import re

BEGIN = "<!-- NOTES:BEGIN -->"
END = "<!-- NOTES:END -->"

# 首次生成时写入标注区的占位提示。人一旦往里面写东西，这段提示就该被替换掉。
_PLACEHOLDER = (
    "（这一区由人手工书写，脚本重跑时会**原样保留**。"
    "可用来记录产物来历、作答会话、复现环境等正文之外的信息。）"
)

_NOTES_RE = re.compile(
    re.escape(BEGIN) + r"(.*?)" + re.escape(END), re.DOTALL
)


def read_notes(path):
    """读出旧文件里的标注区内容；没有标记则返回 None（区别于「标记区为空」）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
    except (OSError, UnicodeDecodeError):
        # 读不动就当没有——宁可丢标注区，也不能让生成流程挂掉。
        return None
    m = _NOTES_RE.search(old)
    return m.group(1) if m else None


def notes_block(notes=None):
    """拼出标注区文本。notes=None 表示旧文件里没有标注区，给一条占位提示。"""
    body = _PLACEHOLDER if notes is None else notes.strip("\n")
    return "%s\n%s\n%s\n" % (BEGIN, body, END)


def write_report(path, body, header_note=None):
    """
    写入报告：新正文 + 保留下来的标注区。

    参数
        path        产物路径（会被覆盖，但标注区内容先被救出来）
        body        脚本生成的正文（**不要**自己带标注区）
        header_note 可选的顶部说明行；默认会写明「标注区外的内容重跑即被覆盖」

    返回
        是否从旧文件里成功保留了标注区（便于调用方打印一行提示）。
    """
    kept = read_notes(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    head = header_note if header_note is not None else (
        "> 本文件由脚本自动生成：**标注区（NOTES）之外的内容每次重跑都会被覆盖**，"
        "请把手工补充的信息写在标注区内。\n"
    )

    parts = []
    if head:
        parts.append(head.rstrip("\n"))
        parts.append("")
    parts.append(body.rstrip("\n"))
    parts.append("")
    parts.append(notes_block(kept))

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts))

    return kept is not None


def has_notes(path):
    """给自检用：产物里是否带标注区标记。"""
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        t = f.read()
    return BEGIN in t and END in t


if __name__ == "__main__":
    # 自检：造一个临时产物，写两次，确认标注区被保留、正文被更新
    import tempfile

    d = tempfile.mkdtemp(prefix="report_notes_")
    p = os.path.join(d, "demo.md")

    write_report(p, "# 正文 v1\n\n- 指标：1/2")
    assert has_notes(p), "首次写入应自动补出标注区"
    with open(p, "r", encoding="utf-8") as f:
        t1 = f.read()
    assert "指标：1/2" in t1

    # 模拟人往标注区里写东西
    t2 = t1.replace(_PLACEHOLDER, "- 作答会话：`agnes-3.0-flash`（会话 `abc-123`）")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(t2)

    keep = write_report(p, "# 正文 v2\n\n- 指标：2/2")
    with open(p, "r", encoding="utf-8") as f:
        t3 = f.read()
    assert keep is True, "第二次写入应报告「保留了标注区」"
    assert "指标：2/2" in t3, "正文应被更新"
    assert "指标：1/2" not in t3, "旧正文应被替换"
    assert "agnes-3.0-flash" in t3, "★ 人工标注必须被保留"
    assert "abc-123" in t3, "★ 人工标注必须被保留"

    # 再跑一次（幂等：标注区不该被重复包裹或加长）
    write_report(p, "# 正文 v3\n\n- 指标：3/3")
    with open(p, "r", encoding="utf-8") as f:
        t4 = f.read()
    assert t4.count(BEGIN) == 1 and t4.count(END) == 1, "标注区不应重复"
    assert "agnes-3.0-flash" in t4 and "指标：3/3" in t4, "多次重跑后两者都要在"

    print("report_notes.py 自检通过：标注区在重跑中被保留，正文被正确替换，多次重跑幂等。")
