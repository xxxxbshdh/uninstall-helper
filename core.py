"""卸载助手核心逻辑。

仅使用公开文档化的 Windows 注册表键与文件系统接口：
- 已安装软件列表来自 Uninstall 注册表键（HKLM/HKCU，含 32 位视图）
- 启动项来自 Run 注册表键与"启动"文件夹，启用状态遵循 StartupApproved 规范
- 卸载即运行软件自带的 UninstallString，随后检测并清理注册表键/目录残留
"""
from __future__ import annotations

import ctypes
import json
import os
import shlex
import shutil
import subprocess
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass

# 已安装软件的注册表位置（64 位视图 + 32 位视图 + 当前用户）
UNINSTALL_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
UNINSTALL_ROOTS = (
    (winreg.HKEY_LOCAL_MACHINE, UNINSTALL_ROOT, winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_LOCAL_MACHINE, UNINSTALL_ROOT, winreg.KEY_WOW64_32KEY),
    (winreg.HKEY_CURRENT_USER, UNINSTALL_ROOT, winreg.KEY_WOW64_64KEY),
)

# 启动项注册表位置
RUN_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_ROOTS = (
    (winreg.HKEY_CURRENT_USER, RUN_ROOT, "HKCU\\Run", winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_LOCAL_MACHINE, RUN_ROOT, "HKLM\\Run", winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_LOCAL_MACHINE, RUN_ROOT, "HKLM\\Run(32位)", winreg.KEY_WOW64_32KEY),
)

# 启动文件夹
STARTUP_FOLDERS = (
    ("当前用户启动文件夹", os.path.join(os.environ.get("APPDATA", ""),
     r"Microsoft\Windows\Start Menu\Programs\Startup")),
    ("所有用户启动文件夹", os.path.join(os.environ.get("PROGRAMDATA", ""),
     r"Microsoft\Windows\Start Menu\Programs\Startup")),
)

# StartupApproved：Windows 8+ 记录启动项启用/禁用状态的标准位置
APPROVED_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

# 残留扫描的常见根目录（环境变量名）
LEFTOVER_ROOT_VARS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA")


@dataclass
class InstalledApp:
    """一个已安装软件的条目。"""
    hive: int                      # 注册表 hive（HKEY_* 常量）
    reg_path: str                  # 卸载键在 hive 下的完整路径
    name: str
    version: str = ""
    publisher: str = ""
    install_location: str = ""
    uninstall_string: str = ""
    estimated_size_kb: int = 0
    install_date: str = ""
    icon_path: str = ""


@dataclass
class StartupItem:
    """一个开机启动项。"""
    name: str
    command: str
    location: str                  # 显示用位置描述
    kind: str                      # "reg" 或 "file"
    enabled: bool = True
    reg_hive: int = 0              # kind=reg 时有效
    reg_path: str = ""             # Run 键完整路径
    value_name: str = ""           # Run 键里的值名
    file_path: str = ""            # kind=file 时有效
    approved_subkey: str = ""      # StartupApproved 下的子键名（Run / StartupFolder）
    approved_name: str = ""        # StartupApproved 里的值名


def _get_value(key, name: str):
    """读取注册表值，失败返回 None。"""
    try:
        val, _typ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if isinstance(val, bytes):
        return val.decode("utf-16-le", errors="ignore").split("\x00")[0]
    return val


def _clean_str(val) -> str:
    return str(val).strip().strip('"') if val is not None else ""


def split_icon(value: str) -> str:
    """DisplayIcon 形如 "C:\\xx\\a.exe,0"，取出图标文件路径。"""
    value = _clean_str(value)
    if not value:
        return ""
    return value.split(",")[0].strip()


def format_size(size_kb: int) -> str:
    if size_kb <= 0:
        return ""
    if size_kb < 1024:
        return f"{size_kb} KB"
    if size_kb < 1024 * 1024:
        return f"{size_kb / 1024:.1f} MB"
    return f"{size_kb / 1024 / 1024:.2f} GB"


def format_install_date(raw: str) -> str:
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


# ---------------------------------------------------------------- 已安装软件

def scan_installed_apps() -> list[InstalledApp]:
    """扫描所有已安装软件，按名称排序。"""
    apps: list[InstalledApp] = []
    seen: set[tuple[int, str]] = set()  # HKCU 在 64/32 两个视图下内容相同，去重
    for hive, root, access in UNINSTALL_ROOTS:
        try:
            root_key = winreg.OpenKey(hive, root, 0, winreg.KEY_READ | access)
        except OSError:
            continue
        with root_key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root_key, index)
                except OSError:
                    break
                index += 1
                ident = (hive, f"{root}\\{subkey_name}")
                if ident in seen:
                    continue
                seen.add(ident)
                try:
                    with winreg.OpenKey(root_key, subkey_name, 0, winreg.KEY_READ | access) as sk:
                        name = _clean_str(_get_value(sk, "DisplayName"))
                        if not name:
                            continue
                        if (_get_value(sk, "SystemComponent") or 0) == 1:
                            continue  # 隐藏的系统组件
                        apps.append(InstalledApp(
                            hive=hive,
                            reg_path=f"{root}\\{subkey_name}",
                            name=name,
                            version=_clean_str(_get_value(sk, "DisplayVersion")),
                            publisher=_clean_str(_get_value(sk, "Publisher")),
                            install_location=_clean_str(_get_value(sk, "InstallLocation")),
                            uninstall_string=_clean_str(_get_value(sk, "UninstallString")),
                            estimated_size_kb=int(_get_value(sk, "EstimatedSize") or 0),
                            install_date=str(_get_value(sk, "InstallDate") or ""),
                            icon_path=split_icon(_get_value(sk, "DisplayIcon") or ""),
                        ))
                except OSError:
                    continue
    apps.sort(key=lambda a: a.name.lower())
    return apps


def split_command(cmd: str) -> list[str]:
    """把命令行拆成 [程序, 参数...]，保留 Windows 风格引号处理。"""
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        return [cmd]
    result = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in ('"', "'"):
            part = part[1:-1]
        result.append(part)
    return result


def launch_uninstall_and_wait(app: InstalledApp, key_timeout: int = 60) -> bool:
    """启动软件自带的卸载程序并等待其完成。

    等待策略：
    1. 等卸载程序主进程退出（proc.wait）
    2. 主进程退出后，可能有子进程仍在卸载（NSIS/Inno 等），
       轮询注册表卸载键是否消失，最多等 key_timeout 秒
    3. 超时也返回 True，由调用方根据 uninstall_key_exists 判断是否真卸干净
    """
    if not app.uninstall_string:
        return False
    try:
        proc = subprocess.Popen(split_command(app.uninstall_string))
    except OSError:
        try:  # 命令行格式怪异时退回 shell 方式
            proc = subprocess.Popen(app.uninstall_string, shell=True)
        except OSError:
            return False
    try:
        proc.wait()
    except Exception:
        return False
    # 主进程已退出，轮询卸载键消失（子进程可能在卸载中）
    deadline = time.time() + key_timeout
    while time.time() < deadline:
        if not uninstall_key_exists(app):
            break
        time.sleep(1)
    return True


# ---------------------------------------------------------------- 进程占用预检

# Win32 API 句柄
_psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
_PROCESS_QUERY_LIMITED_INFO = 0x1000


def _enum_pids() -> list[int]:
    """EnumProcesses：返回当前所有进程 PID 列表。"""
    capacity = 1024
    while True:
        pids = (wintypes.DWORD * capacity)()
        bytes_returned = wintypes.DWORD()
        if not _psapi.EnumProcesses(pids, ctypes.sizeof(pids),
                                    ctypes.byref(bytes_returned)):
            return []
        if bytes_returned.value < ctypes.sizeof(pids):
            count = bytes_returned.value // ctypes.sizeof(wintypes.DWORD)
            return [pids[i] for i in range(count) if pids[i]]
        capacity *= 2


def list_running_procs_in_dir(install_dir: str) -> list[str]:
    """返回 InstallLocation 目录下正在运行的进程 exe 完整路径列表。

    用 EnumProcesses + QueryFullProcessImageNameW，路径前缀匹配 InstallLocation。
    权限不足的系统进程会被跳过，不影响用户软件检测。
    """
    if not install_dir or not os.path.isdir(install_dir):
        return []
    target_prefix = os.path.normpath(install_dir).lower().rstrip("\\")

    running: list[str] = []
    for pid in _enum_pids():
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFO, False, pid)
        if not h:
            continue
        try:
            path = _query_process_image(h)
            if path:
                p = os.path.normpath(path).lower()
                if p == target_prefix or p.startswith(target_prefix + "\\"):
                    running.append(path)
        finally:
            _kernel32.CloseHandle(h)
    return running


def _query_process_image(handle) -> str | None:
    """QueryFullProcessImageNameW：取进程 exe 完整路径，失败返回 None。"""
    path_buf = ctypes.create_unicode_buffer(260)
    size = wintypes.DWORD(260)
    if _kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size)):
        return path_buf.value
    return None


# ---------------------------------------------------------------- 强制卸载兜底

def force_uninstall(app: InstalledApp) -> dict:
    """软件自带卸载程序损坏/丢失时的兜底：直接删注册表键 + 安装目录 + 残留候选。

    返回 {"deleted": [...], "failed": [...]} 记录操作结果。
    """
    deleted: list[str] = []
    failed: list[str] = []

    # 1. 注册表卸载键
    if uninstall_key_exists(app):
        if delete_app_registry_key(app):
            deleted.append(app.reg_path)
        else:
            failed.append(app.reg_path)

    # 2. InstallLocation 目录
    deleted_dir = False
    if app.install_location and os.path.exists(app.install_location):
        if delete_path(app.install_location):
            deleted.append(app.install_location)
            deleted_dir = True
        else:
            failed.append(app.install_location)

    # 3. 其他残留候选目录（scan_leftover_candidates 已含 InstallLocation，避免重复）
    for path in scan_leftover_candidates(
            app.name, app.publisher, app.install_location):
        if deleted_dir and os.path.normpath(path).lower() == \
                os.path.normpath(app.install_location).lower():
            continue
        if path in deleted or path in failed:
            continue
        if delete_path(path):
            deleted.append(path)
        else:
            failed.append(path)

    return {"deleted": deleted, "failed": failed}


def uninstall_key_exists(app: InstalledApp) -> bool:
    """卸载完成后，检查该软件的注册表卸载键是否仍残留。"""
    try:
        winreg.OpenKey(app.hive, app.reg_path, 0, winreg.KEY_READ)
        return True
    except OSError:
        return False


def delete_registry_tree(hive, subpath: str) -> bool:
    """递归删除注册表键及其所有子键。"""
    try:
        with winreg.OpenKey(hive, subpath, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                if not delete_registry_tree(hive, subpath + "\\" + child):
                    return False
        winreg.DeleteKey(hive, subpath)
        return True
    except FileNotFoundError:
        return True  # 已不存在视为成功
    except OSError:
        return False


def delete_app_registry_key(app: InstalledApp) -> bool:
    return delete_registry_tree(app.hive, app.reg_path)


def delete_path(path: str) -> bool:
    """删除文件或整个目录。"""
    path = path.strip().strip('"')
    if not path or not os.path.exists(path):
        return True
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True
    except OSError:
        return False


def scan_leftover_candidates(name: str, publisher: str, install_dir: str) -> list[str]:
    """按软件名/发布者在常见目录里找疑似残留文件夹。"""
    tokens = []
    for src in (name, publisher):
        s = (src or "").strip().lower()
        if len(s) >= 3:
            tokens.append(s)
    if not tokens:
        return []

    install_norm = os.path.normpath(install_dir).lower() if install_dir else ""
    result: list[str] = []
    if install_dir and os.path.isdir(install_dir):
        result.append(install_dir)

    for var in LEFTOVER_ROOT_VARS:
        base = os.environ.get(var)
        if not base or not os.path.isdir(base):
            continue
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            low = entry.lower()
            if any(token in low for token in tokens):
                path = os.path.normpath(os.path.join(base, entry))
                if install_norm and path.lower() == install_norm:
                    continue
                if path not in result:
                    result.append(path)
    return result


# ---------------------------------------------------------------- 启动项

def _approved_state(hive, subkey: str, value_name: str) -> bool | None:
    """读 StartupApproved 状态：True=启用，False=禁用，None=无记录。"""
    try:
        with winreg.OpenKey(hive, f"{APPROVED_ROOT}\\{subkey}", 0, winreg.KEY_READ) as key:
            val, _typ = winreg.QueryValueEx(key, value_name)
    except OSError:
        return None
    if isinstance(val, bytes) and val:
        # 首字节偶数(0x02/0x06)=启用，奇数(0x03)=禁用
        return (val[0] & 0x01) == 0
    return None


def _set_approved(hive, subkey: str, value_name: str, enabled: bool) -> bool:
    """写入 StartupApproved 状态（HKLM 需要管理员权限）。"""
    try:
        with winreg.CreateKeyEx(hive, f"{APPROVED_ROOT}\\{subkey}", 0,
                                winreg.KEY_SET_VALUE) as key:
            payload = (b"\x02" if enabled else b"\x03") + b"\x00" * 11
            winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, payload)
        return True
    except OSError:
        return False


def scan_startup_items() -> list[StartupItem]:
    """扫描 Run 键与启动文件夹里的所有开机启动项。"""
    items: list[StartupItem] = []

    for hive, path, label, access in RUN_ROOTS:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | access)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    value_name, data, _typ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                command = data.decode("utf-16-le", errors="ignore").split("\x00")[0] \
                    if isinstance(data, bytes) else str(data)
                state = _approved_state(hive, "Run", value_name)
                items.append(StartupItem(
                    name=value_name,
                    command=command,
                    location=label,
                    kind="reg",
                    enabled=True if state is None else state,
                    reg_hive=hive,
                    reg_path=path,
                    value_name=value_name,
                    approved_subkey="Run",
                    approved_name=value_name,
                ))

    for label, folder in STARTUP_FOLDERS:
        if not folder or not os.path.isdir(folder):
            continue
        for entry in os.listdir(folder):
            file_path = os.path.join(folder, entry)
            if not os.path.isfile(file_path):
                continue
            state = _approved_state(winreg.HKEY_CURRENT_USER, "StartupFolder", entry)
            items.append(StartupItem(
                name=os.path.splitext(entry)[0],
                command=file_path,
                location=label,
                kind="file",
                enabled=True if state is None else state,
                file_path=file_path,
                approved_subkey="StartupFolder",
                approved_name=entry,
            ))

    items.sort(key=lambda it: (not it.enabled, it.name.lower()))
    return items


def set_startup_enabled(item: StartupItem, enabled: bool) -> bool:
    """启用/禁用启动项（写 StartupApproved，系统标准做法）。"""
    if not item.approved_subkey:
        return False
    if item.kind == "reg":
        return _set_approved(item.reg_hive, item.approved_subkey, item.approved_name, enabled)
    return _set_approved(winreg.HKEY_CURRENT_USER, item.approved_subkey, item.approved_name, enabled)


def delete_startup_item(item: StartupItem) -> bool:
    """彻底删除启动项。"""
    try:
        if item.kind == "reg":
            with winreg.OpenKey(item.reg_hive, item.reg_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, item.value_name)
        else:
            os.remove(item.file_path)
    except OSError:
        return False
    # 顺带清掉 StartupApproved 记录（失败不影响）
    try:
        hive = item.reg_hive if item.kind == "reg" else winreg.HKEY_CURRENT_USER
        with winreg.OpenKey(hive, f"{APPROVED_ROOT}\\{item.approved_subkey}", 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, item.approved_name)
    except OSError:
        pass
    return True


# ---------------------------------------------------------------- 进程管理（任务管理器）

_ntdll = ctypes.WinDLL("ntdll.dll", use_last_error=True)
_user32 = ctypes.WinDLL("user32.dll", use_last_error=True)

PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_SET_INFORMATION = 0x0200
IDLE_PRIORITY_CLASS = 0x40
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
NORMAL_PRIORITY_CLASS = 0x20

# 结束进程的保护名单（结束会导致蓝屏/系统崩溃）
KILL_PROTECTED = {
    "system", "registry", "memory compression", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
}


class ProcessInfo:
    """一个运行中的进程快照。"""

    __slots__ = ("pid", "name", "exe_path", "cpu_percent", "mem_bytes")

    def __init__(self, pid: int, name: str, exe_path: str,
                 cpu_percent: float, mem_bytes: int):
        self.pid = pid
        self.name = name
        self.exe_path = exe_path
        self.cpu_percent = cpu_percent
        self.mem_bytes = mem_bytes


class _FT(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


def _ft_val(ft: _FT) -> int:
    return (ft.hi << 32) | ft.lo


class _PMC(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS。"""
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_kernel32.GetProcessTimes.argtypes = (
    wintypes.HANDLE, ctypes.POINTER(_FT), ctypes.POINTER(_FT),
    ctypes.POINTER(_FT), ctypes.POINTER(_FT))
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_psapi.GetProcessMemoryInfo.argtypes = (
    wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD)
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
_kernel32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel32.SetPriorityClass.restype = wintypes.BOOL
_ntdll.NtSuspendProcess.argtypes = (wintypes.HANDLE,)
_ntdll.NtSuspendProcess.restype = ctypes.c_int
_ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
_ntdll.NtResumeProcess.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))

_CPU_BASE: dict[int, tuple[float, int]] = {}   # pid -> (monotonic, kernel+user 100ns)
_CPU_CORES = os.cpu_count() or 1


def _proc_cpu_total(handle) -> int | None:
    """进程内核+用户态累计 CPU 时间（100ns 单位），失败返回 None。"""
    c, e, k, u = _FT(), _FT(), _FT(), _FT()
    if not _kernel32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                                     ctypes.byref(k), ctypes.byref(u)):
        return None
    return _ft_val(k) + _ft_val(u)


def _proc_mem(handle) -> int:
    """进程工作集内存（字节），失败返回 0。"""
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    if _psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
        return int(pmc.WorkingSetSize)
    return 0


def list_processes() -> list[ProcessInfo]:
    """枚举所有进程并计算 CPU 占用率（相对上次调用的间隔）。"""
    now = time.monotonic()
    result: list[ProcessInfo] = []
    seen: set[int] = set()
    for pid in _enum_pids():
        seen.add(pid)
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFO, False, pid)
        if not h:
            continue
        try:
            exe = _query_process_image(h)
            if exe is None:
                continue
            total = _proc_cpu_total(h)
            mem = _proc_mem(h)
        finally:
            _kernel32.CloseHandle(h)
        cpu = 0.0
        if total is not None:
            base = _CPU_BASE.get(pid)
            if base and now > base[0]:
                cpu = max(0.0, (total - base[1]) / ((now - base[0]) * 1e7)
                          / _CPU_CORES * 100)
            _CPU_BASE[pid] = (now, total)
        result.append(ProcessInfo(pid, os.path.basename(exe), exe, cpu, mem))
    for dead in [p for p in _CPU_BASE if p not in seen]:
        del _CPU_BASE[dead]
    result.sort(key=lambda p: (-p.cpu_percent, -p.mem_bytes, p.pid))
    return result


def get_foreground_pid() -> int:
    """当前前台窗口所属进程 PID，取不到返回 0。"""
    hwnd = _user32.GetForegroundWindow()
    pid = wintypes.DWORD(0)
    if hwnd:
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def kill_process(pid: int, name: str = "") -> tuple[bool, str]:
    """强制结束进程，返回 (是否成功, 失败原因)。"""
    if pid <= 4 or name.lower() in KILL_PROTECTED:
        return False, "系统关键进程，禁止结束"
    try:
        proc = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                              capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    err = proc.stderr.decode("gbk", errors="replace").strip() or \
        proc.stdout.decode("gbk", errors="replace").strip()
    return False, err or f"taskkill 退出码 {proc.returncode}"


def suspend_process(pid: int) -> bool:
    """挂起进程（NtSuspendProcess），暂停其所有线程以释放 CPU。"""
    h = _kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h:
        return False
    try:
        return _ntdll.NtSuspendProcess(h) == 0
    finally:
        _kernel32.CloseHandle(h)


def resume_process(pid: int) -> bool:
    """恢复挂起的进程（NtResumeProcess）。"""
    h = _kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h:
        return False
    try:
        return _ntdll.NtResumeProcess(h) == 0
    finally:
        _kernel32.CloseHandle(h)


def set_process_priority(pid: int, priority_class: int) -> bool:
    """设置进程优先级类别。"""
    h = _kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
    if not h:
        return False
    try:
        return bool(_kernel32.SetPriorityClass(h, priority_class))
    finally:
        _kernel32.CloseHandle(h)


def process_exists(name: str) -> bool:
    """是否存在指定 exe 文件名的进程（不区分大小写）。"""
    low = (name or "").strip().lower()
    if not low:
        return False
    if not low.endswith(".exe"):
        low += ".exe"
    for pid in _enum_pids():
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFO, False, pid)
        if not h:
            continue
        try:
            exe = _query_process_image(h)
            if exe and os.path.basename(exe).lower() == low:
                return True
        finally:
            _kernel32.CloseHandle(h)
    return False


def format_bytes(n: int) -> str:
    if n <= 0:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


# ---------------------------------------------------------------- 服务管理

_advapi32 = ctypes.WinDLL("advapi32.dll", use_last_error=True)

SC_MANAGER_ALL_ACCESS = 0xF003F
SC_MANAGER_LIST = 0x0001 | 0x0004          # CONNECT | ENUMERATE_SERVICE
SERVICE_QUERY_ACCESS = 0x0001 | 0x0004     # QUERY_CONFIG | QUERY_STATUS
SERVICE_MANAGE_ACCESS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
SERVICE_WIN32 = 0x00000030
SERVICE_STATE_ALL = 0x00000003
SERVICE_NO_CHANGE = 0xFFFFFFFF
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONFIG_DELAYED_AUTO_START = 3

_START_KINDS = {0: "boot", 1: "system", 2: "auto", 3: "manual", 4: "disabled"}
START_LABELS = {
    "boot": "启动(引导)", "system": "启动(系统)", "auto": "自动",
    "auto_delayed": "自动(延迟)", "manual": "手动", "disabled": "禁用",
}
STATE_LABELS = {1: "已停止", 2: "启动中", 3: "停止中", 4: "运行中",
                5: "恢复中", 6: "暂停中", 7: "已暂停"}
# 停止/禁用会直接影响系统运行的关键服务，禁止在工具里操作
PROTECTED_SERVICES = frozenset({
    "RpcSs", "RpcEptMapper", "DcomLaunch", "PlugPlay", "LSM",
    "SamSs", "Winmgmt", "Schedule", "EventLog", "Dhcp", "Dnscache",
    "ProfSvc", "gpsvc", "CryptSvc", "StateRepository", "CoreMessagingRegistrar",
    "Audiosrv", "AudioEndpointBuilder", "Themes", "nsi", "BrokerInfrastructure",
    "Appinfo", "WinDefend", "SecurityHealthService", "WdNisSvc", "Sense",
    "Power", "TimeBrokerSvc", "SystemEventsBroker",
})

# 启动类型写回映射：kind -> (dwStartType, delayed(True/False/不修改))
_START_KIND_WRITE = {
    "auto": (2, False),
    "auto_delayed": (2, True),
    "manual": (3, None),
    "disabled": (4, None),
}


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [(f, wintypes.DWORD) for f in (
        "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode",
        "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint")]


class SERVICE_STATUS_PROCESS(ctypes.Structure):
    _fields_ = [(f, wintypes.DWORD) for f in (
        "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode",
        "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint",
        "dwProcessId", "dwServiceFlags")]


class ENUM_SERVICE_STATUS_PROCESSW(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpDisplayName", wintypes.LPWSTR),
        ("ServiceStatusProcess", SERVICE_STATUS_PROCESS),
    ]


class QUERY_SERVICE_CONFIGW(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD), ("dwStartType", wintypes.DWORD),
        ("dwErrorControl", wintypes.DWORD), ("lpBinaryPathName", wintypes.LPWSTR),
        ("lpLoadOrderGroup", wintypes.LPWSTR), ("dwTagId", wintypes.DWORD),
        ("lpDependencies", wintypes.LPWSTR), ("lpServiceStartName", wintypes.LPWSTR),
        ("lpDisplayName", wintypes.LPWSTR),
    ]


_advapi32.OpenSCManagerW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
_advapi32.OpenSCManagerW.restype = wintypes.SC_HANDLE
_advapi32.OpenServiceW.argtypes = (wintypes.SC_HANDLE, wintypes.LPCWSTR, wintypes.DWORD)
_advapi32.OpenServiceW.restype = wintypes.SC_HANDLE
_advapi32.CloseServiceHandle.argtypes = (wintypes.SC_HANDLE,)
_advapi32.EnumServicesStatusExW.argtypes = (
    wintypes.SC_HANDLE, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR)
_advapi32.EnumServicesStatusExW.restype = wintypes.BOOL
_advapi32.QueryServiceStatus.argtypes = (wintypes.SC_HANDLE, ctypes.POINTER(SERVICE_STATUS))
_advapi32.QueryServiceStatus.restype = wintypes.BOOL
_advapi32.QueryServiceConfigW.argtypes = (
    wintypes.SC_HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
_advapi32.QueryServiceConfigW.restype = wintypes.BOOL
_advapi32.QueryServiceConfig2W.argtypes = (
    wintypes.SC_HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD))
_advapi32.QueryServiceConfig2W.restype = wintypes.BOOL
_advapi32.ChangeServiceConfigW.argtypes = (
    wintypes.SC_HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.SC_HANDLE, wintypes.LPCWSTR,
    wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR)
_advapi32.ChangeServiceConfigW.restype = wintypes.BOOL
_advapi32.ChangeServiceConfig2W.argtypes = (
    wintypes.SC_HANDLE, wintypes.DWORD, ctypes.c_void_p)
_advapi32.ChangeServiceConfig2W.restype = wintypes.BOOL
_advapi32.StartServiceW.argtypes = (wintypes.SC_HANDLE, wintypes.DWORD, wintypes.LPCWSTR)
_advapi32.StartServiceW.restype = wintypes.BOOL
_advapi32.ControlService.argtypes = (wintypes.SC_HANDLE, wintypes.DWORD, ctypes.c_void_p)
_advapi32.ControlService.restype = wintypes.BOOL


class ServiceInfo:
    """一个 Windows 服务的快照。"""

    __slots__ = ("name", "display", "state", "start", "pid", "binary", "protected")

    def __init__(self, name: str, display: str, state: int, start: str,
                 pid: int, binary: str, protected: bool):
        self.name = name
        self.display = display
        self.state = state
        self.start = start          # "auto"/"auto_delayed"/"manual"/"disabled"/"boot"/"system"
        self.pid = pid
        self.binary = binary
        self.protected = protected


class ServiceState:
    """服务的实时状态（供看门狗查询）。"""

    __slots__ = ("running", "start", "pid")

    def __init__(self, running: bool, start: str, pid: int = 0):
        self.running = running
        self.start = start
        self.pid = pid


def _scm_err() -> str:
    return str(ctypes.WinError(ctypes.get_last_error()))


def _open_scm() -> int:
    """打开服务控制管理器；优先全权限（需管理员），失败退回只读枚举权限。"""
    h = _advapi32.OpenSCManagerW(None, None, SC_MANAGER_ALL_ACCESS)
    if not h:
        h = _advapi32.OpenSCManagerW(None, None, SC_MANAGER_LIST)
    if not h:
        raise OSError(_scm_err())
    return h


def _query_service_config(handle) -> tuple[int, str] | None:
    """QueryServiceConfigW：返回 (dwStartType, 二进制路径)。"""
    size = wintypes.DWORD(0)
    _advapi32.QueryServiceConfigW(handle, None, 0, ctypes.byref(size))
    if not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if not _advapi32.QueryServiceConfigW(handle, buf, size.value, ctypes.byref(size)):
        return None
    cfg = ctypes.cast(buf, ctypes.POINTER(QUERY_SERVICE_CONFIGW)).contents
    start = int(cfg.dwStartType)
    binary = str(cfg.lpBinaryPathName or "")
    return start, binary


def _query_delayed(handle) -> bool:
    """是否为"自动(延迟启动)"。"""
    val = wintypes.BOOL(0)
    size = wintypes.DWORD(ctypes.sizeof(val))
    if _advapi32.QueryServiceConfig2W(
            handle, SERVICE_CONFIG_DELAYED_AUTO_START,
            ctypes.byref(val), size, ctypes.byref(size)):
        return bool(val.value)
    return False


def list_services() -> list[ServiceInfo]:
    """枚举所有 Win32 服务（不含内核驱动），按名称排序。"""
    scm = _open_scm()
    try:
        needed = wintypes.DWORD(0)
        returned = wintypes.DWORD(0)
        # 缓冲区按需加倍重试：两次调用之间服务状态可能变化导致 234 (ERROR_MORE_DATA)
        size = 0x10000
        buf = None
        for _ in range(6):
            buf = ctypes.create_string_buffer(size)
            if _advapi32.EnumServicesStatusExW(
                    scm, 0, SERVICE_WIN32, SERVICE_STATE_ALL, buf, size,
                    ctypes.byref(needed), ctypes.byref(returned), None, None):
                break
            code = ctypes.get_last_error()
            if code != 234:
                raise OSError(str(ctypes.WinError(code)))
            size = max(needed.value * 2, size * 2)
        else:
            raise OSError("EnumServicesStatusExW：多次重试后仍缓冲区不足")
        arr = ctypes.cast(buf, ctypes.POINTER(ENUM_SERVICE_STATUS_PROCESSW))
        infos: list[ServiceInfo] = []
        for i in range(returned.value):
            entry = arr[i]
            name = str(entry.lpServiceName or "")
            display = str(entry.lpDisplayName or "") or name
            state = int(entry.ServiceStatusProcess.dwCurrentState)
            pid = int(entry.ServiceStatusProcess.dwProcessId)
            start, binary = "manual", ""
            h = _advapi32.OpenServiceW(scm, name, SERVICE_QUERY_ACCESS)
            if h:
                try:
                    cfg = _query_service_config(h)
                    if cfg:
                        start = _START_KINDS.get(cfg[0], "manual")
                        binary = cfg[1]
                        if start == "auto" and _query_delayed(h):
                            start = "auto_delayed"
                finally:
                    _advapi32.CloseServiceHandle(h)
            infos.append(ServiceInfo(name, display, state, start, pid, binary,
                                     name in PROTECTED_SERVICES))
        infos.sort(key=lambda s: s.name.lower())
        return infos
    finally:
        _advapi32.CloseServiceHandle(scm)


def service_query(name: str) -> ServiceState | None:
    """查询单个服务的运行状态与启动类型。"""
    scm = _open_scm()
    try:
        h = _advapi32.OpenServiceW(scm, name, SERVICE_QUERY_ACCESS)
        if not h:
            return None
        try:
            status = SERVICE_STATUS()
            if not _advapi32.QueryServiceStatus(h, ctypes.byref(status)):
                return None
            cfg = _query_service_config(h)
            start = _START_KINDS.get(cfg[0], "manual") if cfg else "manual"
            if start == "auto" and _query_delayed(h):
                start = "auto_delayed"
            return ServiceState(int(status.dwCurrentState) == 4, start)
        finally:
            _advapi32.CloseServiceHandle(h)
    finally:
        _advapi32.CloseServiceHandle(scm)


def service_start(name: str, timeout: float = 15.0) -> tuple[bool, str]:
    """启动服务；已在运行视为成功。"""
    scm = _open_scm()
    try:
        h = _advapi32.OpenServiceW(scm, name, SERVICE_MANAGE_ACCESS)
        if not h:
            return False, _scm_err()
        try:
            if not _advapi32.StartServiceW(h, 0, None):
                code = ctypes.get_last_error()
                if code != 1056:  # ERROR_SERVICE_ALREADY_RUNNING
                    return False, str(ctypes.WinError(code))
                return True, ""
            deadline = time.monotonic() + timeout
            status = SERVICE_STATUS()
            while time.monotonic() < deadline:
                if _advapi32.QueryServiceStatus(h, ctypes.byref(status)):
                    if status.dwCurrentState == 4:
                        return True, ""
                    if status.dwCurrentState == 1:
                        return False, "服务启动后立即停止"
                time.sleep(0.3)
            return False, "启动超时"
        finally:
            _advapi32.CloseServiceHandle(h)
    finally:
        _advapi32.CloseServiceHandle(scm)


def service_stop(name: str, timeout: float = 20.0) -> tuple[bool, str]:
    """停止服务；已停止视为成功，等待"停止中"完成。"""
    scm = _open_scm()
    try:
        h = _advapi32.OpenServiceW(scm, name, SERVICE_MANAGE_ACCESS)
        if not h:
            return False, _scm_err()
        try:
            status = SERVICE_STATUS()
            if not _advapi32.ControlService(h, SERVICE_CONTROL_STOP, ctypes.byref(status)):
                code = ctypes.get_last_error()
                if code != 1062:  # ERROR_SERVICE_NOT_ACTIVE
                    return False, str(ctypes.WinError(code))
                return True, ""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if _advapi32.QueryServiceStatus(h, ctypes.byref(status)):
                    if status.dwCurrentState == 1:
                        return True, ""
                time.sleep(0.3)
            return False, "停止超时"
        finally:
            _advapi32.CloseServiceHandle(h)
    finally:
        _advapi32.CloseServiceHandle(scm)


def service_set_start(name: str, kind: str) -> tuple[bool, str]:
    """修改服务启动类型：auto / auto_delayed / manual / disabled。"""
    if kind not in _START_KIND_WRITE:
        return False, f"未知启动类型：{kind}"
    start, delayed = _START_KIND_WRITE[kind]
    scm = _open_scm()
    try:
        h = _advapi32.OpenServiceW(scm, name, SERVICE_MANAGE_ACCESS)
        if not h:
            return False, _scm_err()
        try:
            if not _advapi32.ChangeServiceConfigW(
                    h, SERVICE_NO_CHANGE, start, SERVICE_NO_CHANGE,
                    None, None, None, None, None, None, None):
                return False, _scm_err()
            if delayed is not None:
                val = wintypes.BOOL(1 if delayed else 0)
                if not _advapi32.ChangeServiceConfig2W(
                        h, SERVICE_CONFIG_DELAYED_AUTO_START, ctypes.byref(val)):
                    return False, _scm_err()
            return True, ""
        finally:
            _advapi32.CloseServiceHandle(h)
    finally:
        _advapi32.CloseServiceHandle(scm)


# ---------------------------------------------------------------- 智能省电配置

MANAGER_CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "卸载助手")
MANAGER_CONFIG_FILE = os.path.join(MANAGER_CONFIG_DIR, "manager_config.json")

# managed_processes: {进程名: "suspend"|"priority"}
# managed_services: {服务名: {"mode": "always_off"|"ondemand", "watch": "app.exe", "orig_start": "auto"|None}}
DEFAULT_MANAGER_CONFIG = {
    "idle_seconds": 60,
    "cpu_threshold": 1.0,
    "managed_processes": {},
    "managed_services": {},
}


def load_manager_config() -> dict:
    """读取智能省电配置，缺省项用默认值补齐。"""
    cfg = json.loads(json.dumps(DEFAULT_MANAGER_CONFIG))  # 深拷贝默认值
    try:
        with open(MANAGER_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_MANAGER_CONFIG})
    except (OSError, ValueError):
        pass
    return cfg


def save_manager_config(cfg: dict) -> bool:
    try:
        os.makedirs(MANAGER_CONFIG_DIR, exist_ok=True)
        with open(MANAGER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False
