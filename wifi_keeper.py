# -*- coding: utf-8 -*-
"""WiFi 守护 —— 断网自动重连 / 连得上但没网时自动切换到其他连过的 WiFi，保证电脑不断网。

运行方式：
  pythonw wifi_keeper.py            # 后台守护（无窗口，由开关或开机自启拉起）
  python  wifi_keeper.py --once     # 前台立即检测并修复一次，打印结果

控制接口（供 卸载助手命令行版.py import 调用）：
  turn_on()  / turn_off() / status_text() / run_once_text() / tail_log()
  daemon_main()                      # 守护主循环
开关状态、开机自启、后台进程三者联动：
  开启 → 写开关 + 加注册表自启 + 立即拉起后台守护
  关闭 → 清开关 + 删注册表自启 + 结束后台守护（关掉后就不会再自动连/切 WiFi）
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

try:
    import winreg
except ImportError:
    winreg = None

# ---------------------------------------------------------------- 路径与常量

APP_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "卸载助手")
CONFIG_FILE = os.path.join(APP_DIR, "wifi_keeper.json")
PID_FILE = os.path.join(APP_DIR, "wifi_keeper.pid")
LOG_FILE = os.path.join(APP_DIR, "wifi_keeper.log")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "WiFiKeeper"
MUTEX_NAME = "Local\\WiFiKeeper_UninstallHelper_Mutex"
ERROR_ALREADY_EXISTS = 183

DEFAULT_CFG = {
    "enabled": False,      # 总开关
    "last_good": "",       # 上次能正常上网的 WiFi 配置文件名
    "interval": 12,        # 常态检测间隔（秒）
    "switch_wait": 22,     # 切换一个 WiFi 后等待关联+联网的最长时间（秒）
    "cooldowns": {},       # 连不上/没网的WiFi冷却表：{配置文件名: 下次允许重试的时间戳}
}

# 联网探测地址（任一通即视为有网；HTTP 比 ping 更不易被拦）
CHECK_URLS = (
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://www.baidu.com",
)

# 某个WiFi切换后仍无法上网时，冷却这么多秒不再试它（到点自动恢复重试）
COOLDOWN_SECONDS = 600


# ---------------------------------------------------------------- 基础工具

def log(msg: str) -> None:
    """追加一行日志（带时间戳），超过 512KB 自动截断保留最后 200 行。"""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-200:]
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.writelines(tail)
            except OSError:
                pass
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def load_cfg() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CFG))  # 深拷贝默认值
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CFG})
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg: dict) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _decode(b: bytes) -> str:
    # netsh 输出编码随环境变化：用户会话 chcp 65001 时是 UTF-8，
    # 开机自启（无控制台、OEMCP=936）时是 GBK。严格 UTF-8 失败再回退 GBK。
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", errors="replace")


def run_netsh(args: list) -> str:
    try:
        r = subprocess.run(["netsh", "wlan"] + args,
                           capture_output=True, timeout=30)
        return _decode(r.stdout) + _decode(r.stderr)
    except Exception as e:
        log(f"netsh {' '.join(args)} 执行异常：{e}")
        return ""


# ---------------------------------------------------------------- WiFi 状态

def list_profiles() -> list:
    """本机保存过的 WiFi 配置文件（即连过的 WiFi），按 netsh 列出顺序。"""
    txt = run_netsh(["show", "profiles"])
    names = []
    for line in txt.splitlines():
        if ("配置文件" in line or "Profile" in line) and (":" in line or "：" in line):
            name = re.split(r"[:：]", line, 1)[1].strip()
            if name and name not in names:
                names.append(name)
    return names


def scan_networks() -> list:
    """扫描周围正在广播的 WiFi，返回 [{ssid, signal, auth, saved}]，信号强的在前。"""
    # mode=bssid 才会输出每个热点的 Signal 强度（普通模式这台机器没有信号行）
    txt = run_netsh(["show", "networks", "mode=bssid"])
    saved = set(list_profiles())
    nets = []
    cur = {}
    for line in txt.splitlines():
        m = re.match(r"\s*SSID\s+\d+\s*[:：]\s*(.+?)\s*$", line)
        if m:
            if cur.get("ssid"):
                nets.append(cur)
            cur = {"ssid": m.group(1)}
            continue
        m = re.match(r"\s*(身份验证|Authentication)\b\s*[:：]\s*(.+?)\s*$", line, re.I)
        if m and cur:
            cur["auth"] = m.group(2)
        m = re.match(r"\s*(信号|Signal)\b\s*[:：]\s*(\d+)", line, re.I)
        if m and cur:  # 同一SSID多个BSSID，保留最强信号
            cur["signal"] = max(cur.get("signal", 0), int(m.group(2)))
    if cur.get("ssid"):
        nets.append(cur)
    out, seen = [], set()
    for n in nets:  # 同名网络多BSSID去重，保留最高信号
        ssid = n.get("ssid", "")
        if not ssid:
            continue
        if ssid in seen:
            for o in out:
                if o["ssid"] == ssid:
                    o["signal"] = max(o.get("signal", 0), n.get("signal", 0))
            continue
        seen.add(ssid)
        n["signal"] = n.get("signal", 0)
        n["auth"] = n.get("auth", "")
        n["saved"] = ssid in saved
        out.append(n)
    out.sort(key=lambda x: x["signal"], reverse=True)
    return out


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def add_profile(ssid: str, password: str = "", open_net: bool = False) -> tuple:
    """用 SSID+密码生成 WiFi 配置文件并导入系统（不用手动连一次）。

    导入后该 WiFi 即进入自动切换池。返回 (ok, msg)。
    """
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "WiFi 名称不能为空"
    if not open_net and len(password) < 8:
        return False, "WiFi 密码至少 8 位（开放式网络请选 open 模式）"
    name = _xml_escape(ssid)
    if open_net:
        security = ("<authEncryption><authentication>OPEN</authentication>"
                    "<encryption>NONE</encryption><useOneX>false</useOneX>"
                    "</authEncryption>")
    else:
        security = ("<authEncryption><authentication>WPA2PSK</authentication>"
                    "<encryption>AES</encryption><useOneX>false</useOneX>"
                    "</authEncryption>"
                    f"<sharedKey><keyType>passPhrase</keyType><protected>false</protected>"
                    f"<keyMaterial>{_xml_escape(password)}</keyMaterial></sharedKey>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
        f"  <name>{name}</name>\n"
        "  <SSIDConfig><SSID>"
        f"<name>{name}</name>"
        "</SSID></SSIDConfig>\n"
        "  <connectionType>ESS</connectionType>\n"
        "  <connectionMode>auto</connectionMode>\n"
        "  <MSM><security>\n"
        f"    {security}\n"
        "  </security></MSM>\n"
        "</WLANProfile>\n"
    )
    xml_path = os.path.join(APP_DIR, f"wifi_profile_{int(time.time())}.xml")
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(xml_path, "w", encoding="utf-8-sig") as f:
            f.write(xml)
        r = subprocess.run(
            ["netsh", "wlan", "add", "profile", f"filename={xml_path}", "user=all"],
            capture_output=True, timeout=30)
        out = _decode(r.stdout) + _decode(r.stderr)
        if r.returncode != 0 or ("已添加" not in out and "added" not in out.lower()):
            return False, f"配置导入失败：{out.strip()[:150]}"
        log(f"已添加WiFi配置文件：{ssid}（open={open_net}）")
        return True, f"WiFi「{ssid}」已保存进自动切换池"
    except Exception as e:
        return False, f"添加异常：{e}"
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass


def forget_profile(ssid: str) -> tuple:
    """删除本机保存的 WiFi 配置文件（删后守护不再自动切它；系统里密码也会清掉）。"""
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "WiFi 名称不能为空"
    if ssid not in list_profiles():
        return False, f"本机没有保存名为「{ssid}」的WiFi配置"
    try:
        r = subprocess.run(["netsh", "wlan", "delete", "profile", f"name={ssid}"],
                           capture_output=True, timeout=30)
        out = _decode(r.stdout) + _decode(r.stderr)
        if r.returncode == 0 or ("已删除" in out or "deleted" in out.lower()):
            cfg = load_cfg()
            cd = cfg.get("cooldowns") or {}
            cd.pop(ssid, None)
            cfg["cooldowns"] = cd
            if cfg.get("last_good") == ssid:
                cfg["last_good"] = ""
            save_cfg(cfg)
            log(f"已删除WiFi配置文件：{ssid}")
            return True, f"WiFi「{ssid}」已删除，守护不会再自动切换到它"
        return False, f"删除失败：{out.strip()[:150]}"
    except Exception as e:
        return False, f"删除异常：{e}"


def show_interface() -> dict | None:
    """返回无线网卡当前状态：{state, ssid, profile}；无网卡返回 None。

    state: "connected" / "disconnected"
    兼容中英文系统输出（状态/State、SSID、配置文件/Profile）。
    """
    txt = run_netsh(["show", "interfaces"])
    state = ssid = profile = None
    for line in txt.splitlines():
        m = re.match(r"\s*(状态|State)\s*[:：]\s*(.+?)\s*$", line, re.I)
        if m:
            s = m.group(2).lower()
            state = "connected" if ("已连接" in m.group(2) or "connected" in s) else "disconnected"
        m = re.match(r"\s*SSID\s*[:：]\s*(.+?)\s*$", line)
        if m and m.group(1).lower() != "bssid":
            ssid = m.group(1)
        m = re.match(r"\s*(配置文件|Profile)\s*[:：]\s*(.+?)\s*$", line, re.I)
        if m:
            profile = m.group(2)
    if state is None:
        return None
    return {"state": state, "ssid": ssid or "", "profile": profile or ""}


def has_internet() -> bool:
    """探测是否真的能上网（任一探测地址连通即可）。"""
    for url in CHECK_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WiFiKeeper/1.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                r.read(64)
                return True
        except Exception:
            continue
    return False


def connect(profile: str) -> bool:
    """连接指定 WiFi 配置文件。netsh 连接是异步的，调用后需轮询状态。"""
    for attempt in (1, 2):
        try:
            r = subprocess.run(["netsh", "wlan", "connect", f"name={profile}"],
                               capture_output=True, timeout=20)
            if r.returncode == 0:
                return True
            err = _decode(r.stderr) or _decode(r.stdout)
            if "blocked" in err.lower() or "正在进行" in err:
                time.sleep(3)
                continue
            log(f"连接 {profile} 被拒绝：{err.strip()[:120]}")
            return False
        except Exception as e:
            log(f"连接 {profile} 异常：{e}")
            time.sleep(3)
    return False


def wait_network(timeout: int) -> bool:
    """等待 已关联 + 能上网，成功返回 True。"""
    end = time.time() + timeout
    while time.time() < end:  # 先等关联
        info = show_interface()
        if info and info["state"] == "connected":
            break
        time.sleep(2)
    else:
        return False
    while time.time() < end:  # 再等联网
        if has_internet():
            return True
        time.sleep(2)
    return False


def _set_cooldown(cfg: dict, profile: str, seconds: int = COOLDOWN_SECONDS) -> None:
    """把某个WiFi打入冷却：接下来 seconds 秒内切换时跳过它（到点自动重试）。"""
    cd = cfg.get("cooldowns") or {}
    cd[profile] = time.time() + seconds
    cfg["cooldowns"] = cd
    save_cfg(cfg)


def _clear_cooldown(cfg: dict, profile: str) -> None:
    cd = cfg.get("cooldowns") or {}
    if cd.pop(profile, None) is not None:
        cfg["cooldowns"] = cd
        save_cfg(cfg)


def _cooling(cfg: dict, profile: str) -> int:
    """返回某WiFi剩余冷却秒数；0 表示可正常尝试。"""
    cd = cfg.get("cooldowns") or {}
    return max(0, int(float(cd.get(profile, 0)) - time.time()))


def ensure_network(cfg: dict) -> tuple:
    """核心修复动作：遍历候选 WiFi 逐个尝试，直到能上网。

    断连时：优先上次好用的 WiFi，然后按已保存列表逐个试；
    连着但没网时：先试其他所有 WiFi，都不行再回到当前 WiFi。
    连不上/没网的 WiFi 会被冷却 COOLDOWN_SECONDS 秒，期间自动跳过
    （全部候选都在冷却中则无视冷却强制重试，保证断网时一定有人兜底）。
    """
    profiles = list_profiles()
    if not profiles:
        return False, "本机没有已保存的WiFi配置文件（没连过任何WiFi），无法自动连接"
    info = show_interface() or {"state": "disconnected", "ssid": "", "profile": ""}
    current = info.get("profile") or ""
    last_good = cfg.get("last_good") or ""

    if info["state"] != "connected":
        ordered = ([last_good] if last_good in profiles else [])
        ordered += [p for p in profiles if p not in ordered]
        reason = "WiFi已断开"
    else:
        ordered = [p for p in profiles if p != current]
        if last_good in profiles and last_good != current:
            ordered = [last_good] + [p for p in ordered if p != last_good]
        reason = f"连着 {info.get('ssid') or current} 但没有网"

    # 冷却过滤
    active = [p for p in ordered if _cooling(cfg, p) <= 0]
    skipped = [p for p in ordered if _cooling(cfg, p) > 0]
    if not active:
        log("候选WiFi全部在冷却中，强制无视冷却重试")
        active = ordered
        skipped = []
    elif skipped:
        log("冷却中暂时跳过：" + "、".join(f"{p}({_cooling(cfg, p)}s)" for p in skipped))

    wait = int(cfg.get("switch_wait", DEFAULT_CFG["switch_wait"]))
    log(f"{reason}，开始自动切换，本轮尝试 {len(active)} 个：{('、'.join(active))[:150]}")
    for p in active:
        log(f"尝试连接：{p}")
        if connect(p) and wait_network(wait):
            cfg["last_good"] = p
            _clear_cooldown(cfg, p)
            save_cfg(cfg)
            return True, f"已自动连上并可正常上网：{p}"
        _set_cooldown(cfg, p)
        log(f"{p} 连接后仍无法上网，冷却 {COOLDOWN_SECONDS} 秒再试，换下一个")
    if current and current not in active:
        log(f"其他WiFi均不可用，尝试回到原WiFi：{current}")
        if connect(current) and wait_network(wait):
            _clear_cooldown(cfg, current)
            save_cfg(cfg)
            return True, f"已恢复原WiFi连接：{current}"
    return False, "所有已保存的WiFi都试过了仍无法上网，下个检测周期继续重试"


# ---------------------------------------------------------------- 单实例 / 进程

def _proc_cmdline(pid: int) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
            capture_output=True, timeout=10)
        return _decode(r.stdout).strip()
    except Exception:
        return ""


def is_running() -> tuple:
    """后台守护是否在运行：(bool, pid)。用 PID 文件 + 命令行双重确认。"""
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False, None
    cmd = _proc_cmdline(pid)
    if not cmd:
        return False, None
    if "wifi_keeper" in cmd.lower() or "--wifi-daemon" in cmd.lower():
        return True, pid
    return False, None


def _write_pid() -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def stop_daemon() -> bool:
    running, pid = is_running()
    if not running:
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        return False
    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                   capture_output=True)
    time.sleep(1.0)
    dead = not _proc_cmdline(pid)
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    return dead


def _daemon_command() -> list:
    """开机自启 / 拉起守护用的命令行（源码模式用 pythonw 无黑窗，打包模式用 exe 自身）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--wifi-daemon"]
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wifi_keeper.py")
    return [pythonw, script]


def launch_daemon() -> bool:
    if is_running()[0]:
        return False
    args = _daemon_command()
    try:
        subprocess.Popen(
            args,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception as e:
        log(f"守护进程启动失败：{e}")
        return False
    for _ in range(10):  # 等它写 PID 文件
        time.sleep(0.4)
        if is_running()[0]:
            return True
    return is_running()[0]


# ---------------------------------------------------------------- 开机自启（注册表 Run）

def get_autostart() -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_VALUE)[0]
    except OSError:
        return None


def set_autostart() -> None:
    if winreg is None:
        raise OSError("winreg 不可用")
    cmd = _daemon_command()
    cmdline = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmdline)


def clear_autostart() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 对外控制接口

def turn_on() -> str:
    os.makedirs(APP_DIR, exist_ok=True)
    cfg = load_cfg()
    cfg["enabled"] = True
    save_cfg(cfg)
    auto_ok = True
    auto_err = ""
    try:
        set_autostart()
    except Exception as e:
        auto_ok = False
        auto_err = str(e)
    already = is_running()[0]
    started = True if already else launch_daemon()
    profiles = list_profiles()

    lines = ["✅ WiFi 守护已开启"]
    lines.append(f"   开机自启：{'已加入（开机后自动后台运行，无需登录操作）' if auto_ok else '设置失败：' + auto_err}")
    lines.append(f"   后台守护：{'已在运行中' if already else ('已启动' if started else '启动失败，请查看日志')}")
    if profiles:
        show = "、".join(profiles[:6]) + ("…" if len(profiles) > 6 else "")
        lines.append(f"   已保存WiFi（{len(profiles)} 个）：{show}")
        lines.append("   策略：WiFi断开→自动重连；连着但没网→自动换其他WiFi，保证不断网。")
    else:
        lines.append("   ⚠ 本机还没连过任何WiFi：请先手动连一次，之后守护才能自动重连/切换。")
    return "\n".join(lines)


def turn_off() -> str:
    cfg = load_cfg()
    cfg["enabled"] = False
    save_cfg(cfg)
    stopped = stop_daemon()
    clear_autostart()
    return ("✅ WiFi 守护已关闭：后台进程已停止，开机自启已取消。\n"
            "   现在不会再自动连接或切换WiFi；需要时重新开启即可。")


def status_text() -> str:
    cfg = load_cfg()
    running, pid = is_running()
    auto = get_autostart()
    info = show_interface()
    profiles = list_profiles()

    lines = ["========== WiFi 守护状态 =========="]
    lines.append(f"  总开关　：{'🟢 开启' if cfg.get('enabled') else '⚪ 关闭'}")
    lines.append(f"  后台守护：{('🟢 运行中 (PID ' + str(pid) + ')') if running else '⚪ 未运行'}")
    lines.append(f"  开机自启：{'🟢 已设置（开机自动运行）' if auto else '⚪ 未设置'}")
    if info is None:
        lines.append("  无线网卡：未检测到（本机可能没有WiFi网卡）")
    elif info["state"] == "connected":
        net_ok = has_internet()
        lines.append(f"  当前WiFi：{info.get('ssid') or '?'}（配置文件：{info.get('profile') or '?'}）")
        lines.append(f"  网络连通：{'🟢 正常' if net_ok else '🔴 连得上但没网 —— 守护会自动换其他WiFi'}")
    else:
        lines.append("  当前WiFi：🔴 未连接 —— 守护会自动重连")
    lines.append(f"  上次好用：{cfg.get('last_good') or '（暂无记录）'}")
    if profiles:
        marks = []
        for p in profiles:
            left = _cooling(cfg, p)
            marks.append(f"{p}（冷却{left // 60 + 1}分钟）" if left > 0 else p)
        lines.append(f"  已保存WiFi（{len(profiles)}）：" + "、".join(marks))
        if len(profiles) < 2:
            lines.append("  ⚠ 可自动切换的WiFi不足2个，建议用「添加WiFi」再加1~2个备选")
    else:
        lines.append("  已保存WiFi：无（用「添加WiFi」输入名称密码，或手动连一次）")
    lines.append("  日志文件：" + LOG_FILE)
    lines.append("===================================")
    return "\n".join(lines)


def scan_text() -> str:
    nets = scan_networks()
    lines = ["========== 周围可用 WiFi =========="]
    if not nets:
        lines.append("  没扫描到任何 WiFi（无线网卡可能被关闭，或附近确实没信号）")
    for i, n in enumerate(nets, 1):
        tag = "✅已保存，可自动切换" if n["saved"] else "⬜未保存（加密码后即可自动切换）"
        lines.append(f"  {i}. {n['ssid']}　信号{n['signal']:>3}%　{n['auth'] or '?'}　{tag}")
    lines.append("")
    lines.append("  添加方法：wifi add <WiFi名称> <密码>  （开放式网络：wifi add <名称> --open）")
    lines.append("===================================")
    return "\n".join(lines)


def add_profile_text(ssid: str, password: str = "", open_net: bool = False, test: bool = True) -> str:
    """添加WiFi配置文件；test=True 时立即连接验证，失败则自动恢复原网络。"""
    ok, msg = add_profile(ssid, password, open_net)
    if not ok:
        return "❌ " + msg
    out = ["✅ " + msg]
    name = (ssid or "").strip()
    if test and name:
        out.append("   正在尝试连接验证（最多 30 秒）…")
        cfg = load_cfg()
        if connect(name) and wait_network(30):
            cfg["last_good"] = name
            _clear_cooldown(cfg, name)
            save_cfg(cfg)
            out.append(f"   ✅ 验证通过：{name} 能正常上网，已加入自动切换池并优先使用。")
        else:
            _set_cooldown(cfg, name)
            out.append("   ⚠ 已保存但当前连不上或没网（密码不对/信号弱/路由器断网）。")
            out.append("   正在恢复原网络…")
            ok2, msg2 = ensure_network(cfg)
            out.append(("   ✅ " if ok2 else "   ⚠ ") + msg2)
    return "\n".join(out)


def run_once_text() -> str:
    info = show_interface()
    if info is None:
        return "⚠ 未检测到无线网卡，无法执行WiFi守护。"
    cfg = load_cfg()
    if info["state"] == "connected" and has_internet():
        if info.get("profile"):
            cfg["last_good"] = info["profile"]
            _clear_cooldown(cfg, info["profile"])
            save_cfg(cfg)
        return f"✅ 网络正常，当前WiFi：{info.get('ssid') or '?'}（{info.get('profile') or '?'}），无需切换。"
    ok, msg = ensure_network(cfg)
    return ("✅ " if ok else "⚠ ") + msg


def tail_log(n: int = 60) -> str:
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]) or "（暂无日志）"
    except OSError:
        return "（暂无日志）"


# ---------------------------------------------------------------- 守护主循环

def daemon_main() -> None:
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW(None, False, MUTEX_NAME)
    if kernel.GetLastError() == ERROR_ALREADY_EXISTS:
        return  # 已有一个守护在跑
    _write_pid()
    log("==== WiFi 守护启动 ====")
    fail_streak = 0
    try:
        while True:
            cfg = load_cfg()
            if not cfg.get("enabled", False):
                log("检测到开关已关闭，守护退出")
                break
            interval = int(cfg.get("interval", DEFAULT_CFG["interval"]))
            try:
                info = show_interface()
                if info is None:
                    time.sleep(30)  # 无网卡，慢轮询
                    continue
                if info["state"] == "connected" and has_internet():
                    changed = False
                    if info.get("profile") and info["profile"] != cfg.get("last_good"):
                        cfg["last_good"] = info["profile"]
                        changed = True
                        log(f"记录当前可用WiFi：{info['profile']}")
                    if info.get("profile") and _cooling(cfg, info["profile"]) > 0:
                        _clear_cooldown(cfg, info["profile"])  # 恢复了就解除冷却
                        changed = False  # _clear_cooldown 已落盘
                    if changed:
                        save_cfg(cfg)
                    fail_streak = 0
                else:
                    fail_streak += 1
                    if info["state"] == "connected":
                        log(f"已连接 {info.get('ssid') or '?'} 但无法上网（第 {fail_streak} 次）")
                        if fail_streak < 2:  # 连续 2 次确认没网才切换，避免抖动
                            time.sleep(interval)
                            continue
                    else:
                        log("WiFi 处于断开状态")
                    try:
                        ok, msg = ensure_network(cfg)
                        log(("✅ " if ok else "⚠ ") + msg)
                    except Exception as e:
                        log(f"修复动作异常：{e}")
                    fail_streak = 0
            except Exception as e:
                log(f"检测循环异常：{e}")
            time.sleep(interval)
    finally:
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        log("==== WiFi 守护已停止 ====")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        print(run_once_text())
    else:
        daemon_main()
