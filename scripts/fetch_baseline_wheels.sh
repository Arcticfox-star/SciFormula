#!/bin/sh
# fetch_baseline_wheels.sh —— 用 curl 从清华镜像取基线实验所需的 wheel
#
# 【为什么不用 pip install】
# 本机 pip 的 HTTPS 请求会被拦截（报 "Could not find a version that satisfies..."，
# 即使 curl 访问同一镜像正常）。所以统一走：curl 取链接 → curl 下载 wheel →
# pip install <本地 .whl> --no-deps --no-index。
#
# 依赖集：gplearn(遗传规划符号回归) → scikit-learn → scipy → numpy
#         + joblib / threadpoolctl（sklearn 的运行时依赖）
set -e
MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
TMP=/c/Users/starlight/AppData/Local/Temp/sciformula_wheels
mkdir -p "$TMP"

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
  curl -s --max-time 300 -o "$TMP/$f" "$MIRROR/$rel"
  ls -la "$TMP/$f" | awk '{print "    下载完成", $5, "字节"}'
}

get numpy "cp314-cp314-win_amd64"
get scipy "cp314-cp314-win_amd64"
get scikit-learn "cp314-cp314-win_amd64"
get joblib "py3-none-any"
get threadpoolctl "py3-none-any"
get gplearn "py3-none-any"

echo
echo "全部下载到 $TMP"
ls "$TMP"
