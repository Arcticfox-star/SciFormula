# -*- coding: utf-8 -*-
"""
datasets.py —— 把**磁盘上的观测数据文件**接进发现流程

【这块是干什么的（对应竞赛规则里的「由 AGH 连接数据」）】
竞赛要求 AGH 连接「数据、专业软件、仿真环境或设备接口」。
之前的数据是在进程内即时合成的：智能体看不到"数据"这个东西。
本模块让智能体通过 AGH 的 MCP 通道**去读磁盘上的 CSV 文件**：

    智能体 →（MCP）→ list_datasets / load_dataset → 读 data/observations/*.csv
           → check_units → build_candidate_library → fit_sparse → verify_formula

【为什么这不只是"合规"】
数据文件是**可替换的**：把同格式的真实测量数据丢进 `data/observations/`，
同一套流程照跑，算法一行都不用改。这条在答辩里比"我们接了数据"更有分量。

【纪律（和评分器不进工具集是同一条）】
CSV 里只有观测值：**没有真值公式、没有单位**。
真值仍在官方数据集文件里，只有验证/评分环节读它。
`load_dataset` 的返回值里也不会出现公式或单位——智能体要自己推断。
"""

import csv
import json
import os
import time

import numpy as np

import equations as EQ
from pipeline import RUN_DIR, _ensure_dir

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data", "observations")
# 仿真实验台跑出来的数据集也放在同一个"可被 AGH 读取"的层面里：
# 它们与观测 CSV 同格式（只有参数取值与读数），所以走同一套 load_dataset 流程。
SIM_DIR = os.path.join(ROOT, "data", "simulations")
DATA_DIRS = (DATA_DIR, SIM_DIR)

DISCOVERY = "discovery"


def _csv_path(name):
    """按数据集名找文件；也接受直接给文件名。观测数据与仿真数据两个目录都找。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("数据集名不能为空")
    if not name.lower().endswith(".csv"):
        name += ".csv"
    for d in DATA_DIRS:
        path = os.path.join(d, name)
        if not os.path.exists(path):
            continue
        # 防止用 ../ 读到别处
        if os.path.dirname(os.path.realpath(path)) != os.path.realpath(d):
            raise ValueError("数据集必须位于 %s 之内" % os.path.relpath(d, ROOT))
        return path
    raise FileNotFoundError("找不到数据集 %s（已搜索 %s）"
                           % (name, " 与 ".join(os.path.relpath(d, ROOT) for d in DATA_DIRS)))


def _sidecar(path):
    """仿真数据集带一个 .sim.json 边车（记录场景与取值范围，供验证环节重跑仿真用）。"""
    p = os.path.splitext(path)[0] + ".sim.json"
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_datasets():
    """
    列出可用的观测数据文件。

    只报告"有什么数据"，不报告"答案是什么"——文件名与列名里不含公式与单位。
    """
    items = []
    for d in DATA_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".csv"):
                continue
            path = os.path.join(d, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    header = next(csv.reader(f))
                n_rows = sum(1 for _ in open(path, encoding="utf-8")) - 1
            except Exception:
                continue
            side = _sidecar(path)
            items.append(dict(name=fn[:-4], file=os.path.relpath(path, ROOT),
                              source="simulation" if side else "observation",
                              scenario=(side or {}).get("scenario"),
                              n_rows=n_rows, n_columns=len(header),
                              columns=header,
                              target_column=header[-1],
                              variable_columns=header[:-1],
                              in_library=fn[:-4] in set(e["id"] for e in EQ.EQUATIONS)))
    return dict(ok=True, count=len(items),
                directory=" 与 ".join(os.path.relpath(d, ROOT) for d in DATA_DIRS),
                n_observation=sum(1 for it in items if it["source"] == "observation"),
                n_simulation=sum(1 for it in items if it["source"] == "simulation"),
                datasets=items,
                note=("这些是**观测数据文件**：每行一次采样，只含变量取值与目标量观测值，"
                      "不含真值公式、不含单位。用 load_dataset 载入后即可开始推断。"
                      "其中 source=simulation 的那些是**仿真实验台**跑出来的（run_sweep 生成）。"))


def load_dataset(name, mode=DISCOVERY):
    """
    读一个数据文件，登记成后续工具可用的 data_id。

    返回的是**观测信息**（变量名、采样范围、观测值统计与预览），
    与 load_problem 在 discovery 模式下的返回形状一致——真值与单位都不给。
    """
    path = _csv_path(name)
    data = np.genfromtxt(path, delimiter=",", names=True)
    cols = list(data.dtype.names)
    if len(cols) < 2:
        raise ValueError("数据文件至少要有「变量列 + 目标量列」两列")
    var_names, target = cols[:-1], cols[-1]
    X = np.column_stack([np.asarray(data[c], dtype=float) for c in var_names])
    y = np.asarray(data[target], dtype=float)
    if not np.all(np.isfinite(X)) or not np.all(np.isfinite(y)):
        raise ValueError("数据里有非有限值（nan / inf），请先清洗")

    dataset_id = os.path.splitext(os.path.basename(path))[0]
    data_id = "%s_csv_n%d" % (dataset_id, X.shape[0])

    _ensure_dir()
    np.savez(os.path.join(RUN_DIR, data_id + ".npz"), X=X, y=y)

    # 两类参考解来源：
    #   ① 官方题库里的同名题 → 附闭式公式
    #   ② 仿真实验台的边车   → 验证时**当场重跑仿真**（边车里没有真值公式）
    known = None
    try:
        known = EQ.get(dataset_id)
    except Exception:
        known = None
    side = _sidecar(path)

    variables = []
    for i, nm in enumerate(var_names):
        col = X[:, i]
        variables.append(dict(name=nm, sampled_range=[float(col.min()), float(col.max())],
                              sampling="来自数据文件列 %d" % i))

    meta = dict(
        data_id=data_id, problem_id=dataset_id if known else "",
        name=dataset_id, ref="观测数据文件 %s" % os.path.relpath(path, ROOT),
        target_variable=target, n_samples=int(X.shape[0]),
        # 数据来自文件，没有"我们加的噪声水平"这个概念：写 0.0 并显式标注，
        # **绝不能写 nan** —— NaN 会让上层回显时产生非法 JSON（详见 agh_tools._json_safe）。
        noise=0.0, seed=(int(side["seed"]) + 1) if side else 0, mode=mode,
        noise_note="数据来自文件，噪声水平未知",
        variables=variables,
        source="simulation" if side else "csv",
        csv=os.path.relpath(path, ROOT),
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    if known is not None:
        meta["reference"] = dict(expr=known["expr"], target_unit=known["target_unit"],
                                 variables_units={k: known["vars"][k]
                                                  for k in EQ.varnames(known)})
    if side is not None:
        meta["simulation"] = side
    with open(os.path.join(RUN_DIR, data_id + ".meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if known is not None:
        verify_hint = ""
    elif side is not None:
        verify_hint = ("注意：这批数据来自**仿真实验台**（场景 %s）。独立验证时会"
                       "**换一组参数当场重跑仿真**，比对的是仿真当刻测得的读数——"
                       "所以 verify_formula 可用，而且它不依赖任何存储的答案。"
                       % side.get("scenario"))
    else:
        verify_hint = ("注意：该数据集既不在官方题库内、也不是仿真实验台产出的，"
                       "没有参考解，独立验证环节将无法进行。")

    k = min(5, len(y))
    return dict(
        ok=True, data_id=data_id, mode=mode, source="simulation" if side else "csv",
        dataset=dict(name=dataset_id, file=meta["csv"], n_rows=int(X.shape[0]),
                     scenario=(side or {}).get("scenario")),
        target=dict(name=target, unit=None),
        variables=variables,
        observations=dict(
            n=int(X.shape[0]),
            y_mean=float(np.mean(y)), y_std=float(np.std(y)),
            y_min=float(np.min(y)), y_max=float(np.max(y)),
            y_preview=[float(v) for v in y[:k]],
            x_preview=[[float(v) for v in row] for row in X[:k]],
        ),
        # 相对项目根的路径（不泄露本机绝对目录结构；该字段会进入会话导出）
        saved=os.path.relpath(os.path.join(RUN_DIR, data_id + ".npz"), ROOT),
        has_reference=bool(known is not None or side is not None),
        verification=("reference" if known is not None
                      else ("simulation_rerun" if side is not None else None)),
        note=("数据来自磁盘文件。目标量的量纲与各变量的量纲**均未提供**："
              "请根据变量名与列名所对应的物理语境推断它们，再用 check_units 校验写法、"
              "用 build_candidate_library 检验推断是否自洽。" + verify_hint),
    )
