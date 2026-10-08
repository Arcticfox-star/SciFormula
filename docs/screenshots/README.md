# 测试样例截图

> 赛事 4.2 对测试样例的要求是「**各含输入、预期、实际、截图**」。
> `docs/test-cases.md` 里前四项都已写明，本目录放**截图**。

## 本目录现有内容

| 文件 | 对应样例 | 截的是什么 |
|---|---|---|
| `N1-1.png` … `N1-5.png` | N1 无噪声下精确恢复动能公式 | 四条 `agh_tools.py call` 的执行过程，按顺序分 5 屏 |
| `B1-1.png`、`B1-2.png` | B1 单位写法不合法 | `check_units` 对 `"米每秒"` 返回 `parsed_ok: false` 与错误说明 |
| `F1.png` | F1 过拟合解被外推检验拦下 | `experiment.py P19` 表格：训练 R² `1.00000` / 外推 R² `-5.20476` / 判定「错误」 |

N1 的关键结果在 `N1-4.png` 与 `N1-5.png`：
前者是 `candidate_kept` 从 40 剪到 **1**，后者是 `formula_readable = "0.5*(m*v**(2))"`、`r2_fit = 1.0`。

**为什么只给三类各一条**：其余样例的「复现命令」本身就是可复核的实证——
评委照着跑一遍即可，成本为零。三条足以证明"三类形态都有截图、都真跑过"。

## 这些截图是怎么来的（两点如实说明）

1. **是真实终端截图，未做任何修改**：直接在运行终端里截屏另存，
   没有裁剪内容、没有重排版、没有拼接。
2. **N1 分 5 屏而不是拼成一张**：完整输出约 120 行，一屏装不下。
   曾尝试按像素重叠自动拼接，实测**不可靠**——各次截图的窗口位置/大小略有差异，
   同一段文字在不同图里的像素坐标不同（逐像素比对得到的偏移量互不一致）。
   硬拼会有内容错位风险，而截图一旦错位就不再是可信的证据。
   **宁可交 5 张连贯的原图，也不交一张拼出来的长图。**

## 如果想重截（例如换了机器或想截得更紧凑）

**先确认解释器装了依赖**：跑 `python -c "import numpy, sympy"`，不报错才继续。
若报 `ModuleNotFoundError: No module named 'numpy'`，说明用错了 `python`——
项目依赖在 `.venv/` 里，用 `.venv\Scripts\python.exe`（Windows）
或 `.venv/bin/python`（Linux/macOS）。

```bash
# 完整输出（会跨多屏）
.venv\Scripts\python.exe scripts/show_test_cases.py N1

# 紧凑输出：只列关键字段（值仍取自真实返回），一屏左右可截
.venv\Scripts\python.exe scripts/show_test_cases.py N1 --compact
```

`--compact` 做的事只是**字段提取**——提取出来的值全部来自真实返回，
不含任何人工改写；输出里也会标明这是节选。它适合"一图一例"的紧凑截法。

手工逐条跑也行（仓库根目录下）：

```bash
# N1
python agh_tools.py call load_problem --json '{"problem_id":"P03","n_samples":100,"noise":0.0,"seed":0}'
python agh_tools.py call check_units --json '{"variables_units":{"m":"kg","v":"m/s"},"target_unit":"J","data_id":"P03_n100_nz0_s0"}'
python agh_tools.py call build_candidate_library --json '{"data_id":"P03_n100_nz0_s0","variables_units":{"m":"kg","v":"m/s"},"target_unit":"J"}'
python agh_tools.py call fit_sparse --json '{"library_id":"lib_1df518136eb2fa42","data_id":"P03_n100_nz0_s0"}'
```

```bash
# B1
python agh_tools.py call check_units --json '{"variables_units":{"m":"kg","v":"米每秒"},"target_unit":"J"}'
```

```bash
# F1
python experiment.py P19
```

命令都可**直接照抄**（`lib_id` 是真实值，不是占位符）。

> 关于 B1 的 `[退出码 1]`：那条样例的期望结果**就是**解析失败，
> 工具如实报告问题才返回非 0 —— **不是命令挂了**。`show_test_cases.py` 会把这句说明
> 一并打出来，截图里带着它更好懂。

## 几条不要做的事

1. **不要拼接多屏截图**——理由见上：像素对齐不可靠，错位的证据比不完整的更糟。
2. **不要把 `results/agh-session*.html` 用文档编辑器打开后截图**。
   那些是 AGH 官方导出的证据原件，编辑器保存会注入 `data-page-node-id` 之类属性、
   破坏字节一致性。用**浏览器**打开看是安全的（浏览器不写文件）。
3. **不要用「重新排版渲染」冒充终端截图**。
   截图的价值在于它是"真跑过"的直接痕迹；重画一张看起来很像的图，
   性质就从证据变成了演示材料——本项目不做这种事。
4. **F1 那条会重写 `results/` 下的报告与明细**（属「生成快照」文件）。
   截完图想还原工作区：`git checkout -- results/`。

## 是否纳入完整性清单

本目录的截图属**提交材料**而非**运行证据原件**（不是程序产出、由人拍摄），
因此**不登记**进 `results/EVIDENCE.sha256`。它们可以被更清晰的截图合法替换，
不涉及"原件是否被改动"这个问题。
