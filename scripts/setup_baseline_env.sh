#!/bin/sh
# setup_baseline_env.sh —— 建立基线实验的隔离环境（对照实验用，不污染 AGH 那个 venv）
#
# 【为什么不用 pip install <包名>】
# 本机 pip 的 HTTPS 请求被拦（报 "Could not find a version..."），
# 但 curl 访问同一镜像正常 → 统一走：curl 下 wheel → pip install <本地 .whl> --no-index --no-deps。
#
# 【两个踩过的坑，别再犯】
#   1. 不要 `pip install --upgrade pip`：这台机器上大操作容易撞 120s 超时被杀，
#      升级 pip 中途被杀 = pip 没了，整个 venv 报废（已经坏过一次）。
#   2. 大包（scipy 37MB）解包慢，必须后台跑，别放前台等。
set -e
ROOT=/d/projects/SciFormula
PY="C:/Users/starlight/AppData/Local/Programs/Python/Python314/python.exe"
W="$ROOT/.baseline_wheels"
V="$ROOT/.venv-baseline"

echo "== 1/4 重建虚拟环境（--clear 会重建 pip，不升级）"
"$PY" -m venv --clear "$V"

echo "== 2/4 装 numpy / joblib"
"$V/Scripts/python.exe" -m pip install --quiet --no-index --no-deps \
  "$W/numpy-2.5.3-cp314-cp314-win_amd64.whl" "$W/joblib-1.6.0-py3-none-any.whl"

echo "== 3/4 装 scipy（大包，耐心等）"
"$V/Scripts/python.exe" -m pip install --quiet --no-index --no-deps \
  "$W/scipy-1.18.1-cp314-cp314-win_amd64.whl"

echo "== 4/4 装 scikit-learn / threadpoolctl / gplearn"
"$V/Scripts/python.exe" -m pip install --quiet --no-index --no-deps \
  "$W/scikit_learn-1.9.1-cp314-cp314-win_amd64.whl" \
  "$W/threadpoolctl-3.7.0-py3-none-any.whl" "$W/gplearn-0.4.3-py3-none-any.whl"

echo "== 冒烟测试"
"$V/Scripts/python.exe" -c "
import numpy, scipy, sklearn, joblib, gplearn
print('numpy', numpy.__version__, '| scipy', scipy.__version__,
      '| sklearn', sklearn.__version__, '| gplearn', gplearn.__version__)
from gplearn.genetic import SymbolicRegressor
import numpy as np
rng = np.random.default_rng(0)
X = rng.uniform(1, 2, size=(200, 2))
y = 0.5 * X[:, 0] * X[:, 1] ** 2
m = SymbolicRegressor(population_size=200, generations=4, random_state=0)
m.fit(X, y)
print('SymbolicRegressor 跑通 | 训练 R2 =', round(float(m.score(X, y)), 4))
"
echo "== 完成"
