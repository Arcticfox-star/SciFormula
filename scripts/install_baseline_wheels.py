# -*- coding: utf-8 -*-
"""
install_baseline_wheels.py —— 不依赖 pip，把本地 wheel 解包进指定的 site-packages

【为什么要这样装】
本机 pip 有两个问题叠在一起：
  ① HTTPS 请求被拦（只能 curl 下 wheel，见 scripts/fetch_baseline_wheels.sh）；
  ② 大包安装容易撞超时被杀——实测把 `pip install --upgrade pip` 打断之后，
     整个 venv 的 pip 直接没了；重建时 ensurepip 也没能把 pip 装回来。
而 wheel 本身就是一个 zip，解包即安装（`.dist-info/` 就在包里，元数据不丢）。
这条路完全离线、不依赖 pip、也不会中途被杀。

【怎么用】
    python install_baseline_wheels.py <目标 python 解释器> <wheel 目录> [wheel...]

例：
    .venv-baseline/Scripts/python.exe install_baseline_wheels.py \
        .venv-baseline/Scripts/python.exe .baseline_wheels
"""

import glob
import os
import sys
import zipfile


def site_packages(py_exe):
    import subprocess
    out = subprocess.run([py_exe, "-c",
                          "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("取 site-packages 失败：%s" % out.stderr.strip())
    return out.stdout.strip()


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    py_exe, wheel_dir = argv[1], argv[2]
    targets = argv[3:] or sorted(glob.glob(os.path.join(wheel_dir, "*.whl")))
    if not targets:
        print("没找到 wheel：%s" % wheel_dir)
        return 2

    dst = site_packages(py_exe)
    print("目标解释器：%s" % py_exe)
    print("安装目录　：%s\n" % dst)

    # 顺序：numpy → scipy → sklearn → 其余（后装的依赖先装的包，但解包顺序本身不影响导入）
    order = {"numpy": 0, "scipy": 1, "scikit_learn": 2, "joblib": 3,
             "threadpoolctl": 4, "gplearn": 5}

    def rank(p):
        base = os.path.basename(p).lower()
        for k, v in order.items():
            if base.startswith(k):
                return v
        return 9

    for w in sorted(targets, key=rank):
        base = os.path.basename(w)
        if not os.path.exists(w):
            print("  !! 缺失 %s" % base)
            continue
        with zipfile.ZipFile(w) as z:
            names = z.namelist()
            z.extractall(dst)
        print("  OK  %-46s（%d 个成员）" % (base, len(names)))
    print("\n解包完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
