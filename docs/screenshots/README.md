# 测试样例截图

> 赛事 4.2 对测试样例的要求是「**各含输入、预期、实际、截图**」。
> `docs/test-cases.md` 里前四项都已写明，本目录放**截图**。

## 需要哪三张

按「三类各一条」补，覆盖正常 / 边界 / 失败三种形态：

| 文件 | 对应样例 | 截的是什么 |
|---|---|---|
| `N1.png` | N1 无噪声下精确恢复动能公式 | 四条 `agh_tools.py call` 命令的输出，重点是最后 `fit_sparse` 返回的 `0.5*(m*v**(2))` 与判定 |
| `B1.png` | B1 单位写法不合法 | `check_units` 对 `"米每秒"` 返回 `parsed_ok: false` 与那条错误说明 |
| `F1.png` | F1 过拟合解被外推检验拦下 | `experiment.py P19` 输出的表格，重点是训练 R² 1.00000 / 外推 R² **-5.20476** / 判定「错误」 |

**为什么只补三条而不是十二条**：其余样例的「复现命令」本身就是可复核的实证——
评委照着跑一遍即可，成本为零。三条足以证明"三类形态都有截图、都真跑过"。

## 怎么截（终端）

仓库根目录下，逐条跑：

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

三条命令的输出都可**直接照抄**（`lib_id` 那串是真实值，不是占位符）。

截图前把终端字号调大一点（能看清即可），截完另存为 PNG，
文件名按上表放在本目录。

> 三条都能用 `scripts/show_test_cases.py` 一条一条打出来：
> `python scripts/show_test_cases.py N1`（把 N1 换成 B1 / F1）。

## 几条不要做的事

1. **不要把 `results/agh-session*.html` 用文档编辑器打开后截图**。
   那些是 AGH 官方导出的证据原件，编辑器保存会注入 `data-page-node-id` 之类属性、
   破坏字节一致性。用**浏览器**打开看是安全的（浏览器不写文件）。
2. **不要用「重新排版渲染」冒充终端截图**。
   截图的价值在于它是"真跑过"的直接痕迹；重画一张看起来很像的图，
   性质就从证据变成了演示材料——本项目不做这种事。
3. **F1 那条会重写 `results/` 下的报告与明细**（它们属「生成快照」文件）。
   截完图想还原工作区：`git checkout -- results/`。

## 放好之后

截图进仓库后，`docs/test-cases.md` 对应条目下的 `- **截图**：` 一行就指向真实文件了。
如需把截图也纳入完整性保护，可以在 `scripts/resign_evidence.py` 的清单里登记——
但图片属于**提交材料**而非**运行证据原件**，不登记也不影响合规。
