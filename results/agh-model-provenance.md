# AGH 运行模型溯源（一手登账）

本文件**全部由 `agh_provenance.py` 从 AGH 事件原件（JSONL）读出**，
没有任何一行是人工填写的。任何人重跑 `python agh_provenance.py`
都应得到同一份内容——这是它能作为核对依据的前提。

| 项 | 值 |
|---|---|
| 事件原件目录 | `results/agh-event/` |
| 已入库原件 | 7 份 |
| 生成方式 | `python agh_provenance.py`（可自行复算）|

## 一、逐会话登账

| 会话 ID | 模型 | AGH 版本 | 预设 | 事件数 | 轮/步 | 工具调用 | 人工批准（拒）| 原件 sha256 前 16 |
|---|---|---|---|---|---|---|---|---|
| `06df62f6-f32f-41dc-a78b-81c5eb4531be` | `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash` | （未提供） | standard-windows | 52 | 1/2 | 1 | 1（0）| `a46015fa5520a402` |
| `161ea3fb-7f17-4a89-9ba7-d6c70d46c6be` | `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash` | （未提供） | standard-windows | 554 | 4/28 | 24 | 24（1）| `48a4e21a3704f462` |
| `39d31a09-7e75-42d0-934e-911e92cf6e74` | `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash` | （未提供） | standard-windows | 391 | 1/20 | 19 | 14（3）| `b71e0dec1eecf1a1` |
| `438d81fd-c1b5-4efc-b739-d238864a3d3a` | `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash` | （未提供） | standard-windows | 524 | 2/26 | 25 | 6（0）| `b315274450cc53a2` |
| `6501b3a2-515a-4ce2-bce6-6dc59d6a954a` | `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash` | （未提供） | standard-windows | 244 | 2/12 | 10 | 10（0）| `a388085cf6d9db1d` |
| `6ea6a59e-2c25-47f8-96b9-e000383a5a93` | `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash` | （未提供） | standard-windows | 170 | 1/8 | 7 | 7（0）| `e5f044f112895599` |
| `9d469c46-1842-49b0-8dc3-21a9f04c6888` | `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash`, `agnes-3.0-flash` | （未提供） | standard-windows | 150 | 1/7 | 6 | 6（0）| `963f231a4e6bd826` |

> 「人工批准」= 每次工具调用前 AGH 都弹卡由人裁决。
> 这一列不为零，才能说明运行是在 AGH 里**逐次受管辖**地发生的，
> 而不是本地脚本一次性跑完后再补一份记录。

## 二、尚未落到官方 HTML 导出的会话

以下会话有事件原件，但在 `results/agh-trace-*.md` 里查不到对应 ID，
复核时可用 `agh export <会话id> --format agnes --html` 补一份：

- `06df62f6-f32f-41dc-a78b-81c5eb4531be`

---

本文件只是**登账**。真正的原件是 `results/agh-event/` 下的 JSONL，
它们同样受 `results/EVIDENCE.sha256` 逐字节保护。
