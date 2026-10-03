# SciFormula 与 AGH 的接入说明

> 这份文档回答一个问题：**"通知要求项目全部内容在 AGH 上做出来，我这些 Python 代码到底能不能用？"**
> 结论：能用，而且这正是 AGH 设计好的用法；但**原样用不行**——必须把"决策"留给 AGH，
> 把"计算"留给 Python。下面讲清楚为什么、以及具体怎么接。

---

## 一、先看规则原文，再看官方怎么实现

### 1.1 规程怎么说的

赛事官网《参赛规程》第三章 3.1 条原文：

> 参赛作品**必须以 Agnes Harness（AGH）作为智能体运行与执行底座**，
> 并经由 AGH 完成**至少三个连续步骤（任务规划、能力调用、反馈处理与结果验证）**。

再对照 4.2 条「运行证据」的要求：

> AGH 执行记录、**≥1 条工具 / 设备调用链**、Agnes 模型参与核心任务证据、关键配置与专业验证结果。

请注意这两处的措辞：它要求的是 **AGH 作为运行与执行底座、并提供工具调用链**。
它**没有**要求"所有算法都必须用某种语言、在 AGH 进程内实现"。

### 1.2 AGH 官方定义的"给智能体加能力"方式

官方仓库 README 与文档 `docs/README.md` 写得很明确，给智能体加业务能力有两条路：

| 路径 | 文档 | 形态 |
|---|---|---|
| **后端插件 Backend plugin** | `docs/develop/backend.md` | Node + Cordis，用 `agnes.registerTool({name, description, parameters, execute})` 注册 |
| **MCP 服务** | `docs/guide/mcp.md` | 接入一个已有的 MCP 工具服务，`agnes.mjs mcp add ... --stdio <可执行文件>` |

README 原话：**"In AGH, a business capability is a plugin: register a tool through a backend plugin or connect an existing service through MCP."**

也就是说：**把算法挂成外部能力，是 AGH 设计内的一等公民，不是绕过规则。**
plugin / MCP 在 AGH 架构里就属于 **Plugins 层**（后端工具、服务、Skill、hook、MCP 连接），
和 Agent Loop（模型/工具执行、任务状态、事件记录）是两个正交的层。

### 1.3 所以真正的分界线在哪里

分界不在"代码写在哪个语言里"，而在**决策发生在哪一层**：

| 必须发生在 AGH 里（占 20% 闭环分） | 可以放在外部(我们的 Python)里 |
|---|---|
| 任务规划：决定先干什么、目标量的单位该怎么猜 | 采样、构造候选、回归、求值这些纯计算 |
| 能力调用：决定调哪个工具、传什么参数 | 工具内部的实现细节 |
| 反馈处理：看到"候选被剪到 0 项"后判断"我单位推错了" | 返回结构化的诊断信息供智能体判断 |
| 结果验证：决定这条公式算不算通过、要不要重试 | 计算 RMSE / R² 等指标数值 |

**这就是为什么原来那版代码不能直接用。**

---

## 二、原来那版代码的问题（已修复）

改造前 `experiment.py` 里有个 `run_one()`，它把整条流程写死在一个函数里：

```
读数据 → 建候选库 → 量纲剪枝 → 稀疏回归 → 等价判定 → 外推检验 → 出结果
```

如果把它原样包成一个工具交给 AGH，AGH 能做的只有一件事：**调用一次、打印结果**。
三个连续步骤里的"任务规划""反馈处理""结果验证"全都发生在 Python 内部，
AGH 只是一个执行了一条命令的外壳。评审看 AGH 轨迹，只会看到一次 bash 调用——
这正好撞在"AGH 与模型执行闭环"这一项（20%）上。

**修法：把流程切成原子能力，把决策权交出去。** 现在分成三层：

```
┌──────────────────────────────────────────────────────────────┐
│  AGH 运行底座（Agnes 模型 + Agent Loop）                      │
│  · 任务规划：选问题、推断单位、决定策略                        │
│  · 能力调用：调下面 8 个工具                                   │
│  · 反馈处理：读诊断，判断"单位推错了"→ 改 → 重试               │
│  · 结果验证：判定通过与否，决定是否收工                        │
│  · 轨迹记录：以上每一步自动留痕（这是要交的运行证据）           │
└───────────────────────────┬──────────────────────────────────┘
                            │ MCP（stdio）/ 后端插件 / CLI
┌───────────────────────────▼──────────────────────────────────┐
│  agh_tools.py —— 工具注册表（8 个工具）                        │
│  只声明接口 + 校验参数 + 写执行日志，不做任何决策               │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│  pipeline.py —— 原子能力层（纯计算，输入输出都是纯数据）        │
│  8 个能力：list_problems / load_problem / check_units /        │
│  build_candidate_library / fit_sparse / verify_formula /       │
│  compare_strategies / score_prediction                         │
└───────────────────────────┬──────────────────────────────────┘
│  dims.py / features.py / sparse.py / equivalence.py            │
│  equations.py —— 算法内核（一行没改，原来就是对的）             │
└───────────────────────────────────────────────────────────────┘
```

**一个必须遵守的设计纪律：不要把这 8 个工具再包成一个"一键跑完 21 题"的工具。**
那等于把决策又拿回脚本里，白改。批量跑 21 题的能力保留在 `experiment.py`，
它的定位是**基准回归测试与出报告**（验证算法没退化），不是智能体路径。

---

## 三、怎么在 AGH 里挂上

### 3.1 推荐：MCP 方式（stdio）—— 本项目已实测跑通

AGH 的 `mcp add` 支持 stdio / HTTP / SSE。官方文档明确提醒：
**不要把整条 shell 命令当可执行文件传**，也不要用 `--arg -c` 绕过策略。
我们的做法正是合规的——可执行文件传解释器本体，脚本路径用 `--arg` 单独传。

**实际执行过的命令（2026-10-03，一次通过）：**

```sh
# 步骤 1：注册。--name 是必需参数，漏了会直接报 usage 错误。
#         这条命令会打印摘要并问 Continue? [y/N]，需要有人在真终端里输 y。
node packages/cli/dist/local/agnes.mjs mcp add sciformula \
  --name SciFormula \
  --stdio "D:\projects\SciFormula\.venv\Scripts\python.exe" \
  --arg "D:\projects\SciFormula\mcp_server.py"

# 步骤 2：取 revision（只读，不需要确认）
node packages/cli/dist/local/agnes.mjs mcp get sciformula

# 步骤 3：信任 + 启用（也会问确认，输 y）
node packages/cli/dist/local/agnes.mjs mcp trust  sciformula --expected-revision REVISION
node packages/cli/dist/local/agnes.mjs mcp enable sciformula --expected-revision REVISION

# 步骤 4：验证（只读）
node packages/cli/dist/local/agnes.mjs mcp status sciformula   # 期望 connection=ready tools=8
node packages/cli/dist/local/agnes.mjs mcp tools  sciformula   # 期望列出 8 个工具，中文描述完整
```

**实测输出（2026-10-03 13:41）：**

```
mcp list   → sciformula revision=58e274c7... trust=trusted desired=enabled actual=ready transport=stdio
mcp status → sciformula connection=ready revision=58e274c7... catalog=1f3eb8bd... tools=8
```

三个词各管一件事，别混：`trust=trusted`（人审过了，不是自动信任）、
`desired=enabled / actual=ready`（配置要它开且真连上了）、`catalog=<指纹>`（AGH 实际抓到的工具目录）。
**只有 `tools=8` 且描述可读，才算「能力调用」这一环真正打通。**

> ⚠️ **`--name <显示名>` 不能省。** 只给 `--stdio` 会报
> `usage: agh mcp add <serverId> --name <displayName> (--stdio ...)`，白跑一轮。
>
> ⚠️ **所有写操作都要交互确认，且没有跳过开关。** AGH 源码里的判定是
> `if (io.stdin.isTTY !== true || io.stdout.isTTY !== true) return false;`，
> 然后才问 `Continue? [y/N]`。用脚本、管道或让助手代跑都会静默失败并报
> `operation cancelled`。这是「built for trust」的刻意设计。
> **只读命令（`get` / `list` / `status` / `tools`）不受影响。**
>
> ⚠️ **`REVISION` 是占位符，填真实值时要替换掉，不要把尖括号抄进终端**——
> shell 会把 `<` 当成输入重定向运算符而报错。实测 `trust` / `enable` 之间 revision 不变，
> 取一次即可连用；若中途有别的配置变更，旧值会被拒绝，届时重新 `mcp get` 一次。

**解释器要指向项目内的 `.venv`，不要指向全局 Python：**

```sh
cd D:\projects\SciFormula
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# 之后 --stdio 就填这个 .venv\Scripts\python.exe 的绝对路径
```

用 `.venv` 而不是全局 Python，是为了让「换台机器 `pip install -r requirements.txt` 就能复现」成立；
指向全局解释器的话，"你这台机器上恰好装了什么"会变成隐藏依赖。

### 3.1.1 工具在模型眼里的名字带前缀

接入之后，智能体看到的工具名**不是** `list_problems`，而是：

```
mcp_sciformula_98791938_list_problems
mcp_sciformula_98791938_load_problem
mcp_sciformula_98791938_check_units
mcp_sciformula_98791938_build_candidate_library
mcp_sciformula_98791938_fit_sparse
mcp_sciformula_98791938_verify_formula
mcp_sciformula_98791938_compare_strategies
mcp_sciformula_98791938_score_prediction
```

前缀是 `<mcp>_<serverId>_<数字>`，中间那串数字由 AGH 生成。写提示词时用短名也能被正确理解，
但**在报告里引用工具调用记录时，要用带前缀的完整名**——那才是轨迹里真实出现的东西。

### 3.1.2 工具调用需要人工批准（这是特性，不是障碍）

实测：在非交互环境里让智能体调用 `list_problems`，返回的是

```
- tool mcp_sciformula_98791938_list_problems
该工具调用被拒绝，无法获取结果。
```

原因是同一套授权机制——批准请求会变成**对话里的一张卡片**，需要有人在键盘前点。
AGH 源码里的开关是 `if (yolo || approvalMode === "off") needsAsk = false;`，
而当前 profile 是 `approvals = {"mode": "manual"}`（可选值 `manual` / `smart`）。

**所以第 7 节的运行必须在网页工作台或用户自己的终端里做，不能用脚本代跑。**

**这对本项目是加分项而非负担**：批准卡片留在执行记录里，正好证明
「这次运行确实受 AGH 管辖、有人工在每个决策点把关」——
比一条自动跑完、看不出谁在控制的记录更有说服力。提交时可以把批准片段一并截图。

### 3.2 备选：命令行方式

如果 MCP 那条路被策略挡住，`agh_tools.py` 本身也提供了 CLI 入口，
可以被 AGH 的命令执行路径直接调用（把它当成一个命令行程序注册即可）：

```sh
python agh_tools.py schema                            # 输出全部工具定义（JSON）
python agh_tools.py call list_problems
python agh_tools.py call load_problem --json '{"problem_id":"P03"}'
python agh_tools.py log                               # 查看执行日志
```

返回统一是 JSON（`{"ok": true/false, ...}`），失败时带 `error` 与 `hint`，
智能体能直接读出错原因并自行纠正。

### 3.3 再备选：后端插件

如果要求能力必须内嵌在 AGH 进程内，就得按 `docs/develop/backend.md` 用 Node 写一层薄壳，
在 `execute()` 里通过子进程调用 `python agh_tools.py call <工具> --json '<参数>'`，
把子进程的 stdout 解析成 `structured` 返回。
**不推荐**：多一层 JS 壳就多一处出错点，而且参数 schema 要写两遍（TypeBox 一份、Python 一份）。
只有在 3.1 与 3.2 都被挡住时才考虑。

---

## 四、在 AGH 里该让智能体怎么做

把下面这段直接作为任务提示交给 AGH（这是闭环能跑起来的关键）：

> 你有一组来自物理系统的带噪声观测数据，任务是从中恢复出支配它的物理定律公式。
> 请按以下流程工作，每一步都要基于上一步的返回值决定下一步做什么：
>
> 1. 用 `list_problems` 选一个基准问题。默认返回教学迷你库（21 题，编号 P01~P21）；
>    需要跑官方题时传参数 `library="feynman"`（官方 Feynman 基准 100 题，编号如
>    `I.12.11`）。官方题的编号可直接用于 `load_problem`，不需要任何环境变量。
> 2. 用 `load_problem` 载入观测数据。**注意：目标量的物理单位不会提供**，
>    需要你根据问题名称、目标量名称与变量名推断。变量名有物理含义（例如 `m`、`v`、`tau`、`sigma`），
>    请据此推断每个变量的量纲。
> 3. 用 `check_units` 校验你的单位写法是否合法。
> 4. 用 `build_candidate_library` 构造候选库并做量纲剪枝。
>    **这一步是对你单位推断的检验**：如果 `candidate_kept` 为 0，
>    说明这组单位让目标量无法由任何候选项拼出，几乎一定是你推错了——
>    请重新审读变量名，换一组单位重试。如果 `candidate_kept` 小于 5 也要警惕。
> 5. 用 `fit_sparse` 拟合，拿到公式。
> 6. 用 `verify_formula` 独立验证。若返回 `overfit`（区间内好、外推崩），
>    说明只是拟合了数据而非找到定律，请考虑调整 `max_terms` 或回到第 2 步重新检查单位。
> 7. 全部通过后输出结论：你推断的单位、最终公式、验证指标；
>    并如实报告过程中失败过几次、每次的原因。

**这段提示里有 4 个真正的决策点**（选题目、推单位、看剪枝反馈决定是否重推、看验证结果决定是否重试），
它们全部留在 AGH 的 agent loop 里 —— 这正是规程 3.1 要的那三个连续步骤。

### 4.1 预期会看到什么（本地已验证的闭环形态）

`toolchain_check.py` 用 P03（动能）走了一遍完整闭环，包括**一次故意的单位推断错误**，
用来验证"推错 → 收到反馈 → 改正 → 成功"这条链路真的闭合：

```
1) list_problems        → 动能（经典力学），变量 m, v，目标量 energy
2) load_problem         → data_id=P03_n100_nz0_s0，目标量 energy（单位未提供）
3) check_units          → 写法通过，目标量纲 = kg*m^2/s^3        ← 第一次推断：误填 W（把动能当成功率）
   build_candidate_library → 候选 40 → 0 项                      ← 反馈：单位为 W 时无解
4) 改判单位为 J 后重建   → 候选 40 → 1 项（砍掉 97.5%）           ← 闭环在这里闭合
5) fit_sparse           → 0.5*(m*v**(2))，1 项，R²=1.000000
6) verify_formula       → passed（区间内 2.4e-16 / 外推 2.6e-16）
7) compare_strategies   → omp: 1 项 | stlsq: 1 项
8) score_prediction     → 1% 噪声下预测 0.5003*(m*v**2) vs 真值 m*v**2/2 → up_to_constant
```

这条序列就是**「≥1 条工具调用链」**这条提交要求的现成素材；
AGH 跑完之后还会生成它自己的轨迹记录，两者可以互相对照。

### 4.1.1 跑官方 Feynman 题的提示变体（正式成绩同款题库）

把任务提示第 1 步换成「用 `list_problems`（`library="feynman"`）从官方 100 题里选一道
变量较多的题（例如 `I.12.11` 洛伦兹力，5 个变量）」，其余步骤完全不变。
**注意**：改过工具描述后要让 AGH 重新拉取（用户在自己终端执行，写操作需要交互确认）：

```bash
agh mcp reconnect sciformula --expected-revision REVISION
```

（`REVISION` 换成 `agh mcp get sciformula` 显示的当前值；重连前先取一次。）

官方题比迷你库难（多变量、嵌套分式），闭环里「推错单位 → 剪枝反馈 → 重推」
这条纠错链更容易被真实触发，轨迹证据反而更有说服力。

### 4.1.2 单位推断评测（B1 方向的量化证据）

`unit_infer_eval.py` 把「单位推断」从两道题的个案变成可量化评测：
`dump` 出 12 道官方题的无单位题目卡 → 在 AGH 里让模型逐题推断 → 回填答案 JSON →
`score` 按量纲等价自动打分（变量级 + 题目级准确率）。详见该文件头部说明。

### 4.2 提交时要交的运行证据怎么对应

| 4.2 要求 | 我们提供什么 |
|---|---|
| AGH 执行记录 | 三份官方导出：`results/agh-session.html`（迷你库 P03/P20）、`results/agh-session-feynman.html`（官方题库闭环，388 事件）、`results/agh-session-unitinfer.html`（单位推断评测作答）|
| ≥1 条工具/设备调用链 | 迷你库 8 步链（`agh-trace-summary.md`）；官方题库 19 次调用、含候选归零→自主诊断→换题的纠错链（`agh-trace-feynman.md`）；`sciformula/.agh_runs/episode_*.json` 是本地副本 |
| Agnes 模型参与核心任务证据 | 单位推断环节由 Agnes 完成（12 题量化评测：变量级 45/54，见 `results/unit_infer_report.md`），三份会话轨迹里都能看到它的推断过程 |
| 关键配置 | MCP 服务定义（`mcp get` 的输出）、`mcp tools` 的工具清单 |
| 专业验证结果 | `results/report.html` + `results/report_feynman.html` + `verify_formula` 的判定输出 |
| 失败类测试样例 | `docs/test-cases.md` 三类齐全；官方题会话里 `verify_formula` 判 overfit 拦截高分假公式是现场案例 |

---

## 五、本地自检命令（不需要 AGH 就能验证接线对不对）

```sh
python pipeline.py            # 8 个原子能力逐个自检，含"故意推错单位"的反馈检查
python agh_tools.py list      # 工具清单
python agh_tools.py schema    # 完整 MCP 工具定义（可直接对照 AGH 里看到的工具）
python mcp_server.py --selftest   # 走一遍 MCP 协议：initialize / tools/list / tools/call / 错误路径
python toolchain_check.py         # 走完整闭环，产出 episode 记录
python experiment.py              # 批量基准（21 题 × 3 噪声档），出报告
```

`--selftest` 会验证三件事：协议能正确应答、**stdout 洁净**（业务代码的 print 不会污染协议流）、
错误能被包装成 `isError: true` 让智能体读到。

---

## 五之二、AGH 那次运行替我们抓到的一个真问题（重要）

第 7 节那次真实运行（会话 `161ea3fb-7f17-4a89-9ba7-d6c70d46c6be`）在 P03 上产出过这样一条公式：

```
E = 0.5017 · m·v²  −  1.204
```

拟合 R² = 0.998，`verify_formula` 判定 **passed**，归一化 RMSE 仅 0.0034，
智能体据此写了结论、还专门解释了「截距 −1.204 属于可忽略的小量」。
**但这个公式是错的**：在真值 E≈0.14 处它预测出 **−1.07**（负动能），相对误差 880%。
原因是归一化 RMSE 由大量级点主导，小量级处的灾难性误差被平均掉了。

根因是**设计漏洞**，不是算法不稳定：

> 候选库里的每一项都经过量纲检查，**只有截距没有**——它是个自由参数，

这违背了本项目自己的核心主张。修复方向由主张本身推出：

> **加性常数只有在目标量本身无量纲时才合法。** 目标量有量纲时截距必须为 0。

改动落点：

| 文件 | 改了什么 |
|---|---|
| `sparse.py` | `fit_terms` / `fit_stlsq` / `fit_best` 增加 `allow_intercept` 开关 |
| `pipeline.py` | `fit_sparse` 依据候选库的 `target_unit` 决定是否放行截距 |
| `pipeline.py` | `verify_formula` 增加**符号合理性检查**与 `max_rel_err_small`；新增 `implausible` 判定 |
| `agh_tools.py` | `verify_formula` 的工具描述同步更新（含四种判定与两个易误判点的提醒） |

修复后的对比：

| | 修复前 | 修复后 |
|---|---|---|
| 公式 | `0.5017*(m*v**2) + -1.204` | `0.5012*(m*v**2)` |
| 截距 | −1.204 | 0 |
| 预测最小值 | −1.067（负动能） | 0.3075 |
| 小量级最大相对误差 | 880% | 0.23% |
| 拟合 R² | 0.99802 | 0.998017 |

`verify_formula` 现在喂进那条旧公式会返回：

```
判定: implausible
理由: 区间内违反符号合理性：观测值在整段区间上同号，而公式预测出了相反的符号
      （预测最小值 -0.8962，观测最小值 0.3068）……
```

批量评测结果**没有回归**（三个噪声档仍是 20/21 通过双重检验），
1% 噪声档的结构判定还从 20/21 提升到 21/21。

**这次运行的价值就在这里**：它不是把脚本搬进 AGH 跑一遍，
而是借智能体自由选参数（它自己挑了 5% 噪声，比我们基准里的 2% 更高）暴露出了
一个我们自己在 2% 档下没触发到的缺陷。

> ⚠️ **工具描述改了之后要重连才会生效。** 磁盘上的 `mcp_server.py` 改了，
> 但已经在运行的 MCP 子进程仍持有旧描述，`mcp status` 里的 `catalog` 指纹也不会变。
> 需要执行（写操作，会问确认）：
> ```
> agh mcp reconnect sciformula --expected-revision REVISION
> ```

## 六、如实说明：哪些已验证、哪些还没

### 已验证（真实环境实测）

- **算法侧**：8 个工具的参数校验、错误反馈、执行日志；
  工具链闭环（含一次单位推断错误的纠正过程，`toolchain_check.py` 通过）；
  改造后 `experiment.py` 结果与改造前完全一致（20/21 × 三个噪声档，剪枝 91.6%）。
- **MCP 协议侧**：`initialize` 握手、`tools/list`、`tools/call` 返回结构
  （`mcp_server.py --selftest` 通过）。
- **Windows 上的 stdio 编码**：走真实管道检查原始字节，1062 个非 ASCII 字节全部合法 UTF-8。
  客户端按 UTF-8 解码时中文不乱码。
- **AGH 真实接入**：`mcp status sciformula` → `connection=ready`、`tools=8`；
  `mcp tools sciformula` 列出全部 8 个工具、中文描述完整。
  **这一步同时反向验证了编码修复是有效的**——若 stdin/stdout 还是 GBK，
  工具描述会在这一步变成乱码、列表根本列不出来。
- **可执行文件策略**：项目内 `.venv\Scripts\python.exe` 作为 `--stdio` 目标**已被允许启动**，
  3.1 里那条「部署策略可能限制可执行文件」的担心可以排除。
- **模型能否看见工具**：实测问智能体「你有哪些工具」，它准确列出 8 个带
  `mcp_sciformula_98791938_` 前缀的工具名。**接线畅通。**

### 尚未验证（下一步）

1. **Agnes 是否按预期顺序连续调用工具。** 官方文档自己就提醒过
   「Whether a real model selects the tool requires real-model verification」。
   已知它能看见工具、也愿意尝试调用（只是被批准机制拦下），
   但**"会不会按 1→7 顺序走完、会不会在候选归零时自己判断推错了单位"还没有实测**。
2. **单位推断环节的实际质量**：Agnes 从变量名推断量纲的准确率、失败后能否自主纠正——
   这是本项目「数学 AI 与形式化推理」的落点，也是最有价值的观察记录。

> 这两项都要在**网页工作台或用户自己的终端**里跑，
> 因为工具调用需要人工批准（见 3.1.2）。计划见 `AGH环境搭建指引.html` 第 7 节。
