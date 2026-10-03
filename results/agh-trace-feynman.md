# AGH 运行记录摘要（第二份：官方 Feynman 题库闭环）

本文件由人工从 AGH 官方 HTML 导出整理（该导出即提交材料之一）。
**事件与指标未经修饰**；只有末尾的分析段是人写的。
（第一份摘要 `agh-trace-summary.md` 由 `trace_summary.py` 从 JSONL 流水自动生成，
本份因导出格式为 HTML 改为人工整理，口径一致：逐条对应导出文件中的事件。）

原始导出：`results/agh-session-feynman.html`（AGH 官方 HTML 导出）

| 项 | 值 |
|---|---|
| 会话 ID | `39d31a09-7e75-42d0-934e-911e92cf6e74` |
| AGH 版本 | `0.0.0` |
| 模型 | `agnes-3.0-flash` |
| 预设 | `standard-windows` |
| 事件总数 | 388 |
| 工具调用次数 | 19 |
| 人工批准卡片 | 14（其中被拒绝 3 次）|
| 导出时间（UTC）| 2026-10-03T14:30:27Z（北京时间 22:30）|

> 这场会话与第一份的区别：**题库换成了官方 Feynman 基准**（`list_problems` 传
> `library="feynman"`），并且这是一场**包含失败与纠错的完整闭环**——
> 模型先选了 9 变量的 I.9.18，遭遇候选归零后自主诊断根因、换题重跑，
> 最终验证环节判 overfit 并如实报告失败。

## 一、工具调用链（按发生顺序）

| # | 工具 | 关键参数 | 批准 | 结果概要 |
|---|---|---|---|---|
| 1 | `list_problems` | {"library": "feynman"} | allowed-once | ok=True; count=100 |
| 2 | `load_problem` | {"problem_id": "I.9.18", "mode": "discovery", "noise": 0.05, "n_samples": 200} | allowed-once | ok=True; data_id=I.9.18_n200_nz0.05_s0（9 变量：m1,m2,G,x1,x2,y1,y2,z1,z2）|
| 3 | `check_units` | {"G": "N*m^2/kg^2", "m1": "kg", "m2": "kg", 坐标="m", "target_unit": "N"} | rejected | approval rejected |
| 4 | `check_units` | 同上 | rejected | approval rejected |
| 5 | `check_units` | 同上 | rejected | approval rejected |
| 6 | `build_candidate_library` | 同上单位组 | allowed-once | candidate_kept=0（候选归零）|
| 7 | `build_candidate_library` | G 改写为 "m^3/(kg*s^2)" | allowed-once | failed（单位写法含括号，解析失败）|
| 8 | `build_candidate_library` | G="m^3/kg/s^2", target="N" | allowed-session | candidate_kept=0（仍归零）|
| 9 | `check_units` | target 改写 "kg*m/s^2"（与 N 等价）| allowed-session | ok=True（量纲等价验证）|
| 10 | `list_problems` | {"library": "feynman"} | allowed-session | 重新选题 |
| 11 | `load_problem` | {"problem_id": "I.13.12", "mode": "discovery", "noise": 0.05, "n_samples": 200} | allowed-session | ok=True; 5 变量：m1,m2,r1,r2,G |
| 12 | `check_units` | {"m1": "kg", "m2": "kg", "r1": "m", "r2": "m", "G": "m^3/kg/s^2", "target_unit": "J"} | allowed-session | ok=True（单位推断全对）|
| 13 | `build_candidate_library` | 同上单位组 | allowed-session | candidate_kept=186 |
| 14 | `fit_sparse` | {"strategy": "omp", "max_terms": 10} | allowed-session | 9 项，R²=0.998，含 log(\|r1/r2\|) 等拼凑项 |
| 15 | `fit_sparse` | 第二组参数 | allowed-session | 2 项，R²=0.978 |
| 16 | `compare_strategies` | OMP vs STLSQ 对照 | allowed-session | OMP 5 项 R²=0.998；STLSQ 151 项、系数达 4.9×10⁷（共线失效）|
| 17 | `verify_formula` | OMP 9 项公式 | allowed-session | verdict=overfit（外推 nRMSE 0.206 > 阈值 0.2）|
| 18 | `verify_formula` | OMP 5 项公式 | allowed-session | verdict=overfit（外推 nRMSE 0.257 > 阈值 0.2）|

> 调用顺序按导出文件中事件出现顺序整理；#14/#15 的先后以模型自报的
> 「OMP #1（10→9 项）/ OMP #2（3→2 项）」对应。

## 二、用户输入

**第 1 轮（唯一一轮任务指令）：**

```
你有一组来自物理系统的带噪声观测数据，任务是从中恢复物理定律公式。请用 list_problems
并传 library="feynman" 查看官方基准题库，选一道变量较多的题。目标量的物理单位不会提供，
需要你根据变量名推断每个变量与目标量的量纲。流程：check_units 校验写法 →
build_candidate_library 建库剪枝（这一步是对你单位推断的检验，candidate_kept 为 0
或很小就要重推）→ fit_sparse 拟合 → verify_formula 独立验证 →
输出结论（推断的单位、公式、验证指标、失败次数）。
```

此后 388 条事件全部由模型在 AGH 的 Agent Loop 里自主推进，用户只在批准卡上做裁决。

## 三、模型自主决策的关键节点

1. **选题**：看完 100 题清单后自选 **I.9.18**（9 变量，全场变量最多的一档），
   自定 noise=0.05、n_samples=200——难度参数是它自己加的码。
2. **单位推断（无任何单位提示）**：从变量名推出 m1,m2=kg、坐标=m、
   G=N·m²/kg²（后验证等价于 m³/kg/s²）、目标 F=N。
3. **候选归零后的自主诊断**：kept=0 反复出现，模型没有硬闯，而是做量纲代数逐项排查
   （含排除「G 无量纲」假设：s 的量纲来源无解），最终得出正确结论——
   **候选库只含单变量幂组合，而 I.9.18 的分母是坐标差的平方和
   `G*m1*m2/((x2-x1)²+(y2-y1)²+(z2-z1)²)`，无法由单变量幂拼出**。
   并据此主动换题 I.13.12（5 变量引力势能）。
4. **换题后单位推断全对**：m1,m2=kg、r1,r2=m、G=m³/kg/s²、目标 U=J，
   还注意到 y_min 为负值符合引力势能符号的物理意义。
5. **诚实收尾**：拟合产物含 `log(|r1/r2|)` 等非物理拼凑项，验证环节判 **overfit**
   （外推 nRMSE 0.206/0.257，阈值 0.2），模型最终自报「失败 2 次、未恢复定律」，
   并指出根因是候选库缺**差分基**（如 (r1−r2)、1/r2−1/r1），建议作为原子基元加入。

## 四、与批量基准的交叉验证（两边独立运行，结论一致）

| 题目 | 智能体会话（本场）| 批量实验 `results/detailed_feynman.csv` |
|---|---|---|
| I.9.18 | kept=0，诊断「候选库无解」| 三档噪声均 `no_candidates` |
| I.13.12 | overfit，外推不过阈 | `approx`，r2_extrap ≈ 0.39~0.41，未过双重检验 |

智能体路径与批量路径**共用同一份 pipeline 实现**（项目纪律：不做两套代码），
两边的失败判定完全吻合——这不是巧合，是同构性的直接证据。

真值公式（事后对照）：I.9.18 = `G*m1*m2/((x2-x1)**2+(y2-y1)**2+(z2-z1)**2)`；
I.13.12 = `G*m1*m2*(1/r2-1/r1)`。模型对「差分结构导致候选库表达力不足」的根因
判断与真值结构相符（I.13.12 真值同样是两个倒数项之差，非单变量幂乘积）。

## 五、人工分析（本段是人写的）

这场会话表面上「失败了」——两道题都没恢复出真值公式——但它是目前**证据价值最高**
的一场，原因有三：

1. **完整闭环且含纠错**：选题 → 推断单位 → 建库 → 遇阻（kept=0）→ 自主诊断 →
   换题重跑 → 拟合 → 对照（OMP vs STLSQ）→ 独立验证 → 诚实报告失败。
   「反馈处理」不是顺境里走个过场，而是在真实的失败反馈里表现出来的。
2. **验证体系当场拦截了「高分假公式」**：OMP 拼出的公式训练区间 R²=0.998，
   若只看拟合分就是「通过」；但 `verify_formula` 的外推检验判 overfit 拒收。
   这与第一份摘要里「负动能公式被符号合理性检查拦截」构成同一主题的两个案例：
   **验证环节在两种不同失效模式下都真实起了作用。**
3. **失败被如实写进了模型的最终报告**：模型自报失败次数、自述根因、给出改进方向
   （差分基），没有粉饰。这正是规程「反馈处理与结果验证」想要的行为，
   也和我们 README 里声明的能力边界（15 题候选库无解）互相印证。

一个必须如实记录的细节：#3~#5 三次 `check_units` 被批准卡拒绝（人工点了拒绝），
模型没有绕过或假装成功，而是在后续改用 `build_candidate_library` 的路径继续，
后来 `check_units` 获得批准后正常使用。拒绝事件完整保留在导出文件中。
