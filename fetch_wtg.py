# -*- coding: utf-8 -*-
"""下载 Rufus + Ventoy 到 tools/。"""
import json
import os
import shutil
import urllib.request
import zipfile

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
os.makedirs(TOOLS, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url, timeout=180, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  第{i + 1}次失败（{e.__class__.__name__}），重试…")
    raise last


def latest_asset(repo, name_filter):
    d = json.loads(fetch(f"https://api.github.com/repos/{repo}/releases/latest",
                         timeout=60))
    print(repo, "->", d["tag_name"])
    for a in d["assets"]:
        if name_filter(a["name"]):
            return a["browser_download_url"], a["name"]
    raise RuntimeError("asset not found")


# 1) Rufus portable（便携版文件名形如 rufus-4.15p.exe）
if not os.path.isfile(os.path.join(TOOLS, "rufus.exe")):
    import re
    try:
        url, name = latest_asset(
            "pbatard/rufus",
            lambda n: bool(re.fullmatch(r"rufus-[\d.]+p\.exe", n)))
    except Exception as e:  # noqa: BLE001
        print("API 失败，用固定版本 v4.15:", e)
        name = "rufus-4.15p.exe"
        url = ("https://github.com/pbatard/rufus/releases/"
               "download/v4.15/" + name)
    print("下载", name, "…")
    data = fetch(url)
    with open(os.path.join(TOOLS, "rufus.exe"), "wb") as f:
        f.write(data)
    print("rufus.exe", round(len(data) / 1048576, 2), "MB OK")
else:
    print("rufus.exe 已存在，跳过")

# 2) Ventoy windows zip
ventoy_done = os.path.isfile(os.path.join(TOOLS, "ventoy",
                                          "Ventoy2Disk.exe"))
if not ventoy_done:
    try:
        url, name = latest_asset(
            "ventoy/Ventoy",
            lambda n: "windows" in n.lower() and n.endswith(".zip"))
    except Exception as e:  # noqa: BLE001
        print("API 失败，用固定版本 v1.1.05:", e)
        name = "ventoy-1.1.05-windows.zip"
        url = ("https://github.com/ventoy/Ventoy/releases/"
               "download/v1.1.05/" + name)
    print("下载", name, "…")
    data = fetch(url)
    zpath = os.path.join(TOOLS, name)
    with open(zpath, "wb") as f:
        f.write(data)
    print("zip", round(len(data) / 1048576, 2), "MB，解压中…")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(TOOLS)
    os.remove(zpath)
    # zip 解出来是 tools/ventoy-x.x.xx/ 顶层目录，改名为 tools/ventoy
    top = os.path.splitext(name)[0]
    src = os.path.join(TOOLS, top)
    dst = os.path.join(TOOLS, "ventoy")
    if os.path.isdir(src):
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        os.rename(src, dst)
    print("Ventoy 解压到 tools/ventoy OK")
else:
    print("Ventoy 已存在，跳过")

print("\n全部完成")
for f in ["rufus.exe", "ventoy/Ventoy2Disk.exe"]:
    p = os.path.join(TOOLS, f)
    print(("  OK  " if os.path.isfile(p) else "  MISS") + " " + f)
