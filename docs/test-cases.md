# 测试样例（正常 / 边界 / 失败）

按提交规程要求，测试样例分**正常、边界、失败**三类。每条样例给出：
输入 → 期望行为 → 实际结果 → 一条可复制执行的复现命令。

所有命令都在项目根目录 `D:\projects\SciFormula` 下执行（Windows PowerShell / Git Bash 均可）；
`python` 指项目虚拟环境 `D:\projects\SciFormula\.venv\Scripts\python.exe`。

---

## 一、正常样例

### N1　无噪声下精确恢复动能公式（完整闭环）

- **输入**：P03（动能），discovery 模式，100 样本，无噪声，seed=0。
  智能体只知道变量名 `m`、`v` 和观测值，不知道真值公式与单位。
- **期望**：推断出 `m=kg, v=m/s, E=J`，量纲剪枝后回归出 `0.5*(m*v**2)`，判定 `exact`。
- **实际**：候选 40 项 → 剪枝后 1 项（正是正确答案）；`0.5*(m*v**(2))`，判定 `exact`。
- **复现**（工具链路径，与 AGH 智能体调用的是同一份实现）：

```bash
python agh_tools.py call load_problem --json '{"problem_id":"P03","n_samples":100,"noise":0.0,"seed":0}'
python agh_tools.py call check_units --json '{"variables_units":{"m":"kg","v":"m/s"},"target_unit":"J","data_id":"P03_n100_nz0_s0"}'
python agh_tools.py call build_candidate_library --json '{"data_id":"P03_n100_nz0_s0","variables_units":{"m":"kg","v":"m/s"},"target_unit":"J"}'
python agh_tools.py call fit_sparse --json '{"library_id":"<上一步返回的 lib_id>","data_id":"P03_n100_nz0_s0"}'
```

（`lib_id` 由上一步返回，复制其真实值；同一输入必然得到同一 `lib_id`。）

- **AGH 内的完整证据**：`results/agh-session.html`（会话 161ea3fb，330 条事件）、
  `results/agh-trace-summary.md`（整理稿）。

### N2　带噪声下的稳定恢复

- **输入**：P03，1% 与 2% 高斯噪声，其余同上。
- **期望**：公式结构不变，系数在噪声水平内漂移，判定 `up_to_constant`，外推 R² ≥ 0.99。
- **实际**：1% → `0.5002*(m*v**(2))`；2% → `0.5005*(m*v**(2))`；均 `up_to_constant`，通过双重检验。
- **复现**：批量跑迷你库并查看逐行明细：

```bash
python experiment.py P03
type results\detailed.csv
```

### N3　官方 Feynman 基准题可用

- **输入**：`list_problems` 指定 `library="feynman"`。
- **期望**：返回官方 100 题清单；官方题编号（如 `I.12.11`）可直接 `load_problem`，且 discovery 模式不泄漏真值。
- **实际**：count=100；`I.12.11` 变量 `q, Ef, B, v, theta` 正常载入，返回中无 `true_formula` 字段。
- **复现**：

```bash
python agh_tools.py call list_problems --json '{"library":"feynman"}'
python agh_tools.py call load_problem --json '{"problem_id":"I.12.11","n_samples":100,"noise":0.01}'
```

---

### N4　仿真实验台的完整闭环（AGH 驱动仿真 → 发现定律 → 重跑仿真验证）

- **输入**：仿真实验台 `rc_discharge`（RC 放电），跑 60 个参数点、读数带 0.5% 噪声。
  智能体只知道参数名（R、C、V0）与读数列名（tau），**不知道任何单位、也不知道公式**。
- **期望**：推断出 `R=ohm、C=F、V0=V、tau=s`；量纲剪枝后只剩正确的那一项；
  回归出 `≈ R*C`；`verify_formula` 换参数**当场重跑仿真**后判定 passed。
- **实际**：候选 124 项 → 剪枝后 **1 项**（正是正确答案）；拟合
  `1.0015396090098025*(C*R)`，R² = 0.999971；验证 passed，
  参考解来源显示为「仿真实验台当场重跑测得的读数（参数点全部留出、不与训练集重叠）」。
- **复现**（与 AGH 智能体调用的是同一份实现）：

```bash
python agh_tools.py call list_simulations
python agh_tools.py call run_sweep --json '{"scenario":"rc_discharge","n_cases":60,"noise":0.005,"seed":0}'
python agh_tools.py call load_dataset --json '{"name":"rc_discharge_n60"}'
python agh_tools.py call check_units --json '{"data_id":"rc_discharge_n60_csv_n60","target_unit":"s","variables_units":{"R":"ohm","C":"F","V0":"V"}}'
python agh_tools.py call build_candidate_library --json '{"data_id":"rc_discharge_n60_csv_n60","target_unit":"s","variables_units":{"R":"ohm","C":"F","V0":"V"}}'
python agh_tools.py call fit_sparse --json '{"library_id":"LIB_ID","data_id":"rc_discharge_n60_csv_n60"}'
python agh_tools.py call verify_formula --json '{"data_id":"rc_discharge_n60_csv_n60","formula":"R*C"}'
```

（`LIB_ID` 换成上一步返回的真实值；同一输入必然得到同一 `LIB_ID`。）

- **仿真器自身的精度证据**：`python simulator.py --selftest`
  → 12 个参数点上「测量值 vs 闭式解」最大相对误差 **1.5e-12**。
- **其余三个场景**（弹簧振子 / 阻尼振子 / 圆轨道天体）同样一次通过，
  结果表见 `README.md` 的「数值仿真实验台」一节。

## 二、边界样例

### B1　单位写法不合法

- **输入**：`check_units` 提交 `{"m":"kg","v":"米每秒"}`（中文单位不在登记表里）。
- **期望**：不崩溃，逐变量返回解析结果，指明哪个单位写法不认识、该怎么改。
- **实际**：`v` 的 `parsed_ok=false`，报 `"未知单位：'米每秒'（请先在 dims.py 的 UNITS 里登记）"`；
  `m` 与目标 `J` 正常解析（`J` 归一化为 `kg*m^2/s^2`）。
- **复现**：

```bash
python agh_tools.py call check_units --json '{"variables_units":{"m":"kg","v":"米每秒"},"target_unit":"J"}'
```

### B2　单位写法合法但物理推断错误

- **输入**：P03 数据，但把速度推成 `kg`（合法写法、错误物理）。
- **期望**：`check_units` 拦不住（它只查写法）；但 `build_candidate_library` 的量纲剪枝后
  候选归零，并给出诊断：这组单位让目标量无法由任何候选拼出，应重新审读变量物理含义。
- **实际**：`candidate_kept=0`，诊断原文见工具返回的 `diagnostics` 字段。
- **复现**：

```bash
python agh_tools.py call build_candidate_library --json '{"data_id":"P03_n100_nz0_s0","variables_units":{"m":"kg","v":"kg"},"target_unit":"J"}'
```

（这条样例展示的是**分层校验**的设计：写法检查与物理检查由两个工具分别负责。）

### B3　AGH 会话中的工具调用被人工拒绝

- **输入**：AGH 网页工作台运行中，第一次 `check_units` 的批准卡被点「拒绝」。
- **期望**：智能体不假装继续，如实上报已完成到哪一步、卡在哪一步，等待重新授权。
- **实际**：会话轨迹 seq 73 记录了 `approval rejected` 后的诚实汇报；重新批准后一次性走完全程。
- **证据**（此样例依赖 AGH 运行时，不能离线复现）：`results/agh-trace-summary.md` 第 2 节。

---

## 三、失败样例

失败样例分三种，全部如实保留在报告里，不做遮掩。

### F1　特征库表达力边界：P19 相对论因子

- **输入**：P19，真值 `gamma = 1/sqrt(1-v**2/c_light**2)`。
- **期望（如实声明的能力边界）**：候选特征库（幂次核 × 函数因子）拼不出
  `1/sqrt(1-x)` 型嵌套结构，无法恢复；判定为结构错误。
- **实际**：无噪声档 `wrong`，噪声档用别的项凑出 `up_to_constant`——但外推检验不及格，
  双重检验判为不通过。这是迷你库 21 题中唯一失败的一题（20/21）。
- **复现**：

```bash
python experiment.py P19
```

### F2　历史 bug 回归测试：负动能截距公式被验证器拦截

- **背景**：2026-10-03 的 AGH 会话中，模型曾在 P03 回归出 `E = 0.5017*m*v**2 - 1.204`，
  归一化 RMSE 极小、被判 passed，但在真值 E≈0.14 处预测出 **-1.07（负动能）**，相对误差 880%。
  根因：候选库每一项都过量纲检查，唯独截距没有——它是唯一能绕开约束的自由参数。
- **输入**：把这条历史公式直接交给 `verify_formula`（P03 数据，5% 噪声）。
- **期望**：判 `implausible`，并说明原因（目标量有量纲时加性常数必须为 0）。
- **实际**：`passed=false, verdict=implausible`，reason 原文：
  「区间内与外推区间 违反符号合理性：观测值在整段区间上同号，而公式预测出了相反的符号
  （预测最小值 -0.8962，观测最小值 0.3068）……建议检查是否混入了非法的加性常数」。
- **复现**：

```bash
python agh_tools.py call load_problem --json '{"problem_id":"P03","n_samples":100,"noise":0.05,"seed":0}'
python agh_tools.py call verify_formula --json '{"formula":"0.5017*(m*v**(2)) - 1.204","data_id":"P03_n100_nz0.05_s0"}'
```

（`data_id` 用 `load_problem` 返回的真实值。）

### F3　官方基准中的候选库无解题（量纲系统判定「拼不出」）

- **输入**：Feynman I.9.18（万有引力，9 个变量）。
- **期望**：变量超过组合上限（`MULTI_VAR_LIMIT`）时只生成到两变量乘积，嵌套分母拼不出；
  量纲自洽的候选为 0，判 `no_candidates`，跳过拟合。
- **实际**：`cand_kept=0`，判定 `no_candidates`（官方 100 题中共 15 题属此类，
  占比与失败构成详见 `results/report_feynman.html`）。
- **复现**：

```bash
python experiment.py I.9.18
```

（官方题直接跑需要先设环境变量 `SCIFORMULA_LIBRARY=feynman`，或用上面的工具链路径指定题目编号。）

---

### F4　仿真台的目标量落在候选库表达能力之外（不是单位错）

- **输入**：仿真实验台 `kepler_speed`，但把仪器读数当作**环绕速度 v**（目标量纲 m/s），
  而正确读数是**比动能 ε**（量纲 m²/s²）。
- **期望**：工具明确报告「目标量的量纲无法由任何候选核拼出」，
  并给出**两种**可能原因（单位推错 / 幂次集合限制），而不是只归因于"单位错"。
- **实际**：`cand_kept = 0`；`build_candidate_library` 的提示指出
  「本库单变量允许 1/2 与 -1/2 次幂，但**三变量及以上的乘积只允许整数幂**，
  所以 sqrt(G*M/a) 表达不出来，而 G*M/a、G*M/(2*a) 这类整数幂形式可以」，
  并建议改为测目标量的平方或等价形式（例如测比动能）。
- **复现**（这条数据本身是合法的，只是换了个目标量来触发边界）：

```bash
python agh_tools.py call load_dataset --json '{"name":"kepler_speed_n60"}'
python agh_tools.py call build_candidate_library --json '{"data_id":"kepler_speed_n60_csv_n60","target_unit":"m/s","variables_units":{"M":"kg","a":"m","G":"kg^-1*m^3*s^-2"}}'
```

- **说明**：这不是 bug，而是**能力边界**——候选库的幂次集合决定了哪些定律能被表达。
  同一条数据把目标量换成比动能（m²/s²）即可跑通，
  拟合出 `0.4997*(G*M*1/a)`（真值 `G*M/(2a)`），验证 passed。

## 附：三类样例的统计口径

| 类别 | 数量 | 出处 |
|---|---|---|
| 正常 | 迷你库 20/21 通过双重检验；官方基准三档 54/54/57（确定性修复后口径，见 README）；**仿真实验台 4 个场景 4/4 一次通过** | `results/report.html`、`results/report_feynman.html`、README「数值仿真实验台」一节 |
| 边界 | 2 条可离线复现 + 1 条 AGH 会话证据 | 本文 B1~B3 |
| 失败 | P19（表达力边界）、负动能拦截（回归测试）、官方 15 题无解、仿真台目标量超表达力 | 本文 F1~F4 |
