# -*- coding: utf-8 -*-
"""
ensure_baseline_deps.py —— 自动把缺的依赖补进基线环境（离线、不用 pip）

思路：反复尝试导入目标模块；一旦报 `No module named X`，就去镜像下载 X 的 wheel
（优先纯 Python 包 `py3-none-any`，其次 cp314 win_amd64），解包进 site-packages，再试。
最多循环 10 轮。

【为什么需要它】
`pip install --no-deps` 省掉了依赖解析，于是要一个个手工补（实测补到第三个：
cloudpickle → narwhals → …）。与其一轮轮试，不如让机器自己补。
"""
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, ".venv-baseline", "Scripts", "python.exe")
WH = os.path.join(ROOT, ".baseline_wheels")
MIRROR = "https://pypi.tuna.tsinghua.edu.cn"

TEST = ("import numpy as np, numpy, scipy, sklearn, joblib, gplearn;"
        "from gplearn.genetic import SymbolicRegressor;"
        "from sklearn.ensemble import RandomForestRegressor;"
        "print('IMPORT_OK')")


def site_packages():
    out = subprocess.run([PY, "-c",
                          "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                         capture_output=True, text=True)
    return out.stdout.strip()


def curl(url, dest, timeout=300):
    r = subprocess.run(["curl", "-s", "-w", "%{http_code}", "--max-time", str(timeout),
                        "-o", dest, url], capture_output=True, text=True)
    return r.stdout.strip()


def fetch(pkg):
    """从镜像取一个 wheel（优先纯 Python）。返回本地路径或 None。"""
    html = subprocess.run(["curl", "-s", "--max-time", "40",
                           "%s/simple/%s/" % (MIRROR, pkg)],
                          capture_output=True, text=True).stdout
    links = re.findall(r'href="([^"]*\.whl[^"]*)"', html)
    for pat in ("py3-none-any", "cp314-cp314-win_amd64", "cp313-cp313-win_amd64"):
        cand = [l for l in links if pat in l]
        if cand:
            rel = cand[-1].replace("../../", "").split("#")[0]
            name = os.path.basename(rel)
            dest = os.path.join(WH, name)
            code = curl("%s/%s" % (MIRROR, rel), dest)
            if code == "200" and os.path.getsize(dest) > 1000:
                return dest
    return None


def main():
    dst = site_packages()
    print("解释器：%s\n安装到：%s\n" % (PY, dst))
    for i in range(1, 11):
        r = subprocess.run([PY, "-c", TEST], capture_output=True, text=True)
        if "IMPORT_OK" in r.stdout:
            print("第 %d 轮：全部导入成功 ✔" % i)
            return 0
        m = re.search(r"No module named '([^']+)'", r.stderr or "")
        if not m:
            print("第 %d 轮：非缺依赖错误，原始输出：\n%s" % (i, (r.stderr or "")[-800:]))
            return 1
        mod = m.group(1).split(".")[0]
        pkg = {"sklearn": "scikit-learn", "skimage": "scikit-image"}.get(mod, mod)
        print("第 %d 轮：缺 %s → 取 %s" % (i, mod, pkg))
        dest = fetch(pkg)
        if not dest:
            print("  取不到 %s 的 wheel，停止" % pkg)
            return 1
        with zipfile.ZipFile(dest) as z:
            z.extractall(dst)
        print("  已解包 %s" % os.path.basename(dest))
    print("循环上限到了，仍未能全部导入")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
