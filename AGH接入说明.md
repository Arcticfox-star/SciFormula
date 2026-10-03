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

### 3.1 推荐：MCP 方式（stdio）

AGH 的 `mcp add` 支持 stdio / HTTP / SSE。官方文档明确提醒：
**不要把整条 shell 命令当可执行文件传**，也不要用 `--arg -c` 绕过策略。
我们的做法正是合规的——可执行文件传解释器本体，脚本路径用 `--arg` 单独传：

```sh
# 在 AGH 源码仓库根目录执行（把 <PYTHON> 换成实际解释器路径）
node packages/cli/dist/local/agnes.mjs mcp add sciformula \
  --stdio <PYTHON> \
  --arg /绝对路径/sciformula/mcp_server.py

# 之后按官方流程审阅、信任、启用
node packages/cli/dist/local/agnes.mjs mcp get     sciformula
node packages/cli/dist/local/agnes.mjs mcp trust   sciformula --expected-revision REVISION
node packages/cli/dist/local/agnes.mjs mcp enable  sciformula --expected-revision REVISION
node packages/cli/dist/local/agnes.mjs mcp status  sciformula
node packages/cli/dist/local/agnes.mjs mcp tools   sciformula     # 应看到 8 个工具
node packages/cli/dist/local/agnes.mjs mcp test    sciformula --expected-revision REVISION
```

启用后，在 Web 的 **Settings → MCP** 里能看到服务状态与工具目录。

**`<PYTHON>` 该填什么？** 建议给项目建一个独立虚拟环境，别指向任何临时路径：

```sh
cd sciformula
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# 之后 <PYTHON> 就是这个 .venv/Scripts/python.exe 的绝对路径
```

> ⚠️ 官方文档提到「**Deployment policy also controls allowed executables**」——
> 部署策略可能会限制允许启动的可执行文件。如果 `mcp add` 之后状态一直是
> 连不上 / 被策略拒绝，先看 `mcp status` 返回的错误码和原因，
> 再去确认该解释器路径是否在允许列表内。这是**接入环节最可能卡住的地方**，
> 建议 10/3 优先验证。

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
> 1. 用 `list_problems` 选一个基准问题。
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

### 4.2 提交时要交的运行证据怎么对应

| 4.2 要求 | 我们提供什么 |
|---|---|
| AGH 执行记录 | AGH 自动生成的轨迹（在 Web/Session 里导出） |
| ≥1 条工具/设备调用链 | 上面那条 8 步链；`sciformula/.agh_runs/episode_*.json` 是本地副本 |
| Agnes 模型参与核心任务证据 | 单位推断环节由 Agnes 完成（这是"数学 AI 与形式化推理"的落点），轨迹里能看到它的推断过程 |
| 关键配置 | MCP 服务定义（`mcp get` 的输出）、`mcp tools` 的工具清单 |
| 专业验证结果 | `results/report.html` + `results/detailed.csv` + `verify_formula` 的判定输出 |
| 失败类测试样例 | ③ 里那次"误填 W 导致候选归零"；以及基准里 P19 的失败案例 |

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

## 六、如实说明：哪些已验证、哪些还没

**已验证（本地实测）**

- 8 个工具的参数校验、错误反馈、执行日志
- MCP 协议握手与 `tools/call` 返回结构（`mcp_server.py --selftest` 通过）
- 工具链闭环，含一次单位推断错误的纠正过程（`toolchain_check.py` 通过）
- 改造后 `experiment.py` 结果与改造前完全一致（20/21 × 三个噪声档，剪枝 91.6%），
  说明分层没有改变算法行为

**尚未验证（10/3 的待办，必须实测）**

1. **AGH 实例里的真实接入**：本机没有 AGH（它是 pre-alpha，需 Node 24.10+ / pnpm 10.34.5 自行从源码构建），
   上面 3.1 的命令来自官方文档原文，**我没有在真实 AGH 上跑过**。
2. **MCP 协议版本兼容**：我们的服务器在 `initialize` 时**回显客户端提出的协议版本**，
   这是为了兼容 pre-alpha 阶段不断变化的协议。若 AGH 要求的版本行为有差异，需要按报错调整。
3. **可执行文件策略**：见 3.1 的警告。
4. **Agnes 模型是否愿意按预期调用工具**：官方文档自己就提醒过，
   "Whether a real model selects the tool requires real-model verification"。
   所以 10/3 接上之后，第一件事是跑一次 P03 看它会不会自己按顺序调工具；
   如果它跳步，就把第四节那段提示词缩得更硬一些（明确"必须按 1→7 顺序"）。
