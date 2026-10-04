# -*- coding: utf-8 -*-
"""
双脑一体 —— 「一个身体，两个大脑」
  大脑A = 小龙虾 OpenClaw（Node 生态个人 AI 助理）
  大脑B = 爱马仕 Hermes Agent（NousResearch 自主智能体）
  身体  = 本机执行器（复用 core.py：进程/服务/系统操作）
可视化：双脑活动实时面板；沟通：聊天界面；并带能力体检与优化建议。
"""
import os
import sys
import json
import time
import queue
import secrets
import threading
import subprocess
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import ttk, scrolledtext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import core

CONF_PATH = os.path.join(BASE, "dualbrain_config.json")
TOOLS = os.path.join(BASE, "tools")
NODE_DIR = os.path.join(TOOLS, "node")
OPENCLAW_CMD = os.path.join(NODE_DIR, "openclaw.cmd")
HERMES_CMD = os.path.join(os.path.dirname(sys.executable), "Scripts", "hermes.exe")
# 身体共享工作区：两个大脑的所有实际操作都发生在这里（高维视角监听此目录）
WORKSPACE = os.path.join(BASE, "brain_workspace")
os.makedirs(WORKSPACE, exist_ok=True)
# 手机控制台「截图」：截屏存工作区 screenshots/，由 Web 的 /shot/ 接口提供给手机
SCREENSHOT_DIR = os.path.join(WORKSPACE, "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

DEFAULT_CFG = {
    "api_base": "",
    "api_key": "",
    "api_model": "",
    "openclaw_cmd": OPENCLAW_CMD,
    "hermes_cmd": HERMES_CMD,
    "route_rules": {},
    "web_port": 8765,
    "web_token": secrets.token_hex(4),
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _run_capture(cmd, timeout=60, env_extra=None):
    """跑一条命令，返回 (ok, 输出文本)。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            env=env, cwd=BASE,
        )
        out = (p.stdout or b"").decode("utf-8", "replace") + \
              (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode == 0, out.strip()
    except FileNotFoundError:
        return False, "命令不存在"
    except subprocess.TimeoutExpired:
        return False, "超时"
    except Exception as e:
        return False, str(e)


def _stream_capture(cmd, on_line, timeout, env_extra=None, cwd=None):
    """实时逐行执行：大脑边工作边输出（高维视角的关键）。

    返回 (ok, 完整输出文本)。超时自动 kill。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=cwd or BASE, env=env)
    except FileNotFoundError:
        return False, "命令不存在"
    except Exception as e:
        return False, str(e)

    lines = []
    kill_timer = threading.Timer(timeout, p.kill)
    kill_timer.daemon = True
    kill_timer.start()
    try:
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line.strip():
                continue
            if len(line) > 220:          # 过长的日志行截断，动作流保持可读
                line = line[:220] + "…"
            lines.append(line)
            try:
                on_line(line)
            except Exception:
                pass
    finally:
        kill_timer.cancel()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
    if not lines:
        return p.returncode == 0, ""
    return p.returncode == 0, "\n".join(lines)


# ---------------------------------------------------------------- 本地小脑
LOCAL_REPLIES = []


def body_list_procs(_msg):
    try:
        procs = core.list_processes()
        procs.sort(key=lambda p: (p.mem_bytes or 0), reverse=True)
        rows = [f"{p.name:<28} PID={p.pid:<8} 内存={core.format_bytes(p.mem_bytes or 0)}"
                for p in procs[:15]]
        return f"身体已执行【查看进程】，内存占用前 15：\n" + "\n".join(rows)
    except Exception as e:
        return f"身体执行失败：{e}"


def body_sysinfo(_msg):
    import platform
    total, used, free = shutil_disk()
    return (
        f"身体自检报告：\n"
        f"  系统：{platform.system()} {platform.release()} ({platform.machine()})\n"
        f"  Python：{platform.python_version()}\n"
        f"  C盘：已用 {used:.0f}GB / 总 {total:.0f}GB，剩余 {free:.0f}GB"
    )


def shutil_disk():
    import shutil as _sh
    t, u, f = _sh.disk_usage("C:")
    return t / 2**30, u / 2**30, f / 2**30


def body_open_url(msg):
    import re
    m = re.search(r"https?://\S+", msg)
    if not m:
        return None
    url = m.group(0).rstrip("。，！？")
    subprocess.Popen(["rundll32", "url.dll,FileProtocolHandler", url])
    return f"身体已执行【打开网页】：{url}"


def take_screenshot():
    """截取本机全部屏幕，保存到工作区 screenshots/，返回 (文件名, 绝对路径)。"""
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True)
    fn = "shot_%s.png" % time.strftime("%Y%m%d_%H%M%S")
    fp = os.path.join(SCREENSHOT_DIR, fn)
    img.save(fp, "PNG")
    # 只保留最近 20 张，避免工作区无限膨胀
    try:
        olds = sorted(
            (os.path.join(SCREENSHOT_DIR, f)
             for f in os.listdir(SCREENSHOT_DIR) if f.lower().endswith(".png")),
            key=os.path.getmtime)
        for old in olds[:-20]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass
    return fn, fp


def local_cortex(msg):
    """本地小脑：无 API key 时的兜底回答 + 身体动作。"""
    low = msg.lower()
    if any(k in low for k in ("进程", "内存", "任务管理")):
        return body_list_procs(msg), "身体"
    if any(k in low for k in ("系统信息", "电脑配置", "自检", "体检")):
        return body_sysinfo(msg), "身体"
    if "http" in low:
        r = body_open_url(msg)
        if r:
            return r, "身体"
    if any(k in low for k in ("你好", "您好", "hi", "hello", "在吗")):
        return ("你好！我是双脑一体的本地小脑。两个大脑（小龙虾/爱马仕）需要配置 "
                "API Key 才能在线思考；在此之前，系统类指令由我直接驱动身体执行，"
                "比如「查看进程」「系统信息」或「打开 https://…」。"), "本地小脑"
    return None, None


# ---------------------------------------------------------------- 大脑抽象
class Brain:
    name = ""
    title = ""
    tag = ""
    timeout = 180  # 单次任务超时（小龙虾启动慢，用更长超时）

    def __init__(self, cfg):
        self.cfg = cfg

    def available(self):
        raise NotImplementedError

    def build_cmd(self, msg):
        raise NotImplementedError

    def _own_key_ready(self):
        """检测该大脑自身配置文件里是否已存有 API Key。"""
        home = os.path.expanduser("~")
        try:
            if self.name == "hermes":
                envf = os.path.join(home, ".hermes", ".env")
                if os.path.isfile(envf):
                    with open(envf, "r", encoding="utf-8", errors="replace") as f:
                        return any("API_KEY=" in ln for ln in f)
                return False
            # openclaw：存在 provider 配置文件即视为已配置
            return os.path.isfile(os.path.join(home, ".openclaw", "openclaw.json"))
        except Exception:
            return False

    def key_ready(self):
        return bool(self.cfg.get("api_key")) or self._own_key_ready()

    def env_extra(self):
        c = self.cfg
        env = {}
        if c.get("api_key"):
            env["OPENAI_API_KEY"] = c["api_key"]
            env["GLM_API_KEY"] = c["api_key"]
        if c.get("api_base"):
            env["OPENAI_BASE_URL"] = c["api_base"]
            env["GLM_BASE_URL"] = c["api_base"]
        if c.get("api_model"):
            env["OPENAI_MODEL"] = c["api_model"]
        return env

    def ask_thread(self, msg, on_line, on_done):
        def run():
            ok = self.available()
            if not ok:
                on_line(f"⚠️ {self.title} 未安装/不可用，跳过")
                on_done(self, False, "未安装")
                return
            if not self.key_ready():
                on_line(f"⚠️ {self.title} 未配置 API Key，无法在线思考（点右上角「⚙ 设置 API」）")
                on_done(self, False, "未配置Key")
                return
            t0 = time.time()
            on_line("▶ 收到消息，思考中…（工作区：" + WORKSPACE + "）")
            ok, out = self.ask_sync(msg, on_line)
            dt = time.time() - t0
            on_done(self, ok and bool(out.strip()), f"{dt:.1f}s", out)
        threading.Thread(target=run, daemon=True).start()

    def ask_sync(self, msg, on_line, timeout=None):
        """实时提问（动作流逐行回调），返回 (ok, 完整输出)。"""
        if timeout is None:
            timeout = self.timeout
        return _stream_capture(self.build_cmd(msg), on_line, timeout,
                               env_extra=self.env_extra(), cwd=WORKSPACE)

    @staticmethod
    def _retryable(out):
        low = (out or "").lower()
        keys = ("429", "访问量过大", "1305", "rate limit", "timed out",
                "timeout", "超时", "overloaded", "temporarily")
        return any(k in low for k in keys)

    def ask_with_retry(self, msg, on_line, attempts=3, timeout=None):
        """带限流重试的提问，返回 (ok, 输出)。"""
        if timeout is None:
            timeout = self.timeout
        for i in range(attempts):
            ok, out = self._ask_once(msg, timeout)
            if ok or not self._retryable(out) or i == attempts - 1:
                return ok, out
            wait = 10 * (i + 1)
            on_line(f"⚠️ 服务繁忙/超时（第 {i+1} 次尝试失败），{wait} 秒后自动重试…")
            time.sleep(wait)
        return False, ""

    @staticmethod
    def _noop(_line):
        pass

    def _ask_once(self, msg, timeout):
        return self.ask_sync(msg, self._noop, timeout)


class BrainClaw(Brain):
    name = "claw"
    title = "小龙虾 OpenClaw"
    tag = "🦞"
    timeout = 240  # 启动+插件加载较慢，放宽超时

    def available(self):
        return os.path.isfile(self.cfg.get("openclaw_cmd", ""))

    def build_cmd(self, msg):
        model = self.cfg.get("api_model") or "glm-4-flash-250414"
        return [self.cfg["openclaw_cmd"], "agent", "exec", msg,
                "--model", f"zhipu/{model}",
                "--fallback", "zhipu/glm-4.7-flash",
                "--no-auth-env-only", "--json",
                "--cwd", WORKSPACE,
                "--state-dir", os.path.join(os.path.expanduser("~"), ".openclaw", "state")]

    def _ask_once(self, msg, timeout):
        ok, out = self.ask_sync(msg, self._noop, timeout)
        # 解析 openclaw --json 输出（输出可能混有 stderr 日志，逐个提取 JSON 对象）
        idx = out.find("{")
        while idx >= 0:
            try:
                data, end = json.JSONDecoder().raw_decode(out[idx:])
            except Exception:
                break
            if isinstance(data, dict) and "ok" in data:
                if data.get("ok"):
                    final = (data.get("final") or "").strip()
                    if final:
                        return True, final
                else:
                    err = data.get("error")
                    msgtxt = err.get("message") if isinstance(err, dict) else None
                    return False, msgtxt or out or "失败"
            nxt = out.find("{", idx + end)
            if nxt < 0:
                break
            idx = nxt
        return ok, out


class BrainHermes(Brain):
    name = "hermes"
    title = "爱马仕 Hermes"
    tag = "☤"
    timeout = 180
    _git_bash = None  # 缓存 Git Bash 路径

    @classmethod
    def _find_git_bash(cls):
        """定位 Git Bash（爱马仕的文件写入工具依赖它）。"""
        if cls._git_bash:
            return cls._git_bash
        cands = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            os.path.join(os.path.expanduser("~"),
                         r"AppData\Local\Programs\Git\bin\bash.exe"),
        ]
        for p in cands:
            if os.path.isfile(p):
                cls._git_bash = p
                return p
        try:
            git = subprocess.run(["git", "--exec-path"], capture_output=True,
                                 timeout=10).stdout.decode("gbk", "replace").strip()
            if git:
                top = os.path.dirname(os.path.dirname(git))
                bash = os.path.join(top, "bin", "bash.exe")
                if os.path.isfile(bash):
                    cls._git_bash = bash
                    return bash
        except Exception:
            pass
        return None

    def available(self):
        return os.path.isfile(self.cfg.get("hermes_cmd", ""))

    def build_cmd(self, msg):
        cmd = [self.cfg["hermes_cmd"], "--provider", "zai"]
        if self.cfg.get("api_model"):
            cmd += ["-m", self.cfg["api_model"]]
        cmd += ["-z", msg]
        return cmd

    def env_extra(self):
        env = super().env_extra()
        bash = self._find_git_bash()
        if bash:
            env["HERMES_GIT_BASH_PATH"] = bash
        return env


# ---------------------------------------------------------------- 直连大脑
BRAIN_API_SYSTEM = (
    "你是『直连大脑』——「一个身体，两个大脑」体系中最快的思考大脑。"
    "你的所有文件操作都发生在一个共享工作区里（伙伴大脑小龙虾也使用它，"
    "用户可以从高维视角观察到工作区文件变动）。"
    "遇到需要创建/读取文件、执行命令、查询系统的任务时，主动调用工具完成，"
    "不要只口头描述。用简体中文交流，回答保持简洁。"
    "当前日期：{date}。"
).format(date=time.strftime("%Y-%m-%d %A"))


class BrainAPI(Brain):
    """内置直连大脑：跳过 CLI/插件，直接调 OpenAI 兼容接口 + 工具循环。"""
    name = "api"
    title = "直连大脑"
    tag = "⚡"
    timeout = 150

    TOOLS_SPEC = [
        {"type": "function", "function": {
            "name": "list_files",
            "description": "列出共享工作区中的文件和目录",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "read_file",
            "description": "读取共享工作区内的文本文件",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "工作区内相对路径"}},
                "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "write_file",
            "description": "在共享工作区内创建或覆盖文本文件",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}},
                "required": ["path", "content"]}}},
        {"type": "function", "function": {
            "name": "run_cmd",
            "description": "在共享工作区执行 Windows 命令（如 dir、echo、python 脚本），超时60秒",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string"}},
                "required": ["command"]}}},
        {"type": "function", "function": {
            "name": "sys_info",
            "description": "获取本机系统信息（系统版本/Python版本/磁盘占用）",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
    ]

    def available(self):
        return bool(self.cfg.get("api_key") and self.cfg.get("api_base"))

    def key_ready(self):
        return self.available()

    def build_cmd(self, msg):
        return None  # 不经过子进程

    # ---- 工具实现 ---------------------------------------------------
    @staticmethod
    def _safe_path(p):
        path = os.path.abspath(os.path.join(WORKSPACE, p))
        if not (path == WORKSPACE or path.startswith(WORKSPACE + os.sep)):
            raise ValueError("路径必须在共享工作区 brain_workspace 内")
        return path

    def _tool_exec(self, name, args):
        if name == "list_files":
            items = []
            for root, dirs, files in os.walk(WORKSPACE):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fn in files:
                    fp = os.path.join(root, fn)
                    rel = os.path.relpath(fp, WORKSPACE)
                    items.append(f"{rel} ({os.path.getsize(fp)}B)")
            return "工作区文件：\n" + ("\n".join(items) or "（空）")
        if name == "read_file":
            path = self._safe_path(args.get("path", ""))
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[:8000]
        if name == "write_file":
            path = self._safe_path(args.get("path", ""))
            os.makedirs(os.path.dirname(path) or WORKSPACE, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(args.get("content", ""))
            return f"已写入 {args.get('path')}（{len(args.get('content', ''))}字符）"
        if name == "run_cmd":
            r = subprocess.run(args.get("command", ""), shell=True,
                               capture_output=True, timeout=60, cwd=WORKSPACE)
            out = (r.stdout or b"").decode("gbk", "replace") + \
                  (r.stderr or b"").decode("gbk", "replace")
            return f"exit={r.returncode}\n{out.strip()[:4000] or '（无输出）'}"
        if name == "sys_info":
            return body_sysinfo("")
        raise ValueError(f"未知工具: {name}")

    # ---- API 调用 ---------------------------------------------------
    def _api_call(self, messages):
        url = self.cfg["api_base"].rstrip("/") + "/chat/completions"
        body = {"model": self.cfg.get("api_model") or "glm-4-flash-250414",
                "messages": messages, "tools": self.TOOLS_SPEC,
                "max_tokens": 4096}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.cfg['api_key']}"})
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.load(resp)
        return data["choices"][0]["message"]

    def ask_sync(self, msg, on_line, timeout=None):
        """agent 循环：模型思考 → 调工具 → 反馈结果 → 直至给出最终回答。"""
        if timeout is None:
            timeout = self.timeout
        deadline = time.time() + timeout
        messages = [{"role": "system", "content": BRAIN_API_SYSTEM},
                    {"role": "user", "content": msg}]
        final_text = ""
        for _round in range(8):
            if time.time() > deadline:
                on_line("⏱ 任务整体超时")
                return False, final_text
            try:
                choice = self._api_call(messages)
            except urllib.error.HTTPError as e:
                body = e.read()[:200].decode("utf-8", "replace")
                return False, f"HTTP {e.code}: {body}"
            except Exception as e:
                return False, str(e)
            tool_calls = choice.get("tool_calls")
            if tool_calls:
                assistant = {"role": "assistant",
                             "content": choice.get("content") or "",
                             "tool_calls": tool_calls}
                messages.append(assistant)
                for tc in tool_calls:
                    fn = tc.get("function", {}).get("name", "?")
                    try:
                        args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                    except Exception:
                        args = {}
                    on_line(f"🔧 工具 {fn} {json.dumps(args, ensure_ascii=False)[:120]}")
                    try:
                        result = self._tool_exec(fn, args)
                    except Exception as e:
                        result = f"工具错误: {e}"
                    on_line(f"   ↳ {str(result)[:120]}")
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id", ""),
                                     "content": str(result)[:8000]})
            else:
                final_text = (choice.get("content") or "").strip()
                for ln in final_text.splitlines():
                    on_line(ln)
                return True, final_text
        return True, final_text or "（达到工具轮次上限，任务未完成）"


BRAINS = {}


# ---------------------------------------------------------------- 手机 Web 控制台
PAGE_HTML = """<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>双脑一体 · 手机控制台</title>
<style>
 body{margin:0;background:#0d1117;color:#e6edf3;font-family:system-ui,"Microsoft YaHei UI",sans-serif;display:flex;flex-direction:column;height:100vh}
 header{padding:8px 12px;background:#161b22;border-bottom:1px solid #30363d;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
 .chip{font-size:12px;padding:2px 8px;border-radius:10px;background:#21262d;border:1px solid #30363d}
 .st-idle{color:#8b949e}.st-working{color:#3fb950;border-color:#238636}.st-done{color:#58a6ff}.st-error{color:#f85149}
 #tabs{display:flex;gap:4px;padding:6px 12px 0}
 .tab{padding:6px 14px;background:#21262d;border-radius:8px 8px 0 0;font-size:13px;cursor:pointer;border:1px solid #30363d;border-bottom:none}
 .tab.on{background:#0d1117;color:#58a6ff}
 main{flex:1;overflow-y:auto;padding:10px 12px}
 .msg{margin:6px 0;white-space:pre-wrap;word-break:break-word;line-height:1.45}
 .user{color:#79c0ff;font-weight:600}
 .claw{color:#56d364}.hermes{color:#d2a8ff}.api{color:#79c0ff}.local{color:#e3b341}.sys{color:#8b949e;font-size:12px}
 .log{font-family:Consolas,monospace;font-size:11px;color:#8b949e;white-space:pre-wrap}
 .file{color:#56d364;font-size:12px;font-family:Consolas,monospace}
 .shot{margin:8px 0}
 .shot img{max-width:100%;border-radius:10px;border:1px solid #30363d;display:block;cursor:zoom-in}
 .shot .cap{font-size:12px;color:#8b949e;margin-top:3px}
 footer{display:flex;gap:6px;padding:8px 10px;background:#161b22;border-top:1px solid #30363d}
 select,input,button{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:8px;font-size:14px}
 #inp{flex:1}
 button:disabled{opacity:.45}
</style></head><body>
<header><b>🧠 双脑一体</b>
 <span class="chip st-idle" id="st_claw">🦞 待机</span>
 <span class="chip st-idle" id="st_api">⚡ 待机</span>
 <span class="chip st-idle" id="st_hermes">☤ 待机</span>
</header>
<div id="tabs"><div class="tab on" data-t="chat">💬 沟通</div><div class="tab" data-t="log">📃 动作流</div><div class="tab" data-t="file">📁 工作区</div></div>
<main id="view_chat"></main><main id="view_log" style="display:none"></main><main id="view_file" style="display:none"></main>
<footer><select id="route"><option>自动</option><option>直连</option><option>小龙虾</option><option>爱马仕</option><option>双脑会商</option></select>
<input id="inp" placeholder="发消息给双脑一体…"><button id="send">发送</button></footer>
<script>
let token=localStorage.getItem('db_token')||'';
const q=new URLSearchParams(location.search);if(q.get('t')){token=q.get('t');localStorage.setItem('db_token',token);}
let last=0,busy=false;
const $=id=>document.getElementById(id);
function add(v,cls,t){const d=document.createElement('div');d.className=cls;d.textContent=t;$(v).appendChild(d);$(v).scrollTop=1e9;}
function addShot(src,cap){const d=document.createElement('div');d.className='shot';
 const url=src+'?token='+encodeURIComponent(token);
 const img=document.createElement('img');img.src=url;img.loading='lazy';
 img.onclick=()=>window.open(url,'_blank');
 d.appendChild(img);
 const c=document.createElement('div');c.className='cap';c.textContent=cap||'';d.appendChild(c);
 $('view_chat').appendChild(d);$('view_chat').scrollTop=1e9;}
function setChip(n,s){const el=$('st_'+n);if(!el)return;el.className='chip st-'+s;
 el.textContent={'claw':'🦞','api':'⚡','hermes':'☤'}[n]+({'idle':' 待机','working':' 思考中','done':' 完成','error':' 出错'})[s];}
async function poll(){
 try{const r=await fetch('/api/events?since='+last,{headers:{'X-Token':token}});
  if(r.status===401){const t=prompt('请输入控制台令牌（电脑端点 🌐 手机控制 按钮查看）');if(t){token=t;localStorage.setItem('db_token',t);}return;}
  const j=await r.json();
  for(const ev of j.events){const id=ev[0],kind=ev[1];last=Math.max(last,id);
   if(kind==='chat')add('view_chat',ev[2],ev[3]);
   else if(kind==='shot')addShot(ev[2],ev[3]);
   else if(kind==='log')add('view_log','log','['+ev[2]+'] '+ev[3]);
   else if(kind==='file')add('view_file','file',ev[2]);
   else if(kind==='brain_state')setChip(ev[2],ev[3]);
   else if(kind==='busy'){busy=ev[2];$('send').disabled=busy;}}
 }catch(e){}
}
$('send').onclick=async()=>{const t=$('inp').value.trim();if(!t||busy)return;$('inp').value='';
 const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json','X-Token':token},
  body:JSON.stringify({msg:t,route:$('route').value})});
 if(r.status===401)alert('令牌错误');else if(r.status===409)alert('大脑正忙，请稍候');
};
$('inp').addEventListener('keydown',e=>{if(e.key==='Enter')$('send').click();});
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));t.classList.add('on');
 ['chat','log','file'].forEach(v=>$('view_'+v).style.display=(v===t.dataset.t?'':'none'));});
setInterval(poll,1500);poll();
</script></body></html>
"""


def make_web_handler(app, token):
    class WebHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # 静默访问日志

        def _token_ok(self, qs):
            if self.headers.get("X-Token") == token:
                return True
            return qs.get("token", [""])[0] == token

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path == "/":
                body = PAGE_HTML.encode("utf-8")
                self._reply(200, body, "text/html; charset=utf-8")
            elif parsed.path == "/api/events":
                if not self._token_ok(qs):
                    self._reply(401, b'{"err":"token"}', "application/json")
                    return
                since = int(qs.get("since", ["0"])[0] or 0)
                events = [[i, *it] for i, it in app.event_log if i > since]
                payload = {"events": events,
                           "busy": getattr(app, "web_busy", False)}
                self._reply(200, json.dumps(payload, ensure_ascii=False)
                            .encode("utf-8"), "application/json")
            elif parsed.path.startswith("/shot/"):
                # 手机端 <img> 无法带 X-Token 头，走 ?token= 查询参数鉴权
                if not self._token_ok(qs):
                    self._reply(401, b'{"err":"token"}', "application/json")
                    return
                name = os.path.basename(parsed.path[len("/shot/"):])
                fp = os.path.realpath(os.path.join(SCREENSHOT_DIR, name))
                if not fp.startswith(os.path.realpath(SCREENSHOT_DIR) + os.sep) \
                        or not name.lower().endswith((".png", ".jpg", ".jpeg")) \
                        or not os.path.isfile(fp):
                    self._reply(404, b"not found", "text/plain")
                    return
                try:
                    with open(fp, "rb") as f:
                        data = f.read()
                    ctype = "image/jpeg" if name.lower().endswith(
                        (".jpg", ".jpeg")) else "image/png"
                    self._reply(200, data, ctype)
                except OSError:
                    self._reply(404, b"not found", "text/plain")
            else:
                self._reply(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/api/send":
                self._reply(404, b"not found", "text/plain")
                return
            if not self._token_ok({}):
                self._reply(401, b'{"err":"token"}', "application/json")
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                self._reply(400, b"bad request", "text/plain")
                return
            msg = str(data.get("msg", "")).strip()
            route = data.get("route", "自动")
            if route not in ("自动", "直连", "小龙虾", "爱马仕", "双脑会商"):
                route = "自动"
            if not msg:
                self._reply(400, b"empty", "text/plain")
                return
            if getattr(app, "web_busy", False):
                self._reply(409, b"busy", "application/json")
                return
            app.ui_q.put(("web_send", msg, route))
            self._reply(202, b'{"ok":true}', "application/json")

        def _reply(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return WebHandler


# ---------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("双脑一体 · 小龙虾 + 爱马仕（一个身体两个大脑）")
        self.geometry("1180x760")
        self.minsize(960, 620)
        self.cfg = load_cfg()
        save_cfg(self.cfg)        # 持久化首次生成的 web_token 等
        self.web_busy = False
        self.ui_q = queue.Queue()
        self.event_log = []       # Web 控制台事件流（带自增 id）
        self.event_id = 0
        self.brain_states = {"claw": "idle", "hermes": "idle",
                             "api": "idle"}  # 高维视角状态机
        self.pulses = []          # 画布光脉冲粒子
        self.last_pulse = {}
        self._build_brains()
        self._build_style()
        self._build_ui()
        self.after(150, self._poll)
        self.after(400, self._anim)          # 高维视角动画循环
        self.after(600, self.self_check)
        threading.Thread(target=self._watch_workspace, daemon=True).start()
        threading.Thread(target=self._start_web, daemon=True).start()

    # ---- 事件总线：GUI 队列 + Web 事件流双写
    def emit(self, item):
        if item[0] == "busy":
            self.web_busy = item[1]   # 手机端防并发发送
        self.event_id += 1
        self.event_log.append((self.event_id, item))
        if len(self.event_log) > 600:
            del self.event_log[:100]
        self.ui_q.put_nowait(item)

    def _start_web(self):
        port = int(self.cfg.get("web_port") or 8765)
        token = self.cfg.get("web_token") or "1234"
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port),
                                      make_web_handler(self, token))
            srv.daemon_threads = True
            srv.serve_forever()
        except Exception as e:
            self.emit(("log", "sys", f"Web 控制台启动失败: {e}"))

    # ---- 大脑
    def _build_brains(self):
        BRAINS["claw"] = BrainClaw(self.cfg)
        BRAINS["hermes"] = BrainHermes(self.cfg)
        BRAINS["api"] = BrainAPI(self.cfg)

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        self.option_add("*Font", ("Microsoft YaHei UI", 10))

    def _build_ui(self):
        # 顶部状态
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        self.lbl_claw = self._status_chip(top, "🦞 小龙虾")
        self.lbl_hermes = self._status_chip(top, "☤ 爱马仕")
        self.lbl_api = self._status_chip(top, "⚡ 直连大脑")
        ttk.Label(top, text="🫀 身体：本机执行器 就绪").pack(side="left", padx=10)
        ttk.Button(top, text="重新自检", command=self.self_check).pack(side="right")
        ttk.Button(top, text="🌐 手机控制", command=self._show_web_info).pack(side="right", padx=6)
        ttk.Button(top, text="📸 截图发手机", command=self._do_screenshot).pack(side="right")
        ttk.Button(top, text="⚙ 设置 API", command=self._settings).pack(side="right")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)

        # 左：动作流 + 高维视角（两个标签页）
        left = ttk.Frame(body)
        body.add(left, weight=1)
        nb = ttk.Notebook(left)
        nb.pack(fill="both", expand=True)

        # Tab1 原有动作流
        tab_log = ttk.Frame(nb)
        nb.add(tab_log, text=" 动作流 ")
        pane = ttk.PanedWindow(tab_log, orient="vertical")
        pane.pack(fill="both", expand=True)
        self.txt_claw = self._log_pane(pane, "小龙虾 OpenClaw", "#0a4d2e")
        self.txt_hermes = self._log_pane(pane, "爱马仕 Hermes", "#4a1a5e")
        self.txt_api = self._log_pane(pane, "⚡ 直连大脑（快速通道）", "#0a5a8a")

        # Tab2 高维视角：像从上空俯瞰双脑指挥身体干活
        tab_hi = ttk.Frame(nb)
        nb.add(tab_hi, text=" 🛰 高维视角 ")
        self.canvas = tk.Canvas(tab_hi, bg="#0d1117", height=430,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_canvas())
        self._draw_canvas()
        flbl = ttk.LabelFrame(tab_hi, text=" 身体共享工作区实时监听（brain_workspace） ")
        flbl.pack(fill="both")
        self.txt_files = scrolledtext.ScrolledText(
            flbl, wrap="word", height=8, bg="#101418", fg="#9fe8b8",
            insertbackground="#ffffff", font=("Consolas", 9))
        self.txt_files.pack(fill="both", expand=True)
        self.txt_files.insert("end", "（等待大脑在工作区创建/修改文件…）\n")

        # 右：沟通界面
        right = ttk.Frame(body)
        body.add(right, weight=1)
        ttk.Label(right, text="沟通界面", padding=4).pack(anchor="w")
        self.chat = scrolledtext.ScrolledText(right, wrap="word", state="disabled",
                                              font=("Microsoft YaHei UI", 10))
        self.chat.pack(fill="both", expand=True, padx=6)
        self.chat.tag_config("user", foreground="#003b8e")
        self.chat.tag_config("claw", foreground="#0a6e3a")
        self.chat.tag_config("hermes", foreground="#6a2a8e")
        self.chat.tag_config("api", foreground="#0a7ea4")
        self.chat.tag_config("local", foreground="#8a5a00")
        self.chat.tag_config("sys", foreground="#777777")

        route = ttk.Frame(right, padding=4)
        route.pack(fill="x")
        ttk.Label(route, text="路由：").pack(side="left")
        self.route_var = tk.StringVar(value="自动")
        for v in ("自动", "直连", "小龙虾", "爱马仕", "双脑会商"):
            ttk.Radiobutton(route, text=v, value=v, variable=self.route_var).pack(side="left")
        inp = ttk.Frame(right, padding=4)
        inp.pack(fill="x")
        self.inp = ttk.Entry(inp)
        self.inp.pack(side="left", fill="x", expand=True)
        self.inp.bind("<Return>", lambda e: self.send())
        self.btn_send = ttk.Button(inp, text="发送", command=self.send)
        self.btn_send.pack(side="left", padx=4)

        # 底部：体检
        bottom = ttk.Frame(self, padding=6)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="🧪 一键体检双脑能力", command=self.run_exam).pack(side="left")
        self.lbl_exam = ttk.Label(bottom, text="体检可对比两个大脑的响应速度与可用性，生成优化路由建议")
        self.lbl_exam.pack(side="left", padx=10)

        self._chat_append("sys", "提示：两个大脑支持任意 OpenAI 兼容接口（GLM/Kimi/OpenRouter 等），"
                                 "点右上角「⚙ 设置 API」填入即可。未配置时由本地小脑驱动身体执行系统类指令。"
                                 "切到左侧「🛰 高维视角」可俯瞰双脑指挥身体干活的实时画面；"
                                 "大脑创建/修改的文件都会出现在共享工作区 brain_workspace 里。")

    def _status_chip(self, parent, text):
        lbl = ttk.Label(parent, text=f"{text}：检测中…", padding=4, relief="groove")
        lbl.pack(side="left", padx=(0, 8))
        return lbl

    def _log_pane(self, parent, title, color):
        frm = ttk.LabelFrame(parent, text=title)
        txt = scrolledtext.ScrolledText(frm, wrap="word", height=10,
                                        bg="#101418", fg="#d8e0e8",
                                        insertbackground="#ffffff",
                                        font=("Consolas", 9))
        txt.tag_config("act", foreground=color)
        txt.pack(fill="both", expand=True)
        parent.add(frm, weight=1)
        return txt

    # ---- 高维视角画布 -----------------------------------------------
    def _brain_xy(self, name):
        """大脑节点圆心坐标（随窗口自适应，三节点横排）。"""
        w = max(self.canvas.winfo_width(), 640)
        y = 95
        return {"claw": (w * 0.2, y), "api": (w * 0.5, y),
                "hermes": (w * 0.8, y)}[name]

    def _body_xy(self):
        w = max(self.canvas.winfo_width(), 640)
        h = max(self.canvas.winfo_height() - 24, 380)
        return (w * 0.5, h - 70)

    _STATE_COLOR = {"idle": "#555555", "working": "#22c55e",
                    "done": "#38bdf8", "error": "#ef4444"}
    _STATE_TEXT = {"idle": "待机", "working": "思考中…",
                   "done": "✅ 已完成", "error": "❌ 失败"}

    def _draw_canvas(self):
        c = self.canvas
        c.delete("all")
        c.configure(bg="#0d1117")
        c.create_text(14, 12, anchor="nw", text="🛰 高维视角 · 双脑指挥身体作业",
                      fill="#8b949e", font=("Microsoft YaHei UI", 9))
        bx, by = self._body_xy()
        for name, title, tag in (("claw", "小龙虾 OpenClaw", "🦞"),
                                 ("api", "直连大脑", "⚡"),
                                 ("hermes", "爱马仕 Hermes", "☤")):
            x, y = self._brain_xy(name)
            st = self.brain_states.get(name, "idle")
            col = self._STATE_COLOR.get(st, "#555555")
            # 连线（大脑 -> 身体）
            c.create_line(x, y, bx, by, fill="#2a3138", width=2,
                          tags=f"line_{name}")
            # 节点光圈 + 本体
            c.create_oval(x - 30, y - 30, x + 30, y + 30, outline=col,
                          width=3, tags=f"ring_{name}")
            c.create_oval(x - 26, y - 26, x + 26, y + 26, fill="#161b22",
                          outline="#3a4149", width=2)
            c.create_text(x, y - 6, text=tag, font=("Segoe UI Emoji", 14),
                          fill="#e6edf3")
            c.create_text(x, y + 15, text=title.split()[0],
                          font=("Microsoft YaHei UI", 10), fill="#e6edf3")
            c.create_text(x, y + 40, text=self._STATE_TEXT.get(st, "待机"),
                          font=("Microsoft YaHei UI", 9), fill=col,
                          tags=f"stat_{name}")
        # 身体节点
        c.create_oval(bx - 46, by - 34, bx + 46, by + 34, fill="#161b22",
                      outline="#b45309", width=3)
        c.create_text(bx, by - 6, text="🫀", font=("Segoe UI Emoji", 18),
                      fill="#e6edf3")
        c.create_text(bx, by + 18, text="身体 · 共享工作区",
                      font=("Microsoft YaHei UI", 10), fill="#e6edf3")
        c.create_text(bx, by + 52, text=WORKSPACE, font=("Consolas", 8),
                      fill="#6e7681")
        # 已有光脉冲重建
        for p in list(self.pulses):
            c.delete(p["id"])
        self.pulses.clear()

    def _spawn_pulse(self, name):
        """沿连线生成一颗光脉冲：0=大脑→身体，1=身体→大脑。"""
        x1, y1 = self._brain_xy(name)
        bx, by = self._body_xy()
        direction = 0 if len(self.pulses) % 2 == 0 else 1
        col = "#22c55e"
        rid = self.canvas.create_oval(-4, -4, 4, 4, fill=col, outline=col)
        self.pulses.append({"id": rid, "name": name, "t": 0.0,
                            "dir": direction, "col": col})

    def _anim(self):
        now = time.time()
        # 工作中的大脑每隔 0.7s 发射一颗脉冲
        for name, st in self.brain_states.items():
            if st == "working" and now - self.last_pulse.get(name, 0) > 0.7:
                self.last_pulse[name] = now
                self._spawn_pulse(name)
        # 推进/回收脉冲
        for p in list(self.pulses):
            p["t"] += 0.045
            x1, y1 = self._brain_xy(p["name"])
            bx, by = self._body_xy()
            if p["dir"] == 1:
                x1, y1, bx, by = bx, by, x1, y1
            t = min(p["t"], 1.0)
            x = x1 + (bx - x1) * t
            y = y1 + (by - y1) * t
            self.canvas.coords(p["id"], x - 4, y - 4, x + 4, y + 4)
            if p["t"] >= 1.0:
                self.canvas.delete(p["id"])
                self.pulses.remove(p)
        self.after(50, self._anim)

    def _set_brain_state(self, name, state):
        self.brain_states[name] = state
        col = self._STATE_COLOR.get(state, "#555555")
        try:
            self.canvas.itemconfig(f"ring_{name}", outline=col)
            self.canvas.itemconfig(f"stat_{name}",
                                   text=self._STATE_TEXT.get(state, "待机"),
                                   fill=col)
        except Exception:
            pass

    # ---- 身体共享工作区文件监听 --------------------------------------
    def _watch_workspace(self):
        snap = {}
        first = True
        while True:
            try:
                found = {}
                for root, dirs, files in os.walk(WORKSPACE):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for fn in files:
                        fp = os.path.join(root, fn)
                        try:
                            st = os.stat(fp)
                            found[fp] = (st.st_mtime, st.st_size)
                        except OSError:
                            pass
                if not first:
                    for fp, (mt, sz) in found.items():
                        if fp not in snap or snap[fp] != (mt, sz):
                            kind = "🆕 新增" if fp not in snap else "✏️ 修改"
                            rel = os.path.relpath(fp, WORKSPACE)
                            tm = time.strftime("%H:%M:%S", time.localtime(mt))
                            size = f"{sz/1024:.1f}KB" if sz < 2**20 else f"{sz/2**20:.1f}MB"
                            self.emit(("file", f"{kind} · {rel} · {tm} · {size}"))
                snap = found
                first = False
            except Exception:
                pass
            time.sleep(3)

    # ---- 工具
    def _chat_append(self, tag, text):
        self.emit(("chat", tag, text))

    def _brain_log(self, brain_name, line):
        self.emit(("log", brain_name, line))
        # 识别到工具/文件动作 → 推送高维视角事件
        low = line.lower()
        if any(k in low for k in ("tool", "shell", "edit_file", "write", "read",
                                  "执行", "创建", "写入", "读取")):
            self.emit(("file", f"⚡ {brain_name} 动作: {line[:80]}"))

    def _poll(self):
        while True:
            try:
                item = self.ui_q.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "log":
                _, who, line = item
                txt = {"claw": self.txt_claw, "hermes": self.txt_hermes,
                       "api": self.txt_api}.get(who)
                if txt is None:
                    continue
                txt.insert("end", line + "\n")
                txt.see("end")
            elif kind == "brain_state":
                _, name, state = item
                self._set_brain_state(name, state)
            elif kind == "file":
                _, text = item
                self.txt_files.insert("end", text + "\n")
                self.txt_files.see("end")
            elif kind == "chat":
                _, tag, text = item
                self.chat.configure(state="normal")
                self.chat.insert("end", f"【{tag}】{text}\n", tag)
                self.chat.see("end")
                self.chat.configure(state="disabled")
            elif kind == "status":
                _, name, ok, note = item
                titles = {"claw": "🦞 小龙虾", "hermes": "☤ 爱马仕",
                          "api": "⚡ 直连大脑"}
                lbls = {"claw": self.lbl_claw, "hermes": self.lbl_hermes,
                        "api": self.lbl_api}
                title = titles.get(name, name)
                state = "✅ 可用" if ok else "❌ 不可用"
                lbls[name].configure(text=f"{title}：{state} {note}")
            elif kind == "exam":
                _, done, total = item
                self.lbl_exam.configure(text=f"体检进行中… {done}/{total}")
            elif kind == "busy":
                _, flag = item
                self.btn_send.configure(state="normal" if not flag else "disabled")
            elif kind == "web_send":
                _, msg, route = item
                self.after(0, lambda m=msg, r=route: self._process(m, r))
        self.after(150, self._poll)

    # ---- 自检
    def self_check(self):
        for name, b in BRAINS.items():
            def probe(name=name, b=b):
                ok = b.available()
                note = ""
                if ok:
                    c = self._version_cmd(name)
                    if c:
                        ok2, out = _run_capture(c, timeout=30)
                        note = out.splitlines()[0][:40] if ok2 else "（版本未知）"
                        ok = ok2 or ok
                self.emit(("status", name, ok, note))
            threading.Thread(target=probe, daemon=True).start()

    def _version_cmd(self, name):
        if name == "claw":
            return [self.cfg["openclaw_cmd"], "--version"]
        if name == "hermes":
            return [self.cfg["hermes_cmd"], "--version"]
        return None  # 直连大脑无 CLI

    # ---- 截图推送到手机
    def _do_screenshot(self):
        """截取电脑屏幕并推送到手机控制台：聊天区直接显示图片，点开可放大/保存。"""
        self.emit(("busy", True))

        def work():
            try:
                fn, _fp = take_screenshot()
                cap = "📸 电脑屏幕截图 · " + time.strftime("%Y-%m-%d %H:%M:%S")
                self.emit(("shot", "/shot/" + fn,
                           cap + "（点图片可放大/保存）"))
            except Exception as e:
                self.emit(("chat", "sys", f"截图失败：{e}"))
            finally:
                self.emit(("busy", False))

        threading.Thread(target=work, daemon=True).start()

    # ---- 设置
    def _show_web_info(self):
        win = tk.Toplevel(self)
        win.title("🌐 手机控制台")
        win.geometry("560x260")
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        port = self.cfg.get("web_port", 8765)
        token = self.cfg.get("web_token", "")
        urls = ["http://localhost:%d/?t=%s" % (port, token)]
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("223.5.5.5", 80))
            ips = {s.getsockname()[0]}
            s.close()
            ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
            for ip in sorted(ips):
                if not ip.startswith("127."):
                    urls.append("http://%s:%d/?t=%s" % (ip, port, token))
        except Exception:
            pass
        ttk.Label(frm, text="手机连接同一 WiFi 后，用浏览器打开任一地址：",
                  wraplength=520).pack(anchor="w")
        box = scrolledtext.ScrolledText(frm, height=6, font=("Consolas", 10),
                                        wrap="word")
        box.pack(fill="both", expand=True, pady=8)
        box.insert("end", "\n".join(urls) + "\n")
        ttk.Label(frm, text="首次打开会带令牌自动登录；以后访问如提示输入令牌，填：%s"
                  % token, foreground="#b45309", wraplength=520).pack(anchor="w")

    def _settings(self):
        win = tk.Toplevel(self)
        win.title("设置 API（OpenAI 兼容）")
        win.geometry("520x240")
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        rows = [("接口地址 base_url（如 https://open.bigmodel.cn/api/paas/v4）", "api_base"),
                ("API Key", "api_key"),
                ("模型名（如 glm-4.7-flash）", "api_model")]
        vars_ = {}
        for i, (label, key) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=self.cfg.get(key, ""))
            vars_[key] = v
            ttk.Entry(frm, textvariable=v, width=48).grid(row=i, column=1, pady=3)

        def do_save():
            for k, v in vars_.items():
                self.cfg[k] = v.get().strip()
            save_cfg(self.cfg)
            for b in BRAINS.values():
                b.cfg = self.cfg
            self._chat_append("sys", "已保存 API 设置。")
            win.destroy()
        ttk.Button(frm, text="保存", command=do_save).grid(row=3, column=1, sticky="e", pady=8)

    # ---- 发送
    def send(self):
        msg = self.inp.get().strip()
        if not msg:
            return
        self.inp.delete(0, "end")
        self._process(msg, self.route_var.get())

    def _process(self, msg, route):
        """消息处理核心：GUI 输入与手机 Web 控制台共用。"""
        self._chat_append("user", "我：" + msg)
        self.emit(("busy", True))     # 通知所有端（GUI/Web）锁住发送按钮

        # 「截图/截屏」：身体直接截屏推送到手机控制台，不走大脑
        if "截图" in msg or "截屏" in msg or "screenshot" in msg.lower():
            self._do_screenshot()
            return

        def done(_b, _ok, _dt):
            self.emit(("chat", "sys", "—"))
            self.emit(("busy", False))

        if route in ("自动",) :
            reply, actor = local_cortex(msg)
            if reply:
                self._chat_append("local", f"本地小脑（{actor}）：{reply}")
                self.emit(("busy", False))
                return
            if BRAINS["api"].available():
                targets = [BRAINS["api"]]      # 自动模式：优先最快的直连大脑
            else:
                targets = [b for b in (BRAINS["claw"], BRAINS["hermes"])
                           if b.available() and b.key_ready()]
                if not targets:
                    self._chat_append("sys", "三个大脑都不可用：直连大脑需配置 API "
                                            "（点右上角「⚙ 设置 API」），本地小脑也无法回答该问题。")
                    self.emit(("busy", False))
                    return
        elif route == "直连":
            targets = [BRAINS["api"]]
        elif route == "小龙虾":
            targets = [BRAINS["claw"]]
        elif route == "爱马仕":
            targets = [BRAINS["hermes"]]
        else:
            targets = [BRAINS["claw"], BRAINS["hermes"]]  # 双脑会商

        self._dispatch(targets, msg, consult=(route == "双脑会商"))

    def _dispatch(self, targets, msg, consult=False):
        remain = len(targets)
        answers = {}  # name -> (title, ok, text)

        def one_done(b, ok, dt, text=""):
            nonlocal remain
            remain -= 1
            answers[b.name] = (b.title, ok, text or "")
            self.emit(("brain_state", b.name, "done" if ok else "error"))
            head = "✅" if ok else "❌"
            self.emit(("chat", b.name, f"{head} {b.title}（{dt}）见左侧动作流"))
            if remain <= 0:
                if consult:
                    self._consult_round2(msg, answers)
                else:
                    self.emit(("chat", "sys", "—"))
                    self.emit(("busy", False))

        for b in targets:
            self._chat_append(b.name, f"【{b.title}】思考中…")
            self.emit(("brain_state", b.name, "working"))
            b.ask_thread(
                msg,
                on_line=lambda line, b=b: self._brain_log(b.name, line),
                on_done=lambda b, ok, dt, out, cb=one_done: cb(b, ok, dt, out),
            )

    def _consult_round2(self, msg, answers):
        """会商第二轮：由主脑综合两位大脑的回答（主脑优先最快的直连大脑）。"""
        chair = BRAINS["api"] if BRAINS["api"].available() else (
            BRAINS["hermes"] if BRAINS["hermes"].key_ready() else BRAINS["claw"])
        if not chair.available():
            self.emit(("chat", "sys", "—"))
            self.emit(("busy", False))
            return
        parts = []
        for name in ("claw", "hermes"):
            title, ok, text = answers.get(name, (BRAINS[name].title, False, ""))
            parts.append(f"【{title}的回答（{'成功' if ok else '失败'}）】\n{text[:2000] or '（无输出）'}")
        other = "小龙虾" if chair.name == "hermes" else "爱马仕"
        prompt = (f"请综合另一位助手（{other}）和你的回答，给出针对该问题的最终统一答案，"
                  f"要点清晰、去重互补，不超过300字。\n"
                  f"问题：{msg}\n\n" + "\n\n".join(parts))
        self.emit(("chat", "sys", f"⚖ 会商第二轮：由 {chair.title} 综合两家答案…"))

        def run():
            self.emit(("brain_state", chair.name, "working"))
            ok, out = chair.ask_sync(prompt, on_line=lambda ln: self._brain_log(chair.name, ln))
            self.emit(("brain_state", chair.name, "done" if ok else "error"))
            head = "✅" if ok else "❌"
            self.emit(("chat", chair.name,
                           f"{head} 会商结论{'已生成' if ok else '失败'}，见左侧动作流末尾"))
            if ok and out.strip():
                self.emit(("chat", chair.name, "⚖ 最终统一答案：" + out.strip()[:800]))
            self.emit(("chat", "sys", "—"))
            self.emit(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    # ---- 体检
    EXAM_TASKS = {
        "自报版本": "--version",
        "最小问答": None,  # 用 -p 提问
    }

    def run_exam(self):
        tasks = ["查看进程并告诉我内存占用最高的前3个（身体类任务）",
                 "用一句话介绍你自己是什么助手"]
        results = {}
        total = len(BRAINS) * 2
        done = [0]

        def worker(name, b):
            c = self._version_cmd(name)
            cli_ok = False
            if c:
                cli_ok, _ = _run_capture(c, timeout=30)
            else:
                cli_ok = b.available()   # 直连大脑无 CLI
            done[0] += 1
            self.emit(("exam", done[0], total))
            if not b.key_ready():
                results[name] = {"cli": cli_ok, "answer": False, "sec": 0.0,
                                 "note": "未配置Key"}
                done[0] += 1
                self.emit(("exam", done[0], total))
                if len(results) == len(BRAINS):
                    self._exam_report(results)
                return
            t0 = time.time()
            self.emit(("brain_state", name, "working"))
            ok2, out2 = b.ask_sync(tasks[1], lambda _l: None)
            self.emit(("brain_state", name, "done" if ok2 else "error"))
            dt = time.time() - t0
            done[0] += 1
            self.emit(("exam", done[0], total))
            results[name] = {"cli": cli_ok, "answer": ok2 and bool(out2.strip()),
                             "sec": round(dt, 1)}
            if len(results) == len(BRAINS):
                self._exam_report(results)

        for name, b in BRAINS.items():
            threading.Thread(target=worker, args=(name, b), daemon=True).start()

    def _exam_report(self, res):
        lines = ["", "═══ 双脑能力体检报告 ═══"]
        for name, r in res.items():
            title = BRAINS[name].title
            note = r.get("note", "")
            ans = "正常" if r["answer"] else (note or "异常/未配置API")
            lines.append(f"{title}：CLI={'可用' if r['cli'] else '不可用'}，"
                         f"回答={ans}，耗时={r['sec']}s")
        claw, hermes = res.get("claw", {}), res.get("hermes", {})
        sug = ["优化建议："]
        if claw.get("cli") and not hermes.get("cli"):
            sug.append("  · 只有小龙虾可用 → 系统决策类任务全部路由小龙虾")
        elif hermes.get("cli") and not claw.get("cli"):
            sug.append("  · 只有爱马仕可用 → 长任务/自进化技能路由爱马仕")
        elif claw.get("cli") and hermes.get("cli"):
            fast = "小龙虾" if claw.get("sec", 99) <= hermes.get("sec", 99) else "爱马仕"
            sug.append(f"  · 双脑均可用，快速问答优先 {fast}；"
                       "复杂长任务走爱马仕（自进化技能），日常闲聊/浏览器走小龙虾")
        else:
            sug.append("  · 双脑都未就绪：请先完成安装并配置 API Key")
        sug.append("  · 系统级即时操作（进程/服务/清理）保持由身体+本地小脑直接执行，零延迟")
        lines += sug
        text = "\n".join(lines)
        self.emit(("chat", "sys", text))


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
