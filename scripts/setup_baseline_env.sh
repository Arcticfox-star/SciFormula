#!/bin/sh
# setup_baseline_env.sh —— 建立基线实验的隔离环境（对照实验用，不污染 AGH 那个 venv）
#
# 【为什么不用 pip install <包名>】
# 本机 pip 的 HTTPS 请求被拦（报 "Could not find a version..."），
# 但 curl 访问同一镜像正常 → 统一走：curl 下 wheel → **解包安装**（连 pip 都不用）。
#
# 【三个踩过的坑，别再犯】
#   1. 不要 `pip install --upgrade pip`：这台机器上大操作容易撞 120s 超时被杀，
#      升级 pip 中途被杀 = pip 没了，整个 venv 报废（已经坏过一次）。
#      所以这里**完全不调用 pip**：用 scripts/install_baseline_wheels.py 解包安装。
#   2. 大包（scipy 37MB）解包慢，必须后台跑，别放前台等。
#   3. wheel 文件名带版本与平台标签，写死就会换台机器失效 —— 改成让脚本自己探测。
#
# 【不写死任何本机路径】
#   项目根目录由脚本位置反推；Python 解释器取环境变量 PYTHON 或 PATH 上的 python。
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$(command -v python || command -v python3 || true)}"
if [ -z "$PY" ]; then
  echo "!! 找不到 python。请设置 PYTHON 指向你的解释器，例如："
  echo "   PYTHON=/c/Python314/python.exe sh scripts/setup_baseline_env.sh"
  exit 1
fi
W="$ROOT/.baseline_wheels"
V="$ROOT/.venv-baseline"

echo "== 项目根目录：$ROOT"
echo "== 解释器：$PY"

if [ ! -d "$W" ] || [ -z "$(ls -A "$W" 2>/dev/null)" ]; then
  echo "!! 还没下载 wheel。请先跑：sh scripts/fetch_baseline_wheels.sh"
  exit 1
fi

echo "== 1/4 重建虚拟环境（--clear 会重建 pip，但我们不用 pip）"
"$PY" -m venv --clear "$V"

# venv 的解释器在 Windows 下是 Scripts/、在 Linux/macOS 下是 bin/
PYV="$V/Scripts/python.exe"
[ -x "$PYV" ] || PYV="$V/bin/python"
echo "   隔离环境的解释器：$PYV"

echo "== 2/4 解包安装 wheel（不依赖 pip，完全离线）"
"$PY" "$ROOT/scripts/install_baseline_wheels.py" "$PYV" "$W"

echo "== 3/4 自动补齐 --no-deps 漏掉的依赖（缺哪个补哪个）"
"$PY" "$ROOT/scripts/ensure_baseline_deps.py"

echo "== 4/4 冒烟测试"
"$PYV" -c "
import numpy as np, numpy, scipy, sklearn, joblib, gplearn
print('numpy', numpy.__version__, '| scipy', scipy.__version__,
      '| sklearn', sklearn.__version__, '| gplearn', gplearn.__version__)
from gplearn.genetic import SymbolicRegressor
rng = np.random.default_rng(0)
X = rng.uniform(1, 2, size=(200, 2))
y = 0.5 * X[:, 0] * X[:, 1] ** 2
m = SymbolicRegressor(population_size=200, generations=4, random_state=0)
m.fit(X, y)
print('SymbolicRegressor 跑通 | 训练 R2 =', round(float(m.score(X, y)), 4))
"
echo "== 完成。基线实验命令见 README 与《常用命令速查》"
