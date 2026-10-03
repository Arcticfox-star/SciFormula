# data/ —— 官方基准数据集

## 文件

| 文件 | 是什么 |
|---|---|
| `FeynmanEquations.csv` | 官方 Feynman 符号回归基准的 **100 条方程**（本项目实际使用） |
| `BonusEquations.csv` | 同一来源的 Bonus 数据集（更难，含更多复合函数与特殊函数），暂未使用，留档 |

## 来源

- **原始出处**：AI Feynman 论文（Silviu-Marian Udrescu & Max Tegmark, 2020,
  *AI Feynman: a Physics-Inspired Method for Symbolic Regression*, arXiv:1905.11481）
  随论文发布的 *Feynman Symbolic Regression Database*。
- **论文给的下载地址**：`https://space.mit.edu/home/tegmark/aifeynman.html`
  —— **该页面现已 404**（站点搬迁）。这是 2026-10-03 实测确认的。
- **本项目实际取用的副本**：
  `https://github.com/chrishaider/FeynmanEquations-Python`
  路径 `generate/src/FeynmanEquations.csv`。
  该仓库在说明中写明其 CSV 于 2022-04-01 取自上述 MIT 页面，并记录了两处
  与原版的行内错误（变量个数与区间个数对不上），我们的解析器会显式校验变量个数，
  不依赖这些字段。
- **取用日期**：2026-10-03。

## 数据集的字段

一行一条方程：

```
Filename, Number, Output, Formula, # variables,
v1_name, v1_low, v1_high, v2_name, v2_low, v2_high, ... v10_name, v10_low, v10_high
```

- `Formula` 用人类写法，与 Python 数学表达式基本一致（`**` 表示乘方，函数有
  `sqrt / exp / sin / cos / tanh / ln / arcsin`）。我们在解析时把 `ln` 映射成 `log`。
- 采样区间都很窄（多数是 1..5 这种），是这份基准的原始设定，我们不做修改。

## ⚠️ 这份数据集**不含物理单位**

这是接入本项目时最关键的一点。数据集只给了公式、变量名、采样区间。

而本项目的核心机制是"用量纲先验缩小搜索空间"，**没有单位表就完全跑不起来**。
现有公开工具的做法是要使用者自己手动填，且大多只对少数题目给出示范。

因此我们自己补建了一张完整的单位表：**`../feynman_units.py`**（按变量名建全局表 +
少量逐方程覆盖），并用自动化检查保证它自洽（加法同量纲、三角函数参数无量纲）。
检查结果：**100 条全部通过**。

这张表是**我们自己的贡献**，不是数据集的组成部分；凡是做了判断而非查表的地方，
都在 `feynman_units.py` 末尾的 NOTES 里逐条交代。

## 关于许可与使用

该数据集源自《费曼物理学讲义》中的公式，以基准数据集形式公开发布供学术评测使用。
本项目仅在学术竞赛与学习场景中使用，并在文档中标注了完整出处。
需要正式发表或商业使用时，请自行核实许可条款。
