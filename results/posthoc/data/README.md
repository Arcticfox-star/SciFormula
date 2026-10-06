# 判定输入副本（`results/posthoc/data/`）

## 这个目录为什么存在

`posthoc_eval.py` 判定一条公式时，需要**拟合时那张设计矩阵 `X`**——
它拿 `X` 去撒点比对「模型给的公式」和「真值公式」，才能给出
`exact / up_to_constant / approx / wrong` 的判定。

这张 `X` 原本只存在于运行期目录 `.agh_runs/`，而 `.agh_runs/` 被 `.gitignore` 排除。
后果（2026-10-06 外部审计第二轮抓出，已实测复现）：

> 干净克隆后重跑评分器，**同一条会话得到不同的结论**——
> 6 份评分卡全部从 `up_to_constant` / `wrong` / `approx` 翻转成 `no_formula`；
> 而且是**静默**翻转，不报错、不提示。
> 方向固定是把「智能体成功恢复了公式」写成「智能体没给出公式」。

这与 README 第八节承诺的「结果可复现、可审计、任何一行数字都能回溯」直接冲突。
根因不是判定逻辑错，而是**判定所依赖的输入没有随证据一起提交**。

所以：把评分卡实际依赖的那几份输入**固定副本**放进本目录，随仓库一起提交。

## 里面是什么

每个 `<data_id>.npz` 是 `numpy.savez` 存的两个数组：

| 键 | 含义 |
|---|---|
| `X` | 设计矩阵（每一列一个自变量），**判定用的就是它** |
| `y` | 观测值（含噪声） |

同名 `.meta.json` 是生成时写下的边车，记录 `problem_id / n_samples / noise / seed`
与真值参考——**有了它，任何人不看本文档也能用同样的参数重新生成同一份数据来交叉核对**。

**它们不含任何"额外答案"。** 全部可以由仓库里已有的东西复现：
题库题走 `load_problem`，数据文件题走 `data/observations/*.csv` 与 `data/simulations/*.csv`。
把副本放进仓库，是为了让**判定结果不必依赖"你有没有在本地跑过那一场"**。

## 清单

| 文件 | 来源 | 参数 | 被哪份评分卡引用 | 字节 | sha256（前 16 位） |
|---|---|---|---|---|---|
| `I.39.22_csv_n200.npz` | `data/observations/I.39.22.csv`（200 行） | `load_dataset("I.39.22")` | `agh-session-data`、`agh-session-data2` | 8490 | `6cda9eee2bb20c5c` |
| `I.39.22_n200_nz0.01_s0.npz` | 官方题库 I.39.22 | `n_samples=200, noise=0.01, seed=0` | `agh-session-demo` 第 1 轮 | 8490 | `35b6f9e7f5998f1a` |
| `I.34.8_n200_nz0.01_s0.npz` | 官方题库 I.34.8 | `n_samples=200, noise=0.01, seed=0` | `agh-session-demo` 第 2 轮 | 8490 | `0bfa3fcd10e101d2` |
| `I.13.12_n200_nz0.05_s0.npz` | 官方题库 I.13.12 | `n_samples=200, noise=0.05, seed=0` | `agh-session-feynman` | 10090 | `360ebb31fdcb8d72` |
| `P03_n100_nz0.05_s0.npz` | 迷你库 P03 | `n_samples=100, noise=0.05, seed=0` | `agh-session` 第 1、2 轮 | 2890 | `eb4f3459b7c4ae00` |
| `P20_n100_nz0.05_s0.npz` | 迷你库 P20 | `n_samples=100, noise=0.05, seed=0` | `agh-session` 第 3 轮 | 2890 | `345bbc5725c2ec4c` |
| `rc_discharge_n60_csv_n60.npz` | `data/simulations/rc_discharge_n60.csv`（60 行） | `load_dataset("rc_discharge_n60")` | `agh-session-sim` | 2410 | `981d8035b4361127` |

（每份都带同名 `.meta.json`。完整哈希见 `results/EVIDENCE.sha256`——它们全部在受保护清单内。）

## 一处必须如实说明：`P20_n100_nz0.05_s0` 是重建的

其余 6 份都是从工作区的 `.agh_runs/` 原样拷来的；**唯独 P20 这一份，
本地工作区里已经找不到原文件**（`agh-session.md` 第 3 轮用的 `noise=0.05` 那份没留下）。

处理办法：用它**记录在案、且可从会话导出读出的参数**
（`n_samples=100, noise=0.05, seed=0`，见 `results/agh-session.html` 第 3 轮的
`load_problem` 调用）走**同一条代码路径**重新生成——
`pipeline.load_problem("P20", 100, 0.05, 0)`。

这样做是成立的，理由是：数据生成是**确定性的**（`sample_X` 用
`np.random.default_rng(seed)`，`add_noise` 用 `default_rng(seed+999)`，
随机数发生器固定为 PCG64、跨平台可复现），所以重新生成得到的 `X` 与会话当时被智能体
看到的 `X` **逐元素相同**；而公式判定只依赖 `X`，不依赖 `y`。
旁证：修好之后该轮判定为 `approx`（近似恢复），与评分卡里记录的
拟合 `R²=0.997995`、剪枝 `640 → 80` 自洽。

**修好之前这一轮的判定是 `no_formula`——即使公式明明被成功提取出来了。**
也就是说这条断链已经真实地污染过一份提交出去的评分卡：
`agh-session.md` 的会话得分原写「公式恢复 **1/3**」，修好后是「**2/3**」——
被吃掉的那一分，正是这条断链造成的。**它降低的恰好是项目自己的成绩。**

## 相关文件

- `posthoc_eval.py` 的 `resolve_npz()`：先查本目录，再查 `.agh_runs/`；
  两处都找不到时判 `unscorable`（详见该文件顶部与 `README.md` 第八节）
- `scripts/resign_evidence.py`：重签清单（本目录的 14 个文件都在 `EVIDENCE.sha256` 里）
- `verify_evidence.py`：核对（二进制文件不做换行符容错）
