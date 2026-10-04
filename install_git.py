# -*- coding: utf-8 -*-
"""从国内镜像下载 Git for Windows 并静默安装，进度即时写日志。"""
import os
import subprocess
import time
import urllib.request

OUT = r".\git_install_log.txt"
INSTALLER = r".\git_installer.exe"

URLS = [
    "https://registry.npmmirror.com/-/binary/git-for-windows/v2.55.0.windows.3/Git-2.55.0.3-64-bit.exe",
    "https://mirrors.tuna.tsinghua.edu.cn/github-release/git-for-windows/git/v2.55.0.windows.3/Git-2.55.0.3-64-bit.exe",
    "https://registry.npmmirror.com/-/binary/git-for-windows/v2.50.1.windows.1/Git-2.50.1-64-bit.exe",
]

def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")

open(OUT, "w").close()

ok = False
for url in URLS:
    log(f"尝试: {url}")
    try:
        t0 = time.time()
        urllib.request.urlretrieve(url, INSTALLER)
        sz = os.path.getsize(INSTALLER)
        if sz > 50 * 2**20:
            log(f"下载成功 {sz/2**20:.1f}MB 耗时 {time.time()-t0:.0f}s")
            ok = True
            break
        log(f"文件过小({sz})，删除重试")
        os.remove(INSTALLER)
    except Exception as e:
        log(f"失败: {e}")

if ok:
    log("开始静默安装（/VERYSILENT /NORESTART）…")
    try:
        r = subprocess.run([INSTALLER, "/VERYSILENT", "/NORESTART",
                            "/COMPONENTS=gitlite"],
                           timeout=540)
        log(f"安装器退出码: {r.returncode}")
    except Exception as e:
        log(f"安装异常: {e}")
    bash = r"C:\Program Files\Git\bin\bash.exe"
    log(f"bash.exe 存在: {os.path.isfile(bash)}")
    try:
        os.remove(INSTALLER)
    except Exception:
        pass
log("结束")
