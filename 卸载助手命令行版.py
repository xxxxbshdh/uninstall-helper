"""卸载助手 命令行版 — 卸载软件/启动项/进程管理/服务管理/智能省电/路径清理/驱动助手。

用法：
  python 卸载助手命令行版.py                 # 进入交互菜单
  python 卸载助手命令行版.py brain           # 直接启动双脑一体 GUI
  python 卸载助手命令行版.py wifi [on|off|status|now|log|scan|add|del]  # WiFi守护：断网自动重连/没网自动换WiFi/开机自启
  python 卸载助手命令行版.py wifi scan              # 扫描周围WiFi
  python 卸载助手命令行版.py wifi add <名称> <密码>  # 加WiFi进自动切换池（--open为无密码）
  python 卸载助手命令行版.py wifi del <名称>         # 删除废WiFi配置
  python 卸载助手命令行版.py apps [关键词]    # 列出已安装软件
  python 卸载助手命令行版.py startup          # 列出启动项
  python 卸载助手命令行版.py procs            # 列出进程
  python 卸载助手命令行版.py procs kill <pid> # 结束进程（suspend/resume/prio 同理）
  python 卸载助手命令行版.py svcs [过滤词]    # 列出服务
  python 卸载助手命令行版.py svcs start|stop <服务名>
  python 卸载助手命令行版.py svcs set <服务名> <auto|auto_delayed|manual|disabled>
  python 卸载助手命令行版.py smart show|watch
  python 卸载助手命令行版.py clean <路径>     # 删除残留文件/目录（需确认）
  python 卸载助手命令行版.py drv hw|problems|list          # 硬件摘要/异常设备/驱动包
  python 卸载助手命令行版.py drv auto                       # 联网自动修复（微软官方源）
  python 卸载助手命令行版.py drv match [--dry]              # 按硬件ID搜微软驱动目录匹配安装
  python 卸载助手命令行版.py drv backup <目录>              # 备份全部第三方驱动
  python 卸载助手命令行版.py drv install <目录>             # 递归安装目录下全部 .inf 驱动
  python 卸载助手命令行版.py drv del <oemXX.inf> [--force]  # 删除驱动包
"""
import ctypes
import os
import subprocess
import sys
import time

import core

ts = lambda: time.strftime("%H:%M:%S")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> None:
    if getattr(sys, "frozen", False):
        args = list(sys.argv[1:])
    else:
        args = [os.path.abspath(sys.argv[0])] + list(sys.argv[1:])
    params = " ".join(f'"{a}"' for a in args)
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1)
    if rc > 32:
        sys.exit(0)
    print("!! 未获得管理员权限，部分功能（服务/系统目录清理）会失败")


def confirm(msg: str) -> bool:
    return input(f"{msg} (y/N)：").strip().lower() in ("y", "yes", "是")


def pause():
    input("\n回车返回…")


# ---------------------------------------------------------------- 已安装软件

def print_apps(entries):
    print(f"共 {len(entries)} 个")
    for i, a in enumerate(entries, 1):
        size = core.format_size(a.estimated_size_kb)
        print(f"{i:>4}. {a.name[:38]:<40} {a.version[:14]:<16} {size:<10} {a.publisher[:24]}")


def uninstall_flow(entries):
    idx = input("输入要卸载的序号（回车返回）：").strip()
    if not idx.isdigit() or not (1 <= int(idx) <= len(entries)):
        return
    app = entries[int(idx) - 1]
    running = core.list_running_procs_in_dir(app.install_location) if app.install_location else []
    if running:
        print(f"!! 软件正在运行（{len(running)} 个进程），建议先在进程管理里结束")
    if not confirm(f"确定卸载「{app.name}」？"):
        return
    if not core.launch_uninstall_and_wait(app):
        print("自带卸载程序执行失败或超时")
    if core.uninstall_key_exists(app):
        if confirm("注册表卸载键还在，强制清理？（删除卸载键+安装目录）"):
            info = core.force_uninstall(app)
            print("强制卸载结果:", info)
            if confirm("顺带删除注册表卸载键？"):
                print("删除注册表键:", core.delete_app_registry_key(app))
    left = core.scan_leftover_candidates(app.name, app.publisher, app.install_location)
    if not left:
        print("未发现残留文件")
        return
    print(f"发现 {len(left)} 个残留：")
    for p in left:
        print("  -", p)
    if confirm("全部删除这些残留？"):
        for p in left:
            print(f"  删除 {p}: {'成功' if core.delete_path(p) else '失败'}")


def apps_menu(keyword: str = ""):
    entries = core.scan_installed_apps()
    while True:
        if keyword:
            k = keyword.lower()
            entries = [a for a in core.scan_installed_apps()
                       if k in a.name.lower() or k in a.publisher.lower()]
        else:
            entries = core.scan_installed_apps()
        print_apps(entries)
        cmd = input("\n回车=重新扫描 / u=卸载 / s=搜索 / q=返回：").strip().lower()
        if cmd == "q":
            return
        if cmd == "u":
            uninstall_flow(entries)
        elif cmd == "s":
            keyword = input("关键词：").strip()
        else:
            keyword = ""


# ---------------------------------------------------------------- 启动项

def startup_menu():
    while True:
        items = core.scan_startup_items()
        print(f"共 {len(items)} 个启动项")
        for i, it in enumerate(items, 1):
            state = "启用" if it.enabled else "禁用"
            print(f"{i:>3}. [{state}] {it.name[:32]:<34} {it.location}")
            print(f"     {it.command[:90]}")
        cmd = input("\n序号=切换启用/禁用 / d 序号=删除 / q=返回：").strip().lower()
        if cmd == "q":
            return
        parts = cmd.split()
        try:
            idx = int(parts[-1]) - 1
            item = items[idx]
        except (ValueError, IndexError):
            continue
        if parts[0] == "d":
            if confirm(f"删除启动项「{item.name}」？"):
                print("删除:", core.delete_startup_item(item))
        elif confirm(f"{"启用" if item.enabled else "禁用"}「{item.name}」？"):
            print("设置:", core.set_startup_enabled(item, not item.enabled))


# ---------------------------------------------------------------- 进程

def print_procs():
    procs = core.list_processes()
    print(f"共 {len(procs)} 个进程，按 CPU 排序（前 40）：")
    print(f"{"PID":>8}  {"CPU%":>6}  {"内存":>10}  进程名")
    for p in procs[:40]:
        mark = " ←前台" if p.pid == core.get_foreground_pid() else ""
        print(f"{p.pid:>8}  {p.cpu_percent:>6.1f}  {core.format_bytes(p.mem_bytes):>10}  {p.name[:44]}{mark}")
    return procs


def procs_menu():
    while True:
        print_procs()
        cmd = input("\nk PID=结束 / s PID=挂起 / r PID=恢复 / p PID 级别(below|idle|normal) / 回车刷新 / q=返回：").strip().lower()
        if cmd == "q":
            return
        parts = cmd.split()
        if not parts:
            continue
        try:
            pid = int(parts[1])
        except (IndexError, ValueError):
            continue
        if parts[0] == "k":
            if confirm(f"结束进程 {pid}？"):
                ok, err = core.kill_process(pid)
                print("结果:", "成功" if ok else f"失败：{err}")
        elif parts[0] == "s":
            print("挂起:", core.suspend_process(pid))
        elif parts[0] == "r":
            print("恢复:", core.resume_process(pid))
        elif parts[0] == "p" and len(parts) >= 3:
            cls = {"below": core.BELOW_NORMAL_PRIORITY_CLASS, "idle": core.IDLE_PRIORITY_CLASS,
                   "normal": core.NORMAL_PRIORITY_CLASS}.get(parts[2])
            print("优先级:", "成功" if cls and core.set_process_priority(pid, cls) else "失败")


# ---------------------------------------------------------------- 服务

KIND_ALIAS = {"自动": "auto", "自动延迟": "auto_delayed", "手动": "manual", "禁用": "disabled"}


def normalize_kind(text: str) -> str | None:
    text = text.strip().lower()
    return KIND_ALIAS.get(text) or KIND_ALIAS.get(text.replace("_", "")) or \
        (text if text in ("auto", "auto_delayed", "manual", "disabled") else None)


def print_services(filt: str = ""):
    infos = core.list_services()
    if filt:
        k = filt.lower()
        infos = [s for s in infos if k in s.name.lower() or k in s.display.lower()]
    print(f"共 {len(infos)} 个服务：")
    for s in infos:
        state = core.STATE_LABELS.get(s.state, str(s.state))
        start = core.START_LABELS.get(s.start, s.start)
        guard = " [关键]" if s.protected else ""
        print(f"  {s.name[:34]:<36} {state:<6} {start:<10}{guard} {s.display[:36]}")
    return infos


def svcs_menu():
    while True:
        filt = input("\n过滤词（回车=全部 / q=返回）：").strip()
        if filt.lower() == "q":
            return
        print_services(filt)
        cmd = input("start 名称 / stop 名称 / set 名称 类型 / 回车刷新：").strip()
        if not cmd:
            continue
        parts = cmd.split()
        if len(parts) < 2:
            continue
        action, name = parts[0].lower(), parts[1]
        if action == "start":
            ok, err = core.service_start(name)
            print("结果:", "成功" if ok else f"失败：{err}")
        elif action == "stop":
            ok, err = core.service_stop(name)
            print("结果:", "成功" if ok else f"失败：{err}")
        elif action == "set" and len(parts) >= 3:
            kind = normalize_kind(parts[2])
            if not kind:
                print("启动类型应为：auto / auto_delayed / manual / disabled")
                continue
            ok, err = core.service_set_start(name, kind)
            print("结果:", "成功" if ok else f"失败：{err}")


# ---------------------------------------------------------------- 智能省电

def smart_show():
    cfg = core.load_manager_config()
    print(f"空闲判定 {cfg.get('idle_seconds')} 秒 | CPU 阈值 {cfg.get('cpu_threshold')}%")
    print("托管进程：")
    for n, act in cfg.get("managed_processes", {}).items():
        print(f"  {n}  ->  {"空闲挂起" if act == "suspend" else "降低优先级"}")
    print("托管服务：")
    for n, info in cfg.get("managed_services", {}).items():
        if isinstance(info, dict):
            extra = f"（绑定 {info.get('watch')}）" if info.get("watch") else ""
            print(f"  {n}  ->  {"按需启动" if info.get("mode") == "ondemand" else "始终禁用"}{extra}")
    return cfg


def smart_menu():
    while True:
        cfg = smart_show()
        cmd = input("\nproc add 名称 [suspend|priority] / proc del 名称 / "
                    "svc add 名称 [进程名] / svc del 名称 / idle 秒数 / cpu 阈值 / "
                    "watch=开看门狗 / q=返回：").strip()
        if cmd == "q":
            return
        if cmd == "watch":
            run_watchdog(cfg)
            continue
        parts = cmd.split()
        if len(parts) < 2:
            continue
        if parts[0] == "proc" and parts[1] == "add" and len(parts) >= 3:
            name = parts[2]
            action = parts[3] if len(parts) >= 4 and parts[3] in ("suspend", "priority") else "suspend"
            cfg.setdefault("managed_processes", {})[name] = action
            core.save_manager_config(cfg)
        elif parts[0] == "proc" and parts[1] == "del":
            cfg.get("managed_processes", {}).pop(parts[2], None)
            core.save_manager_config(cfg)
        elif parts[0] == "svc" and parts[1] == "add" and len(parts) >= 3:
            watch = parts[3] if len(parts) >= 4 else ""
            cfg.setdefault("managed_services", {})[parts[2]] = {
                "mode": "ondemand" if watch else "always_off", "watch": watch}
            core.save_manager_config(cfg)
        elif parts[0] == "svc" and parts[1] == "del":
            cfg.get("managed_services", {}).pop(parts[2], None)
            core.save_manager_config(cfg)
        elif parts[0] == "idle":
            cfg["idle_seconds"] = max(10, int(parts[1]))
            core.save_manager_config(cfg)
        elif parts[0] == "cpu":
            cfg["cpu_threshold"] = float(parts[1])
            core.save_manager_config(cfg)


def run_watchdog(cfg: dict):
    """前台看门狗：托管进程空闲挂起/降优先级，托管服务按需启停。Ctrl+C 停止。"""
    print(f"[{ts()}] 智能省电看门狗已开启，Ctrl+C 停止并恢复托管进程…")
    proc_state: dict[int, str] = {}
    idle_since: dict[int, float] = {}
    svc_idle: dict[str, float] = {}
    hinted: set[str] = set()
    last_svc = 0.0
    last_names = set(cfg.get("managed_processes", {}))
    try:
        while True:
            now = time.monotonic()
            idle_sec = max(10, int(cfg.get("idle_seconds", 60)))
            thr = float(cfg.get("cpu_threshold", 1.0))
            procs = core.list_processes()
            fg = core.get_foreground_pid()
            by_name: dict[str, list] = {}
            alive: set[int] = set()
            for p in procs:
                alive.add(p.pid)
                by_name.setdefault(p.name.lower(), []).append(p)

            mp = cfg.get("managed_processes", {})
            for removed in last_names - set(mp):
                for p in by_name.get(removed.lower(), []):
                    st = proc_state.pop(p.pid, None)
                    if st == "suspended":
                        core.resume_process(p.pid)
                        print(f"  [{ts()}] {removed}({p.pid}) 已恢复（取消托管）")
                    elif st == "idle_priority":
                        core.set_process_priority(p.pid, core.NORMAL_PRIORITY_CLASS)
            last_names = set(mp)

            for name, action in mp.items():
                if action not in ("suspend", "priority"):
                    continue
                for p in by_name.get(str(name).lower(), []):
                    active = (p.pid == fg) or (p.cpu_percent >= thr)
                    if active:
                        idle_since.pop(p.pid, None)
                    else:
                        idle_since.setdefault(p.pid, now)
                    idle = now - idle_since.get(p.pid, now) >= idle_sec
                    if action == "suspend":
                        want = "suspended" if (idle and not active) else "normal"
                        if proc_state.get(p.pid, "normal") != want:
                            fn = core.suspend_process if want == "suspended" else core.resume_process
                            if fn(p.pid):
                                proc_state[p.pid] = want
                                print(f"  [{ts()}] {name}({p.pid}) {"已挂起省资源" if want == "suspended" else "已恢复"}")
                    else:
                        want = "idle_priority" if (idle and not active) else "normal"
                        if proc_state.get(p.pid, "normal") != want:
                            cls = (core.BELOW_NORMAL_PRIORITY_CLASS if want == "idle_priority"
                                   else core.NORMAL_PRIORITY_CLASS)
                            if core.set_process_priority(p.pid, cls):
                                proc_state[p.pid] = want
                                print(f"  [{ts()}] {name}({p.pid}) 优先级已{"降低" if want == "idle_priority" else "恢复"}")
            for pid in list(proc_state):
                if pid not in alive:
                    proc_state.pop(pid, None)
                    idle_since.pop(pid, None)

            if now - last_svc >= 15:
                last_svc = now
                for name, info in dict(cfg.get("managed_services", {})).items():
                    if not isinstance(info, dict):
                        continue
                    mode = info.get("mode", "always_off")
                    watch = str(info.get("watch") or "").strip()
                    st = core.service_query(name)
                    if st is None:
                        continue
                    if mode == "ondemand" and watch:
                        if core.process_exists(watch):
                            svc_idle.pop(name, None)
                            if not st.running:
                                if st.start == "disabled":
                                    ok, err = core.service_set_start(name, info.get("orig_start") or "auto")
                                    if not ok:
                                        print(f"  [{ts()}] 恢复 {name} 启动类型失败：{err}")
                                        continue
                                ok, err = core.service_start(name)
                                print(f"  [{ts()}] 检测到 {watch}，服务 {name} 已自动启动"
                                      if ok else f"  [{ts()}] 启动服务 {name} 失败：{err}")
                        elif st.running or st.start != "disabled":
                            since = svc_idle.setdefault(name, now)
                            if now - since >= idle_sec:
                                if "orig_start" not in info and st.start != "disabled":
                                    info["orig_start"] = st.start
                                    core.save_manager_config(cfg)
                                ok, err = core.service_stop(name)
                                ok2, err2 = core.service_set_start(name, "disabled")
                                print(f"  [{ts()}] {watch} 未运行超 {idle_sec} 秒，服务 {name} 已停用"
                                      if ok and ok2 else f"  [{ts()}] 停用服务 {name} 失败：{err or err2}")
                                svc_idle.pop(name, None)
                        else:
                            svc_idle.pop(name, None)
                    else:
                        if mode == "ondemand" and name not in hinted:
                            hinted.add(name)
                            print(f"  [{ts()}] 服务 {name} 未绑定进程，按\"始终禁用\"处理")
                        if st.running or st.start != "disabled":
                            core.service_stop(name)
                            ok, err = core.service_set_start(name, "disabled")
                            print(f"  [{ts()}] 服务 {name} 已停止并禁用" if ok or not st.running
                                  else f"  [{ts()}] 禁用服务 {name} 失败：{err}")
            time.sleep(5)
    except KeyboardInterrupt:
        print()
    for pid, st in proc_state.items():
        if st == "suspended":
            core.resume_process(pid)
        core.set_process_priority(pid, core.NORMAL_PRIORITY_CLASS)
    print(f"[{ts()}] 看门狗已停止，托管进程已恢复")


# ---------------------------------------------------------------- 驱动助手

def _oem_codepage() -> str:
    """子进程（pnputil/powershell）管道输出使用的 OEM 代码页。"""
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "gbk"


def _run_decode(cmd: list[str]) -> str:
    """运行命令并按控制台代码页解码输出。"""
    r = subprocess.run(cmd, capture_output=True)
    enc = _oem_codepage()
    text = (r.stdout or b"").decode(enc, errors="replace")
    err = (r.stderr or b"").decode(enc, errors="replace")
    return text + ("\n" + err if err.strip() else "")


def _ps(script: str) -> str:
    """调用 PowerShell 执行 CIM/WMI 查询。"""
    return _run_decode(["powershell", "-NoProfile", "-Command", script])


def drv_hw():
    """硬件与驱动版本摘要。"""
    out = _ps(
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores |"
        " Format-List | Out-String -Width 120;"
        "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,Status |"
        " Format-List | Out-String -Width 120;"
        "Get-CimInstance Win32_NetworkAdapter -Filter 'Physical=True' |"
        " Select-Object Name,NetEnabled | Format-List | Out-String -Width 120;"
        "Get-CimInstance Win32_SoundDevice | Select-Object -First 3 Name,Status |"
        " Format-List | Out-String -Width 120;"
        "Get-CimInstance Win32_PnPEntity -Filter \"Name like '%蓝牙%'\" |"
        " Select-Object -First 2 Name | Format-List | Out-String -Width 120")
    print(out.strip() or "查询失败")


def drv_problems():
    """扫描当前存在问题的设备（黄色感叹号）。"""
    out = _ps(
        "$ErrorActionPreference='SilentlyContinue';"
        "$d = Get-PnpDevice -PresentOnly | Where-Object { $_.Status -ne 'OK' };"
        "if ($d) { $d | Select-Object Status,Class,FriendlyName,InstanceId |"
        " Format-Table -AutoSize | Out-String -Width 160 }"
        " else { '所有设备状态正常，无异常设备' }")
    print(out.strip())


# 通过 Windows Update Agent COM 联网搜索/下载/安装驱动（微软官方源，免会员）
WU_AUTO_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
try {
  $session  = New-Object -ComObject Microsoft.Update.Session
  $searcher = $session.CreateUpdateSearcher()
  $result   = $searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
  $updates  = $result.Updates
  if ($updates.Count -eq 0) { Write-Output 'RESULT NONE'; exit 0 }
  Write-Output ("RESULT FOUND " + $updates.Count)
  for ($i = 0; $i -lt $updates.Count; $i++) {
    Write-Output ("ITEM " + $updates.Item($i).Title)
  }
  $coll = New-Object -ComObject Microsoft.Update.UpdateColl
  foreach ($u in $updates) {
    try { if (-not $u.EulaAccepted) { $u.AcceptEula() } } catch {}
    if ($u.InstallationBehavior.CanRequestUserInput) {
      Write-Output ("SKIP " + $u.Title); continue
    }
    [void]$coll.Add($u)
  }
  if ($coll.Count -eq 0) { Write-Output 'RESULT SKIPPED-ALL'; exit 0 }
  $downloader = $session.CreateUpdateDownloader()
  $downloader.Updates = $coll
  $dr = $downloader.Download()
  Write-Output ("DOWNLOAD " + $dr.ResultCode)
  $installer = $session.CreateUpdateInstaller()
  $installer.Updates = $coll
  $ir = $installer.Install()
  Write-Output ("INSTALL " + $ir.ResultCode)
  for ($i = 0; $i -lt $coll.Count; $i++) {
    $r2 = $ir.GetUpdateResult($i)
    Write-Output ("ITEM-RESULT " + $r2.ResultCode + " " + $coll.Item($i).Title)
  }
} catch {
  Write-Output ("ERROR " + $_.Exception.Message)
  exit 1
}
'''


def _ps_stream(script: str) -> int:
    """流式执行 PowerShell 脚本，逐行实时打印输出，返回退出码。"""
    r = subprocess.Popen(["powershell", "-NoProfile", "-Command", script],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    enc = _oem_codepage()
    for raw in r.stdout:
        line = raw.decode(enc, errors="replace").rstrip("\r\n")
        if line:
            print("  " + line, flush=True)
    r.wait()
    return r.returncode


def drv_auto():
    """自动修复：微软官方源搜索 → 下载 → 安装 → 复查。"""
    print("第 1 步：当前异常设备：")
    drv_problems()
    print("\n第 2 步：连接微软官方更新源，搜索并安装驱动更新"
          "（搜索可能需要 1-5 分钟，请耐心等待）…")
    rc = _ps_stream(WU_AUTO_SCRIPT)
    print("\n（结果码含义：2=成功 3=部分成功 4=失败 5=中止）")
    if rc != 0:
        print("!! 联网修复执行出错：需要管理员权限和网络。请确认已提权后重试。")
    print("\n第 3 步：复查异常设备：")
    drv_problems()
    print("\n提示：若仍有设备带感叹号，用菜单 5 指定驱动包目录安装。")


# ------------------------------------------------ 按硬件ID匹配驱动建议

import json
import re


def _problem_devices():
    """返回异常设备列表 [{Status,Class,FriendlyName,InstanceId}]。"""
    out = _ps(
        "$d = Get-PnpDevice -PresentOnly | Where-Object { $_.Status -ne 'OK' };"
        "@($d | Select-Object Status,Class,FriendlyName,InstanceId |"
        " ConvertTo-Json)")
    out = out.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return [data] if isinstance(data, dict) else data


# 常见硬件 ID 片段 → 建议驱动名称（Intel/AMD/NVIDIA/Realtek 为主）
# 用于把 17 个零散的异常设备归并到少数几个驱动包
_HWID_DRIVER_HINTS = [
    # Intel Elkhart Lake (N4500) / Tiger Lake 芯片组设备
    (r"PCI\\VEN_8086&DEV_4E(03|11|12|13|14)", "Intel Serial IO Driver（串口/IO/GPIO 控制器）"),
    (r"PCI\\VEN_8086&DEV_4D(A3|A4|A8|C3|C5|C8|CB|D0)",
     "Intel Chipset Device Software（芯片组 + SM 总线 + SMBus）"),
    (r"PCI\\VEN_8086&DEV_4D(A8|C3|D0)", "Intel Serial IO Driver（UART/SPI）"),
    (r"PCI\\VEN_8086&DEV_4E(11|12|13)", "Intel Serial IO Driver（GPIO）"),
    (r"PCI\\VEN_8086&DEV_4E(03)", "Intel SDA Driver（数据捕获控制器）"),
    (r"PCI\\VEN_8086&DEV_4D(E9|AB|EA|E8|C5|EB|DE)",
     "Intel Chipset Device Software（芯片组系统设备）"),
    # Intel 其他平台
    (r"PCI\\VEN_8086&DEV_5[0-9A-F]{3}", "Intel Chipset / Serial IO Driver"),
    (r"PCI\\VEN_8086&DEV_6[0-9A-F]{3}", "Intel Chipset / Serial IO Driver"),
    # ACPI / HID 热管理
    (r"ACPI\\INT(34|C10[0-9A-F])", "Intel HID Event Filter Driver + Intel Thermal Driver"),
    # 显卡
    (r"PCI\\VEN_8086&DEV_56A[0-9]|PCI\\VEN_8086&DEV_59[0-9A-F]{2}",
     "Intel Graphics Driver（核显驱动）"),
    (r"PCI\\VEN_10DE", "NVIDIA Graphics Driver"),
    (r"PCI\\VEN_1002", "AMD Graphics Driver"),
    # 网卡/蓝牙
    (r"PCI\\VEN_8086&DEV_A0[0-9A-F]{2}|PCI\\VEN_8086&DEV_3D",
     "Intel Ethernet Driver（网卡）"),
    (r"USB\\VID_8087", "Intel Bluetooth Driver（蓝牙）"),
    # 声卡
    (r"PCI\\VEN_8086&DEV_4DC8|PCI\\VEN_8086&DEV_51A[0-9]",
     "Intel Smart Sound Technology Driver（音频）"),
    # Realtek
    (r"PCI\\VEN_10EC", "Realtek Ethernet / Audio Driver"),
    # AMD
    (r"PCI\\VEN_1022&DEV_7[0-9A-F]{3}", "AMD Chipset Driver + AMD Audio Driver"),
    # NVIDIA 芯片组
    (r"PCI\\VEN_10DE&DEV_0[0-9A-F]{3}", "NVIDIA Chipset Driver"),
]


def _suggest_driver(instance_id: str) -> str:
    """根据硬件 ID 建议驱动名。"""
    iid = (instance_id or "").upper()
    for pat, hint in _HWID_DRIVER_HINTS:
        if re.search(pat, iid):
            return hint
    # 厂商级兜底
    m = re.match(r"(\w+)\\VEN_([0-9A-F]{4})", iid)
    if m:
        ven = m.group(2)
        known = {"8086": "Intel 对应驱动", "10DE": "NVIDIA 对应驱动",
                 "1002": "AMD/ATI 对应驱动", "10EC": "Realtek 对应驱动"}
        return known.get(ven, f"VEN_{ven} 厂商对应驱动")
    return "到主板/整机官网下载该型号的全套驱动包"


def _open_url(url: str):
    """绕过 360 劫持，用系统默认浏览器打开。"""
    try:
        subprocess.Popen(["rundll32", "url.dll,FileProtocolHandler", url])
    except OSError:
        import webbrowser
        webbrowser.open(url)


def drv_match(dry: bool = False):
    """按硬件 ID 推荐对应驱动，并引导用户安装。

    流程：扫描 → 按硬件 ID 归类推荐驱动名 → 打开 Intel DSA 自动检测页
    （Intel N4500 等平台直接让 Intel 官网自动匹配合适的驱动）→ 用户下载后
    回到这里用菜单 5 安装。
    """
    print("第 1 步：扫描异常设备…")
    devices = _problem_devices()
    if not devices:
        print("  所有设备状态正常，无需修复")
        return
    for d in devices:
        print(f"  [{d.get('Class') or '?'}] {d.get('FriendlyName') or '?'}"
              f"  {d.get('InstanceId')}")

    # 按建议驱动名归类
    groups = {}          # 建议驱动名 -> [硬件ID列表]
    for d in devices:
        iid = d.get("InstanceId") or ""
        hint = _suggest_driver(iid)
        groups.setdefault(hint, []).append(iid)

    print(f"\n第 2 步：{len(devices)} 个异常设备归并为 {len(groups)} 个驱动包：")
    for hint, iids in groups.items():
        print(f"  ▶ {hint}")
        for iid in iids:
            print(f"      {iid}")

    if dry:
        print("\n（--dry 演练模式：未打开浏览器）")
        return

    ans = input("\n推荐两种安装方式：\n"
                "  1) Intel DSA 在线检测（如果你的硬件全是 Intel，"
                "让 Intel 官网自动匹配安装最省心）\n"
                "  2) 驱动之家（国内最全的第三方驱动站，按硬件ID搜）\n"
                "  输入 1 打开 Intel DSA，输入 2 打开驱动之家搜索，"
                "或直接回车不打开浏览器： ").strip()

    if ans == "1":
        # Intel DSA 在线检测页（Intel N4500 等平台自动识别）
        url = "https://www.intel.com/content/www/us/en/support/detect.html"
        print(f"正在打开 Intel DSA 自动检测页：{url}")
        print("在浏览器里点“Launch Intel Driver & Support Assistant”"
              "下载安装，让它自动检测并推荐驱动。")
        _open_url(url)
    elif ans == "2":
        # 驱动之家按硬件 ID 搜索（把最常见的硬件 ID 直接拼好）
        for hint, iids in groups.items():
            first_hwid = iids[0]
            # 提取 PCI\VEN_xxx&DEV_xxx 部分作为搜索关键词
            parts = first_hwid.upper().split("\\")
            if len(parts) >= 2:
                search_q = parts[1]
            else:
                search_q = first_hwid
            url = ("https://so.mydrivers.com/drivers/"
                   + urllib.parse.quote(search_q))
            print(f"  {hint} → {url}")
        input("复制上面的链接粘贴到浏览器，逐个下载；"
              "或让我帮你打开第一个（回车继续，q 跳过）： ").strip().lower()
        if ans != "q":
            first_key = next(iter(groups))
            first_hwid = groups[first_key][0]
            parts = first_hwid.upper().split("\\")
            search_q = parts[1] if len(parts) >= 2 else first_hwid
            _open_url("https://so.mydrivers.com/drivers/"
                      + urllib.parse.quote(search_q))
            print("已打开第一个搜索页，在浏览器里找对应驱动下载。")

    print("\n下载完后，把所有 .cab/.zip/.exe 驱动包放进同一个文件夹，"
          "然后回到卸载助手菜单 → 5（安装驱动）→ 输入那个文件夹的路径，"
          "pnputil 会自动匹配安装到所有对应的设备上。")
    print("装完建议重启一次系统让驱动生效。")


def drv_list():
    """列出已安装的第三方驱动包（oemXX.inf）。"""
    out = _run_decode(["pnputil", "/enum-drivers"])
    print(out.strip() or "pnputil 查询失败")


def drv_backup(target: str):
    """把当前系统里全部第三方驱动备份到目录。"""
    target = target.strip().strip('"')
    os.makedirs(target, exist_ok=True)
    print(f"正在导出全部第三方驱动到 {target}，可能需要几分钟…")
    out = _run_decode(["pnputil", "/export-driver", "*", target])
    ok = os.listdir(target)
    print(out.strip())
    print(f"完成：{len(ok)} 项" if ok else "没有导出任何驱动")


def drv_install(source: str):
    """递归安装目录下所有 .inf 驱动（万能驱动包解压后同样适用）。"""
    source = source.strip().strip('"')
    if not os.path.isdir(source):
        print("目录不存在")
        return
    print(f"正在递归安装 {source} 下的驱动，请稍候…")
    out = _run_decode(["pnputil", "/add-driver", os.path.join(source, "*.inf"),
                       "/subdirs", "/install"])
    print(out.strip())


def drv_del(package: str, force: bool):
    """删除一个第三方驱动包。"""
    package = package.strip().strip('"')
    if not package.lower().endswith(".inf"):
        package += ".inf"
    cmd = ["pnputil", "/delete-driver", package]
    if force:
        cmd.append("/force")
    out = _run_decode(cmd)
    print(out.strip())


DRV_MENU = """
---------- 驱动助手（pnputil 免费版，无会员）----------
 1. 硬件摘要（CPU/显卡/网卡/声卡/蓝牙）
 2. 异常设备扫描（黄色感叹号）
 3. 第三方驱动包列表
 4. 备份驱动（导出全部第三方驱动）
 5. 安装驱动（递归安装某目录的 .inf）
 6. 删除驱动包
 7. 自动修复（联网搜微软官方源，自动装缺失驱动）
 8. 自动匹配安装（按硬件ID精确搜微软驱动目录）
 0. 返回
------------------------------------------------------"""


def drv_menu():
    while True:
        print(DRV_MENU)
        c = input("选择: ").strip()
        if c == "1":
            drv_hw()
            pause()
        elif c == "2":
            drv_problems()
            pause()
        elif c == "3":
            drv_list()
            pause()
        elif c == "4":
            drv_backup(input("备份到目录："))
            pause()
        elif c == "5":
            drv_install(input("驱动包目录："))
            pause()
        elif c == "6":
            drv_del(input("驱动包名（如 oem12.inf）："),
                    "--force" in input("强制卸载（设备将无驱动，加 --force）？输入 --force 确认："))
            pause()
        elif c == "7":
            drv_auto()
            pause()
        elif c == "8":
            drv_match(dry=False)
            pause()
        elif c in ("0", "q"):
            return


# ---------------------------------------------------------------- 路径清理

def clean_path(path: str):
    path = path.strip().strip('"')
    if not os.path.exists(path):
        print("路径不存在")
        return
    print("将删除：", path)
    if not confirm("确认删除且不可恢复？"):
        return
    print("结果:", "成功" if core.delete_path(path) else "失败（权限不足或被占用）")


# ---------------------------------------------------------------- 下载助手

"""用 Python 标准库 + Windows BITS 实现的下载管理器，替代 IDM 等付费工具。

- 前台下载：多线程切片（HTTP Range），实时进度条
- 后台下载：Start-BitsTransfer，断点续传、断网自动重试
- 批量下载：URL 列表文件
- 测速：下载已知大文件前几 MB
"""

import threading


def _fmt_size(n):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_speed(n):
    return _fmt_size(n) + "/s"


def _http_head(url):
    """发 HEAD 请求拿 Content-Length，失败则 GET 取前 1 字节。"""
    import urllib.request
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0",
                                          "Range": "bytes=0-0"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        cl = resp.headers.get("Content-Length")
        ar = resp.headers.get("Accept-Ranges", "")
        return int(cl) if cl else 0, ar == "bytes", resp.geturl()
    except Exception:  # noqa: BLE001
        try:
            req2 = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"})
            resp = urllib.request.urlopen(req2, timeout=15)
            # Content-Range: bytes 0-0/total
            cr = resp.headers.get("Content-Range", "")
            total = 0
            m = re.search(r"/(\d+)$", cr)
            if m:
                total = int(m.group(1))
            cl = resp.headers.get("Content-Length")
            if not total and cl:
                total = int(cl)
            return total, bool(cr), resp.geturl()
        except Exception:  # noqa: BLE001
            return 0, False, url


def _download_chunk(url, start, end, dest_path, buf, ev):
    """下载 [start, end] 区间到 buf 对应位置，线程安全。"""
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0",
                      "Range": f"bytes={start}-{end}"})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = resp.read()
        buf[start:start + len(data)] = data
    except Exception as e:  # noqa: BLE001
        buf[start:start] = b"__ERR__" + str(e).encode()
    finally:
        ev.set()


def _download_threaded(url, dest, threads=8, show_progress=True):
    """多线程切片下载，返回 (是否成功, 最终路径)。"""
    total, accept_ranges, final_url = _http_head(url)
    if not final_url.lower().startswith("http"):
        final_url = url
    if not dest:
        name = os.path.basename(final_url.split("?")[0]) or "download.bin"
        dest = os.path.join(os.path.expanduser("~"), "Downloads", name)
    dest_dir = os.path.dirname(dest)
    os.makedirs(dest_dir, exist_ok=True)

    if not accept_ranges or total <= 0:
        # 服务器不支持 Range，单线程下载
        if show_progress:
            print(f"  单线程下载（服务器不支持 Range）→ {dest}")
        return _download_single(final_url, dest)

    if show_progress:
        print(f"  目标: {final_url}")
        print(f"  大小: {_fmt_size(total)}  线程: {threads}  →  {dest}")

    buf = bytearray(total)
    events = []
    chunk_size = (total + threads - 1) // threads
    t0 = time.time()
    for i in range(threads):
        start = i * chunk_size
        end = min(start + chunk_size - 1, total - 1)
        if start >= total:
            break
        ev = threading.Event()
        events.append((start, end, ev))
        t = threading.Thread(target=_download_chunk,
                             args=(final_url, start, end, dest, buf, ev))
        t.daemon = True
        t.start()

    # 进度显示
    done_sizes = [0] * len(events)
    while True:
        ok = sum(1 for _, _, ev in events if ev.is_set())
        for idx, (s, e, _) in enumerate(events):
            done_sizes[idx] = max(0,
                                  e - s + 1 if events[idx][2].is_set()
                                  else 0)
        done = sum(done_sizes)
        pct = done * 100 / total
        elapsed = time.time() - t0
        speed = done / elapsed if elapsed > 0 else 0
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        if show_progress:
            print(f"  [{bar}] {pct:5.1f}%  {_fmt_size(done)}/{_fmt_size(total)}"
                  f"  {_fmt_speed(speed)}\r", end="", flush=True)
        if ok == len(events):
            break
        time.sleep(0.3)

    print()
    # 写入文件
    with open(dest, "wb") as f:
        f.write(buf)
    elapsed = time.time() - t0
    print(f"  完成 ✓  总耗时 {elapsed:.1f}s  平均速度 {_fmt_speed(total / elapsed)}")
    return True, dest


def _download_single(url, dest):
    """单线程下载（无 Range 支持时的回退）。"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=30)
    total = int(resp.headers.get("Content-Length") or 0)
    with open(dest, "wb") as f:
        t0 = time.time()
        got = 0
        chunk = 8192
        while True:
            data = resp.read(chunk)
            if not data:
                break
            f.write(data)
            got += len(data)
            pct = got * 100 / total if total else 0
            bar_len = 30
            filled = int(bar_len * pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            elapsed = time.time() - t0
            speed = got / elapsed if elapsed > 0 else 0
            print(f"  [{bar}] {pct:5.1f}%  {_fmt_size(got)}/{_fmt_size(total) or '?'}"
                  f"  {_fmt_speed(speed)}\r", end="", flush=True)
        print()
    return True, dest


def dl_get(url, dest=None, threads=8):
    """前台多线程下载一个 URL。"""
    if not url:
        print("请提供 URL")
        return
    ok, path = _download_threaded(url, dest, threads=threads)
    if ok:
        print(f"文件已保存: {path}")
    else:
        print("下载失败")


def dl_batch(urls_file, dest_dir=None):
    """批量下载：urls_file 每行一个 URL，或 URL|文件名。"""
    if not os.path.isfile(urls_file):
        print("列表文件不存在:", urls_file)
        return
    if not dest_dir:
        dest_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(dest_dir, exist_ok=True)
    with open(urls_file, encoding="utf-8", errors="replace") as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    print(f"共 {len(urls)} 个下载任务，保存到 {dest_dir}")
    for i, line in enumerate(urls, 1):
        if "|" in line:
            url, name = line.split("|", 1)
            url, name = url.strip(), name.strip()
        else:
            url = line
            name = None
        dest = os.path.join(dest_dir, name) if name else None
        print(f"\n[{i}/{len(urls)}] {url}")
        dl_get(url, dest)


def dl_bg(url, dest=None):
    """后台下载（BITS 服务，支持断点续传）。"""
    if not dest:
        name = os.path.basename(url.split("?")[0]) or "download.bin"
        dest = os.path.join(os.path.expanduser("~"), "Downloads", name)
    print(f"后台下载已提交（BITS 服务）：{url}")
    print(f"目标路径: {dest}")
    print("下载完成后用 'bitsadmin /complete' 或下面的 'dl pending' 查看")
    r = _ps(
        f"Import-Module BitsTransfer -ErrorAction SilentlyContinue;"
        f"$job = Start-BitsTransfer -Source '{url}' -Destination '{dest}' "
        f"-Asynchronous -Description '卸载助手下载'; "
        f"$job.JobId.ToString()")
    jobid = r.strip()
    print(f"任务 ID: {jobid}")


def dl_pending():
    """查看所有 BITS 下载任务状态。"""
    out = _ps(
        "Get-BitsTransfer -AllUsers -ErrorAction SilentlyContinue | "
        "Select DisplayName, JobState, BytesTransferred, BytesTotal, @{N='速度(KB/s)';"
        "E={ if($_.JobState -eq 'Transferring'){[math]::Round($_.BytesPerSecond/1KB,1)} "
        "else{'-'} }} | Format-Table -AutoSize")
    print(out if out.strip() else "(没有待处理的 BITS 下载任务)")


def dl_speedtest(url=None):
    """下载测速（默认用阿里云 CDN 小文件，国内可达）。"""
    # 国内可达的测速源（前三个优先，依次尝试）
    defaults = [
        "https://dl.google.com/sdk/android/repo/addon2.xml",   # Google DNS 文件，小且稳定
        "https://mirrors.aliyun.com/alpine/latest-stable/main/x86_64/APKINDEX.tar.gz",
        "https://repo.huaweicloud.com/openeuler/openEuler-24.03-LTS/ISO/openEuler-24.03-LTS-x86_64-dvd.iso",
        "https://speed.hetzner.de/10MB.bin",                    # 国外备用
    ]
    urls_to_try = [url] if url else defaults
    import urllib.request
    for test_url in urls_to_try:
        print(f"测速地址: {test_url}")
        print("正在下载前 4MB 测速…")
        t0 = time.time()
        try:
            req = urllib.request.Request(
                test_url, headers={"User-Agent": "Mozilla/5.0",
                                    "Range": "bytes=0-4194303"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = resp.read()
            elapsed = time.time() - t0
            speed = len(data) / elapsed
            print(f"  下载 {_fmt_size(len(data))} 用时 {elapsed:.2f}s")
            print(f"  平均速度: {_fmt_speed(speed)}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"  此地址测速失败（{e.__class__.__name__}），换下一个…")
            continue
    print("所有测速源均不可用，请手动指定一个 URL 测速。")


def dl_menu():
    while True:
        print("""
---------- 下载助手（Python 标准库，免会员）----------
 1. 下载单个文件（多线程切片，支持 Range）
 2. 批量下载（URL 列表文件）
 3. 后台下载（BITS 服务，断点续传）
 4. 查看后台任务状态
 5. 网速测试
 0. 返回主菜单
""")
        c = input("选择: ").strip()
        if c == "1":
            u = input("URL: ").strip()
            d = input("保存路径（回车默认下载文件夹）: ").strip().strip('"') or None
            t = input("线程数（默认 8）: ").strip()
            threads = int(t) if t else 8
            dl_get(u, d, threads)
            pause()
        elif c == "2":
            f = input("URL 列表文件路径: ").strip().strip('"')
            d = input("保存目录（回车默认下载文件夹）: ").strip().strip('"') or None
            dl_batch(f, d)
            pause()
        elif c == "3":
            u = input("URL: ").strip()
            d = input("保存路径（回车默认下载文件夹）: ").strip().strip('"') or None
            dl_bg(u, d)
            pause()
        elif c == "4":
            dl_pending()
            pause()
        elif c == "5":
            u = input("测速 URL（回车用默认）: ").strip() or None
            dl_speedtest(u)
            pause()
        elif c in ("0", "q"):
            return


# ---------------------------------------------------------------- 投屏助手

# scrcpy 和 adb 的路径
_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
_SCRCPY_DIR = os.path.join(_TOOLS, "scrcpy", "scrcpy-win64-v4.1")
_SCRCPY_EXE = os.path.join(_SCRCPY_DIR, "scrcpy.exe")
_SCRCPY_SERVER = os.path.join(_TOOLS, "scrcpy-server-release.apk")
_ADB_EXE = os.path.join(_TOOLS, "adb.exe")


def _find_adb():
    """找到可用的 adb.exe（优先 tools/ 下的，其次 PATH 里的）。"""
    if os.path.isfile(_ADB_EXE):
        return _ADB_EXE
    # 尝试 PATH 里的
    r = _run_decode(["where", "adb"])
    if r.strip():
        return "adb"
    return None


def _find_scrcpy():
    """找到可用的 scrcpy.exe。"""
    if os.path.isfile(_SCRCPY_EXE):
        return _SCRCPY_EXE
    r = _run_decode(["where", "scrcpy"])
    if r.strip():
        return "scrcpy"
    return None


def mirror_devices():
    """列出已连接的 Android 设备。"""
    adb = _find_adb()
    if not adb:
        print("未找到 adb.exe，请确认 tools/adb.exe 存在")
        return []
    out = _run_decode([adb, "devices"])
    print(out)
    lines = [l.strip() for l in out.splitlines()
             if l.strip() and "List of devices" not in l]
    devices = [l.split("\t")[0] for l in lines if "\tdevice" in l]
    return devices


def mirror_scrcpy(wireless=False, device=None):
    """用 scrcpy 投屏 Android 手机。

    wired=True: USB 有线投屏（需开 USB 调试）
    wireless=True: WiFi 无线投屏（需先用 USB 连一次配对）
    """
    scrcpy = _find_scrcpy()
    adb = _find_adb()
    if not scrcpy:
        print("未找到 scrcpy.exe，请确认 tools/scrcpy/ 已下载")
        return
    if not adb:
        print("未找到 adb.exe")
        return

    if not os.path.isfile(_SCRCPY_SERVER):
        print(f"警告: scrcpy-server 不在 {_TOOLS}，scrcpy 可能无法启动")

    if wireless:
        # WiFi 投屏：需要先知道手机 IP:端口
        ip = input("输入手机 WiFi 投屏地址（如 192.168.1.100:5555）: ").strip()
        if not ip:
            print("未输入地址，取消")
            return
        print(f"正在连接 {ip} …")
        r = _run_decode([adb, "connect", ip])
        print(r)
        device = ip

    devices = mirror_devices()
    if not devices:
        print("\n没有检测到设备。请确认：")
        print("  1. 手机已用 USB 数据线连接电脑（有线）")
        print("  2. 手机已开启「USB 调试」（设置→开发者选项）")
        print("  3. 手机上弹出的「允许 USB 调试」已点确定")
        print("  4. WiFi 投屏需先 USB 连一次再 tcpip 切换")
        return

    print(f"\n检测到 {len(devices)} 个设备，启动 scrcpy…")
    cmd = [scrcpy]
    if device:
        cmd += ["--serial", device]
    cmd += ["--stay-awake", "--turn-screen-off"]
    print("命令:", " ".join(cmd))
    print("（按 Ctrl+C 或关闭窗口退出投屏）")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n已退出投屏")


def mirror_wifi_setup():
    """WiFi 投屏前期设置：用 USB 连接后切换到 TCP/IP 模式。"""
    adb = _find_adb()
    if not adb:
        print("未找到 adb.exe")
        return
    devices = mirror_devices()
    if not devices:
        print("请先用 USB 连接手机并开启 USB 调试")
        return
    print("\n正在切换到 WiFi 模式…")
    r = _run_decode([adb, "tcpip", "5555"])
    print(r)
    print("\n现在可以拔掉 USB 线了")
    print("在手机上查看 WiFi IP（设置→关于手机→状态→IP 地址）")
    ip = input("\n输入手机 IP 地址（如 192.168.1.100）: ").strip()
    if ip:
        addr = f"{ip}:5555"
        print(f"连接 {addr} …")
        r = _run_decode([adb, "connect", addr])
        print(r)
        if "connected" in r.lower():
            print("连接成功！现在可以用「WiFi 投屏」了")
        else:
            print("连接失败，请确认手机和电脑在同一 WiFi")


def mirror_screenshot(device=None):
    """截取手机屏幕截图。"""
    adb = _find_adb()
    if not adb:
        print("未找到 adb.exe")
        return
    devices = mirror_devices()
    if not devices:
        print("没有检测到设备")
        return
    dest = os.path.join(os.path.expanduser("~"), "Desktop",
                        f"phone_screenshot_{int(time.time())}.png")
    cmd = [adb]
    if device:
        cmd += ["-s", device]
    # 直接拉取截图到电脑
    cmd += ["exec-out", "screencap", "-p"]
    print("正在截取屏幕…")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=10)
        if r.returncode == 0 and r.stdout:
            with open(dest, "wb") as f:
                f.write(r.stdout)
            print(f"截图已保存: {dest}")
        else:
            print("截图失败:", r.stderr.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        print("截图失败:", e)


def mirror_miracast():
    """启动 Windows Miracast 无线显示（免 USB 调试）。"""
    # Windows 10/11 的 Connect 应用
    connect_exe = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                "System32", "Connect.exe")
    if os.path.isfile(connect_exe):
        print("正在启动 Windows 无线显示（Miracast）…")
        print("在手机上打开「无线投屏」/「Smart View」/「投射」即可")
        subprocess.Popen([connect_exe])
    else:
        # Windows 11：打开「投影到此电脑」设置页（接收手机投屏的配置入口）
        print("正在打开「投影到此电脑」设置…")
        print("手机上打开「无线投屏/Smart View」搜索电脑即可；")
        print("若搜不到，在该设置页把「某些 Windows 和 Android 设备可以投影到这台电脑」"
              "改为「所有位置都可用」。")
        subprocess.Popen(["explorer.exe", "ms-settings:project"])


def mirror_auto():
    """自动扫描设备，检测到就自动启动 scrcpy 投屏。"""
    scrcpy = _find_scrcpy()
    adb = _find_adb()
    if not scrcpy or not adb:
        print("缺少 scrcpy 或 adb，无法自动连接")
        return
    print("自动扫描模式已启动（每 2 秒检测一次，Ctrl+C 退出）")
    print("插上手机（需已开 USB 调试）会自动弹出投屏窗口…")
    seen = set()
    try:
        while True:
            out = _run_decode([adb, "devices"])
            lines = [l.strip() for l in out.splitlines()
                     if l.strip() and "List of devices" not in l]
            online = [l.split("\t")[0] for l in lines if "\tdevice" in l]
            new_devs = [d for d in online if d not in seen]
            for dev in new_devs:
                print(f"\n✓ 检测到新设备: {dev}，自动启动投屏…")
                seen.add(dev)
                cmd = [scrcpy, "--serial", dev, "--stay-awake",
                       "--turn-screen-off"]
                subprocess.Popen(cmd)
            # 设备断开后清除记录，重新插入能再次触发
            gone = [d for d in seen if d not in online]
            for g in gone:
                print(f"  设备已断开: {g}")
                seen.discard(g)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n已退出自动扫描")


def mirror_menu():
    while True:
        scrcpy_ok = _find_scrcpy() is not None
        adb_ok = _find_adb() is not None
        scrcpy_status = "✓" if scrcpy_ok else "✗"
        adb_status = "✓" if adb_ok else "✗"
        print(f"""
---------- 投屏助手（scrcpy {scrcpy_status} / adb {adb_status}，免会员）----------
 1. WiFi 无线投屏【Miracast】（免 USB 调试，Android 手机直接投）
 2. USB 有线投屏【scrcpy】（需开 USB 调试，画面清晰流畅）
 3. WiFi 投屏【scrcpy】（需先开 USB 调试配对一次）
 4. WiFi 投屏配对设置（USB 连接后切换到 TCP/IP 模式）
 5. 查看已连接设备
 6. 截取手机屏幕截图
 7. 自动扫描连接（插上手机自动弹投屏窗口）
 0. 返回主菜单
""")
        c = input("选择: ").strip()
        if c == "1":
            mirror_miracast()
            pause()
        elif c == "2":
            mirror_scrcpy(wireless=False)
            pause()
        elif c == "3":
            mirror_scrcpy(wireless=True)
            pause()
        elif c == "4":
            mirror_wifi_setup()
            pause()
        elif c == "5":
            mirror_devices()
            pause()
        elif c == "6":
            mirror_screenshot()
            pause()
        elif c == "7":
            mirror_auto()
        elif c in ("0", "q"):
            return


# ---------------------------------------------------------------- 系统U盘助手

_RUFUS_EXE = os.path.join(_TOOLS, "rufus.exe")
_VENTOY_DIR = os.path.join(_TOOLS, "ventoy")
_VENTOY_DISK = os.path.join(_VENTOY_DIR, "Ventoy2Disk.exe")


def wtg_list_usb():
    """列出所有 USB 磁盘（U盘/移动硬盘）。"""
    out = _ps(
        "Get-Disk | Where-Object BusType -eq 'USB' | "
        "Select-Object Number,FriendlyName,@{N='SizeGB';E="
        "{[math]::Round($_.Size/1GB,1)}},"
        "@{N='Partitions';E={($_ | Get-Partition | Measure-Object).Count}} | "
        "Format-Table -AutoSize")
    if not out.strip():
        print("没有检测到 USB 磁盘，请插入U盘或移动硬盘")
        return
    print(out)


def wtg_fetch_ventoy():
    """自动下载并解压 Ventoy（断点续传 + 失败自动重试）。"""
    import shutil
    import zipfile
    url = ("https://github.com/ventoy/Ventoy/releases/download/"
           "v1.1.17/ventoy-1.1.17-windows.zip")
    zpath = os.path.join(_TOOLS, "ventoy.zip")
    ok = False
    for i in range(1, 31):
        print(f"下载 Ventoy（第{i}次尝试，断点续传）…", flush=True)
        r = subprocess.run(
            ["curl.exe", "-L", "--ssl-no-revoke", "-C", "-",
             "--connect-timeout", "20", "--speed-time", "30",
             "--speed-limit", "1024", "-o", zpath, url],
            capture_output=True)
        if r.returncode == 0:
            ok = True
            break
        print(f"  网络中断（exit={r.returncode}），10 秒后自动续传…",
              flush=True)
        time.sleep(10)
    if not ok:
        print("下载未完成，稍后再运行本项会自动续传")
        return
    print("下载完成，解压中…")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(_TOOLS)
    os.remove(zpath)
    top = os.path.join(_TOOLS, "ventoy-1.1.17")
    if os.path.isdir(top):
        if os.path.isdir(_VENTOY_DIR):
            shutil.rmtree(_VENTOY_DIR)
        os.rename(top, _VENTOY_DIR)
    print("Ventoy 就绪:", "OK" if os.path.isfile(_VENTOY_DISK) else "失败")


def wtg_ventoy(drive=None):
    """把 U 盘做成 Ventoy 启动盘（之后任意 ISO 拖进去即可启动安装）。"""
    if not os.path.isfile(_VENTOY_DISK):
        print("未找到 Ventoy2Disk.exe。")
        ans = input("是否自动下载 Ventoy（约 16MB，断点续传）？(y/N): ").strip().lower()
        if ans in ("y", "yes", "是"):
            wtg_fetch_ventoy()
        if not os.path.isfile(_VENTOY_DISK):
            return
    wtg_list_usb()
    if not drive:
        drive = input("输入要制作成 Ventoy 的盘符（如 E:）: ").strip()
    if not drive:
        print("未输入盘符，取消")
        return
    print(f"\n!! 警告：{drive} 上所有数据将被清空！")
    ans = input("确认继续？(y/N): ").strip().lower()
    if ans not in ("y", "yes", "是"):
        print("已取消")
        return
    print("正在安装 Ventoy 到", drive, "…")
    r = _run_decode([_VENTOY_DISK, "/I", f"/Drive:{drive}"])
    print(r if r.strip() else "（CLI 无输出）")
    print("\n完成说明：如果 CLI 没生效，程序已打开 Ventoy 图形界面，")
    print("在界面里确认设备后点「安装」即可。")
    if not r.strip():
        subprocess.Popen([_VENTOY_DISK])
    print("\n制作完成后，把任何系统 ISO（Win7/10/11、Linux、PE…）")
    print("直接复制到 U 盘里，开机从 U 盘启动就能选 ISO 安装。")


def wtg_rufus(iso=None):
    """启动 Rufus（Windows To Go / Linux 启动盘制作）。"""
    if not os.path.isfile(_RUFUS_EXE):
        print("未找到 rufus.exe，请确认 tools/rufus.exe 存在")
        return
    print("正在启动 Rufus…")
    print("提示：制作 Windows To Go 时，在「镜像选项」里选")
    print("「Windows To Go」，插上目标 U 盘点开始即可（免费无会员）。")
    cmd = [_RUFUS_EXE]
    if iso and os.path.isfile(iso):
        cmd.append(iso)
    subprocess.Popen(cmd)


def wtg_menu():
    while True:
        ventoy_ok = "✓" if os.path.isfile(_VENTOY_DISK) else "✗"
        rufus_ok = "✓" if os.path.isfile(_RUFUS_EXE) else "✗"
        print(f"""
---------- 系统U盘助手（Ventoy {ventoy_ok} / Rufus {rufus_ok}，全免费开源）----------
 1. 列出 U 盘 / 移动硬盘
 2. 制作 Ventoy 启动盘（之后任意 ISO 拖进 U 盘就能装任何系统）
 3. 启动 Rufus（Windows To Go：把 Win10/11 完整装进 U 盘随身带）
 4. 使用说明
 0. 返回主菜单
""")
        c = input("选择: ").strip()
        if c == "1":
            wtg_list_usb()
            pause()
        elif c == "2":
            wtg_ventoy()
        elif c == "3":
            p = input("ISO 路径（回车跳过，之后在 Rufus 里选）: ").strip().strip('"')
            wtg_rufus(p or None)
            pause()
        elif c == "4":
            print("""
【Ventoy —— 一个U盘装百系统（推荐）】
 1. 选 2 把U盘做成 Ventoy 盘（只需做一次，会清空U盘）
 2. 之后把任何系统 ISO 直接复制到U盘：Win7/Win10/Win11、
    Ubuntu/Deepin/统信UOS、各种 PE、甚至 Android-x86
 3. 开机按 F12/F9/Esc 选 U 盘启动 → Ventoy 列表里选 ISO 回车
 4. 想装新系统？把新 ISO 拷进去就行，永远不用重新做盘

【Rufus —— Windows To Go（把 Windows 装进U盘当随身系统）】
 1. 选 3 启动 Rufus
 2. 设备选你的 U 盘，引导类型选 Windows ISO
 3. 镜像选项选「Windows To Go」→ 开始（建议 U 盘 ≥32GB、USB3.0）
 4. 做好的 U 盘插任何电脑都能启动出你自己的 Windows

注意：两者都会清空 U 盘数据，重要文件先备份！""")
            pause()
        elif c in ("0", "q"):
            return


def launch_dualbrain():
    """以独立进程启动双脑一体 GUI（pythonw 无黑窗，不阻塞本终端）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "双脑一体.py")
    if not os.path.isfile(path):
        print(f"未找到 {path}")
        return
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    try:
        subprocess.Popen(
            [pythonw, path], cwd=os.path.dirname(path),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        print("✅ 双脑一体已启动（独立窗口，可关闭本菜单不影响运行）。")
        print("   📱 手机控制：在双脑一体窗口点顶部「🌐 手机控制」获取地址。")
    except Exception as e:
        print(f"启动失败：{e}")


def launch_yingshiku():
    """以独立进程启动影视库播放器。"""
    path = r"C:\影视播放器\影视库.py"
    if not os.path.isfile(path):
        print(f"未找到 {path}")
        return
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    try:
        subprocess.Popen(
            [pythonw, path], cwd=os.path.dirname(path),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        print("✅ 影视库已启动（独立窗口）。")
    except Exception as e:
        print(f"启动失败：{e}")


# ---------------------------------------------------------------- WiFi 守护

def wifi_menu():
    """WiFi 守护交互菜单：断网自动重连 / 没网自动换连过的WiFi / 开机自启，带总开关。"""
    import wifi_keeper
    while True:
        print("\n" + wifi_keeper.status_text())
        print(""" ---------- WiFi 守护操作 ----------
 1. 开启守护（断网自动重连 + 没网自动换WiFi + 开机自启）
 2. 关闭守护（停止后台进程 + 取消开机自启，之后不再自动连WiFi）
 3. 立即检测并修复一次
 4. 查看运行日志
 5. 扫描周围WiFi（看有哪些可以加进自动切换池）
 6. 添加WiFi（输名称+密码，直接存进自动切换池，不用手动连）
 7. 删除WiFi（废网删掉，再也不自动切它）
 0. 返回主菜单""")
        c = input("选择: ").strip()
        if c == "1":
            print("\n" + wifi_keeper.turn_on())
            pause()
        elif c == "2":
            print("\n" + wifi_keeper.turn_off())
            pause()
        elif c == "3":
            print("\n正在检测网络并尝试修复…")
            print(wifi_keeper.run_once_text())
            pause()
        elif c == "4":
            print("\n" + wifi_keeper.tail_log())
            pause()
        elif c == "5":
            print("\n" + wifi_keeper.scan_text())
            pause()
        elif c == "6":
            ssid = input("WiFi 名称（SSID，可照抄扫描结果）：").strip()
            if not ssid:
                print("名称不能为空。")
                pause()
                continue
            open_net = input("是不是没有密码的开放网络？(y/N)：").strip().lower() in ("y", "yes", "是")
            pwd = ""
            if not open_net:
                import getpass
                pwd = getpass.getpass("WiFi 密码（输入不显示，输完回车）：").strip()
            print("\n正在添加并验证…")
            print(wifi_keeper.add_profile_text(ssid, pwd, open_net=open_net))
            pause()
        elif c == "7":
            profiles = wifi_keeper.list_profiles()
            print("\n当前已保存的WiFi：" + ("、".join(profiles) if profiles else "（无）"))
            ssid = input("要删除哪个WiFi（输名称）：").strip()
            if ssid and confirm(f"确认删除「{ssid}」？删除后该WiFi密码也会从本机清除"):
                ok, msg = wifi_keeper.forget_profile(ssid)
                print(("✅ " if ok else "❌ ") + msg)
            pause()
        elif c in ("0", "q", "exit"):
            return


def wifi_cli(rest):
    import wifi_keeper
    act = rest[0].lower() if rest else "status"
    if act in ("on", "开启", "start", "enable"):
        print(wifi_keeper.turn_on())
    elif act in ("off", "关闭", "stop", "disable"):
        print(wifi_keeper.turn_off())
    elif act in ("now", "once", "检测", "fix"):
        print("正在检测网络并尝试修复…")
        print(wifi_keeper.run_once_text())
    elif act in ("log", "日志", "logs"):
        print(wifi_keeper.tail_log())
    elif act in ("scan", "扫描", "list-ap"):
        print(wifi_keeper.scan_text())
    elif act in ("add", "添加"):
        if len(rest) < 2:
            print("用法：python 卸载助手命令行版.py wifi add <WiFi名称> [密码] [--open]")
            print("  密码省略时会提示输入；--open 表示无密码的开放WiFi")
            return
        ssid = rest[1]
        open_net = "--open" in rest[2:]
        pwd = ""
        if not open_net:
            args = [a for a in rest[2:] if a != "--open"]
            if args:
                pwd = args[0]
            else:
                import getpass
                pwd = getpass.getpass("WiFi 密码（输入不显示）：").strip()
        print("正在添加并验证…")
        print(wifi_keeper.add_profile_text(ssid, pwd, open_net=open_net))
    elif act in ("del", "delete", "forget", "rm", "删除"):
        if len(rest) < 2:
            print("用法：python 卸载助手命令行版.py wifi del <WiFi名称>")
            return
        ok, msg = wifi_keeper.forget_profile(rest[1])
        print(("✅ " if ok else "❌ ") + msg)
    elif act in ("status", "状态", ""):
        print(wifi_keeper.status_text())
    else:
        print("用法：python 卸载助手命令行版.py wifi [on|off|status|now|log|scan|add|del]")


# ---------------------------------------------------------------- 主入口

MENU = """
============ 卸载助手 集成命令终端 ==========
 1. 已安装软件（卸载/强制卸载/清残留）
 2. 启动项管理
 3. 进程管理（结束/挂起/恢复/优先级）
 4. 服务管理（启停/启动类型）
 5. 智能省电（托管看门狗）
 6. 路径清理（删残留文件/目录）
 7. 驱动助手（检测/备份/安装/清理，免会员）
 8. 下载助手（多线程/后台断点续传，替代 IDM）
 9. 投屏助手（Miracast 免调试 / scrcpy 有线+无线）
 10. 系统U盘助手（Ventoy 任意ISO / Rufus WinToGo，替代傲梅）
 11. 双脑一体（AI 智能体：小龙虾+爱马仕+直连大脑，支持手机控制）
 12. 影视库（本地电影电视剧播放器：海报墙/续播，无广告免费）
 13. WiFi守护（断网自动重连 / 没网自动换连过的WiFi / 开机自启，带开关）
 0. 退出
============================================"""


def interactive():
    while True:
        print(MENU)
        c = input("选择: ").strip()
        if c == "1":
            apps_menu()
        elif c == "2":
            startup_menu()
        elif c == "3":
            procs_menu()
        elif c == "4":
            svcs_menu()
        elif c == "5":
            smart_menu()
        elif c == "6":
            clean_path(input("要删除的路径："))
            pause()
        elif c == "7":
            drv_menu()
        elif c == "8":
            dl_menu()
        elif c == "9":
            mirror_menu()
        elif c == "10":
            wtg_menu()
        elif c == "11":
            launch_dualbrain()
            pause()
        elif c == "12":
            launch_yingshiku()
            pause()
        elif c == "13":
            wifi_menu()
        elif c in ("0", "q", "exit"):
            return


def main():
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argv = sys.argv[1:]
    if argv and argv[0] == "--wifi-daemon":
        # 后台守护入口（开机自启/开关拉起用）：不提权、不弹窗，静默运行
        import wifi_keeper
        wifi_keeper.daemon_main()
        return
    if argv and argv[0] == "clean":
        clean_path(argv[1] if len(argv) > 1 else input("要删除的路径："))
        return
    if not is_admin() and not os.environ.get("UNINSTALL_HELPER_NO_ELEVATE"):
        relaunch_as_admin()
    if not argv:
        interactive()
        return
    cmd = argv[0].lower()
    rest = argv[1:]
    if cmd == "apps":
        print_apps(core.scan_installed_apps())
    elif cmd == "startup":
        print_startup()
    elif cmd == "procs" and rest:
        one_shot_proc(rest)
    elif cmd == "procs":
        print_procs()
    elif cmd == "svcs" and rest and rest[0].lower() in ("start", "stop", "set"):
        one_shot_svc(rest)
    elif cmd == "svcs":
        print_services(rest[0] if rest else "")
    elif cmd == "smart" and rest and rest[0] == "show":
        smart_show()
    elif cmd == "smart" and rest and rest[0] == "watch":
        run_watchdog(core.load_manager_config())
    elif cmd == "drv" and rest and rest[0] == "hw":
        drv_hw()
    elif cmd == "drv" and rest and rest[0] == "problems":
        drv_problems()
    elif cmd == "drv" and rest and rest[0] == "auto":
        drv_auto()
    elif cmd == "drv" and rest and rest[0] == "match":
        drv_match(dry="--dry" in rest[1:])
    elif cmd == "drv" and rest and rest[0] == "list":
        drv_list()
    elif cmd == "drv" and len(rest) >= 2 and rest[0] == "backup":
        drv_backup(rest[1])
    elif cmd == "drv" and len(rest) >= 2 and rest[0] == "install":
        drv_install(rest[1])
    elif cmd == "drv" and len(rest) >= 2 and rest[0] == "del":
        drv_del(rest[1], "--force" in rest[2:])
    elif cmd == "drv":
        drv_menu()
    elif cmd == "dl" and rest and rest[0] == "get":
        url = rest[1] if len(rest) > 1 else input("URL: ").strip()
        dest = rest[2] if len(rest) > 2 else None
        dl_get(url, dest)
    elif cmd == "dl" and rest and rest[0] == "batch":
        f = rest[1] if len(rest) > 1 else input("URL 列表文件: ").strip()
        d = rest[2] if len(rest) > 2 else None
        dl_batch(f, d)
    elif cmd == "dl" and rest and rest[0] == "bg":
        url = rest[1] if len(rest) > 1 else input("URL: ").strip()
        dest = rest[2] if len(rest) > 2 else None
        dl_bg(url, dest)
    elif cmd == "dl" and rest and rest[0] == "pending":
        dl_pending()
    elif cmd == "dl" and rest and rest[0] in ("speed", "speedtest"):
        url = rest[1] if len(rest) > 1 else None
        dl_speedtest(url)
    elif cmd == "dl":
        dl_menu()
    elif cmd == "mirror" and rest and rest[0] == "miracast":
        mirror_miracast()
    elif cmd == "mirror" and rest and rest[0] == "usb":
        mirror_scrcpy(wireless=False)
    elif cmd == "mirror" and rest and rest[0] == "wifi":
        mirror_scrcpy(wireless=True)
    elif cmd == "mirror" and rest and rest[0] == "setup":
        mirror_wifi_setup()
    elif cmd == "mirror" and rest and rest[0] == "devices":
        mirror_devices()
    elif cmd == "mirror" and rest and rest[0] == "shot":
        mirror_screenshot()
    elif cmd == "mirror" and rest and rest[0] == "auto":
        mirror_auto()
    elif cmd == "mirror":
        mirror_menu()
    elif cmd == "wtg" and rest and rest[0] == "list":
        wtg_list_usb()
    elif cmd == "wtg" and rest and rest[0] == "ventoy":
        wtg_ventoy(rest[1] if len(rest) > 1 else None)
    elif cmd == "wtg" and rest and rest[0] == "rufus":
        wtg_rufus(rest[1] if len(rest) > 1 else None)
    elif cmd == "wtg":
        wtg_menu()
    elif cmd in ("brain", "brain2", "双脑"):
        launch_dualbrain()
    elif cmd in ("tv", "film", "影视"):
        launch_yingshiku()
    elif cmd == "wifi":
        wifi_cli(rest)
    else:
        print(__doc__)


def one_shot_proc(rest):
    action, pid = rest[0].lower(), int(rest[1])
    if action == "kill":
        ok, err = core.kill_process(pid)
        print("结果:", "成功" if ok else f"失败：{err}")
    elif action == "suspend":
        print("挂起:", core.suspend_process(pid))
    elif action == "resume":
        print("恢复:", core.resume_process(pid))
    elif action == "prio" and len(rest) >= 3:
        cls = {"below": core.BELOW_NORMAL_PRIORITY_CLASS, "idle": core.IDLE_PRIORITY_CLASS,
               "normal": core.NORMAL_PRIORITY_CLASS}.get(rest[2].lower())
        print("优先级:", "成功" if cls and core.set_process_priority(pid, cls) else "失败")


def one_shot_svc(rest):
    action, name = rest[0].lower(), rest[1]
    if action == "start":
        ok, err = core.service_start(name)
        print("结果:", "成功" if ok else f"失败：{err}")
    elif action == "stop":
        ok, err = core.service_stop(name)
        print("结果:", "成功" if ok else f"失败：{err}")
    elif action == "set" and len(rest) >= 3:
        kind = normalize_kind(rest[2])
        if not kind:
            print("启动类型应为：auto / auto_delayed / manual / disabled")
            return
        ok, err = core.service_set_start(name, kind)
        print("结果:", "成功" if ok else f"失败：{err}")


def print_startup():
    items = core.scan_startup_items()
    print(f"共 {len(items)} 个启动项")
    for i, it in enumerate(items, 1):
        state = "启用" if it.enabled else "禁用"
        print(f"{i:>3}. [{state}] {it.name[:36]:<38} {it.location}\n     {it.command[:90]}")


if __name__ == "__main__":
    main()