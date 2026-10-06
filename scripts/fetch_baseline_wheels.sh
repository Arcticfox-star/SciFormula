#!/bin/sh
# fetch_baseline_wheels.sh —— 从清华镜像取基线实验所需的 wheel
#
# 【为什么不用 pip install】
# 本机 pip 的 HTTPS 请求会被拦截（报 "Could not find a version that satisfies..."，
# 即使 curl 访问同一镜像正常）。所以统一走：curl 取链接 → curl 下载 wheel →
# 解包安装（scripts/install_baseline_wheels.py，连 pip 都不用）。
#
# 依赖集：gplearn(遗传规划符号回归) → scikit-learn → scipy → numpy
#         + joblib / threadpoolctl（sklearn 的运行时依赖）
#
# 【不写死任何本机路径与版本号】
#   · 下载目录：脚本位置反推的项目内 .baseline_wheels/（已 gitignore）；
#     不放系统临时目录——实测本机临时目录可能被安全策略限制写入。
#   · wheel 标签：问当前解释器要（cpXX-cpXX-平台），换台机器/换 Python 版本也对。
#     要覆盖就设环境变量 PYTHON 或 TAG。
set -e

# 镜像站根地址：wheel 实际在 /packages/... 下，**不是** /simple/packages/...
#（按后者拼会 404；这是实测踩过的坑）
BASE=https://pypi.tuna.tsinghua.edu.cn
MIRROR="$BASE/simple"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$ROOT/.baseline_wheels"
mkdir -p "$TMP"

PY="${PYTHON:-$(command -v python || command -v python3 || true)}"
if [ -z "$PY" ]; then
  echo "!! 找不到 python。请设置 PYTHON 指向你的解释器，例如："
  echo "   PYTHON=/c/Python314/python.exe sh scripts/fetch_baseline_wheels.sh"
  exit 1
fi
TAG="${TAG:-$("$PY" -c "import sysconfig,sys;v='cp%d%d'%sys.version_info[:2];print('%s-%s-%s'%(v,v,sysconfig.get_platform().replace('-','_').replace('.','_')))")}"
echo "== 解释器：$PY"
echo "== wheel 标签：$TAG"
echo "== 下载到：$TMP"

pick() {  # pick <包名> <匹配模式>
  curl -s --max-time 40 "$MIRROR/$1/" \
    | grep -o "href=\"[^\"]*\.whl[^\"]*\"" \
    | grep -E "$2" | tail -1 \
    | sed 's/^href="\.\.\/\.\.\///; s/#.*//'
}

get() {   # get <包名> <匹配模式>
  rel=$(pick "$1" "$2")
  if [ -z "$rel" ]; then echo "!! 找不到 $1 ($2)"; return 1; fi
  f=$(basename "$rel")
  echo "==> $f"
  curl -s --max-time 300 -o "$TMP/$f" "$BASE/$rel"
  ls -la "$TMP/$f" | awk '{print "    下载完成", $5, "字节"}'
}

get numpy "$TAG"
get scipy "$TAG"
get scikit-learn "$TAG"
get joblib "py3-none-any"
get threadpoolctl "py3-none-any"
get gplearn "py3-none-any"

echo
echo "全部下载到 $TMP"
ls "$TMP"
echo
echo "下一步：sh scripts/setup_baseline_env.sh（建立隔离环境并冒烟测试）"
