"""卸载助手 - PySide6 界面。"""
from __future__ import annotations

import os
import time
from datetime import datetime

from PySide6.QtCore import QFileInfo, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor

try:  # QFileIconProvider 在不同 PySide6 版本里位于不同模块
    from PySide6.QtWidgets import QFileIconProvider
except ImportError:
    from PySide6.QtGui import QFileIconProvider
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import core

DARK_QSS = """
QMainWindow, QDialog { background: #1e2229; }
QWidget { color: #d8dde6; font-size: 13px; }
QLineEdit, QTableWidget, QListWidget {
    background: #262b33; border: 1px solid #333a45; border-radius: 6px; padding: 4px;
}
QTableWidget::item, QListWidget::item { padding: 4px; }
QTableWidget::item:selected, QListWidget::item:selected { background: #3b6ea5; }
QHeaderView::section { background: #2b313b; border: none; padding: 6px; }
QPushButton {
    background: #3b6ea5; border: none; border-radius: 6px; padding: 7px 14px; color: #fff;
}
QPushButton:hover { background: #4a7fba; }
QPushButton:disabled { background: #39404c; color: #7c8494; }
QPushButton#navBtn { background: transparent; text-align: left; padding: 10px 16px; }
QPushButton#navBtn:hover { background: #2b313b; }
QPushButton#navBtn:checked { background: #3b6ea5; }
QPushButton#dangerBtn { background: #a54b3b; }
QPushButton#dangerBtn:hover { background: #ba5c4a; }
QLabel#title { font-size: 18px; font-weight: bold; }
QStatusBar { background: #181b20; }
"""


class UninstallWorker(QThread):
    """后台线程：逐个运行卸载程序并等待退出，避免卡死界面。"""

    progress = Signal(str)
    one_done = Signal(object, bool)
    all_done = Signal()

    def __init__(self, apps, parent=None):
        super().__init__(parent)
        self._apps = apps

    def run(self):
        for app in self._apps:
            self.progress.emit(f"正在卸载：{app.name}（请在弹出的卸载窗口中完成操作）")
            ok = core.launch_uninstall_and_wait(app)
            self.one_done.emit(app, ok)
        self.all_done.emit()


class _NumItem(QTableWidgetItem):
    """按数值排序的表格项：显示文本随意，排序用 UserRole 里的数字。"""

    def __lt__(self, other):
        return self.data(Qt.UserRole) < other.data(Qt.UserRole)


class SmartWatchdog(QThread):
    """智能省电后台线程：托管进程空闲降载，托管服务按需启停。

    每 5 秒检查一次进程；每 15 秒检查一次服务。
    """

    log = Signal(str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg          # 与主窗口共享同一个 dict
        self._stop = False
        self._last_proc_names = set(cfg.get("managed_processes", {}))

    def stop(self):
        self._stop = True

    def run(self):
        proc_state: dict[int, str] = {}     # pid -> "suspended"/"idle_priority"
        idle_since: dict[int, float] = {}   # pid -> 开始空闲的时间
        svc_idle: dict[str, float] = {}     # 服务名 -> 开始空闲的时间
        hinted: set[str] = set()
        last_svc = 0.0
        self.log.emit("自动优化已开启")
        while not self._stop:
            try:
                last_svc = self._tick(proc_state, idle_since, svc_idle, hinted, last_svc)
            except Exception as exc:  # 看门狗不允许因异常退出
                self.log.emit(f"自动优化异常：{exc}")
            for _ in range(50):  # 5 秒，可快速响应 stop
                if self._stop:
                    break
                time.sleep(0.1)
        # 退出前把托管进程全部恢复正常
        for pid, state in list(proc_state.items()):
            if state == "suspended":
                core.resume_process(pid)
            core.set_process_priority(pid, core.NORMAL_PRIORITY_CLASS)
        self.log.emit("自动优化已关闭，托管进程已恢复")

    # ------------------------------------------------------------ 内部逻辑

    def _tick(self, proc_state, idle_since, svc_idle, hinted, last_svc) -> float:
        now = time.monotonic()
        cfg = self._cfg
        idle_sec = max(10, int(cfg.get("idle_seconds", 60)))
        thr = float(cfg.get("cpu_threshold", 1.0))

        procs = core.list_processes()
        fg = core.get_foreground_pid()
        by_name: dict[str, list] = {}
        alive: set[int] = set()
        for p in procs:
            alive.add(p.pid)
            by_name.setdefault(p.name.lower(), []).append(p)

        managed = cfg.get("managed_processes", {})
        # 用户移除托管后，立即恢复该进程
        for removed in self._last_proc_names - set(managed):
            for p in by_name.get(removed.lower(), []):
                self._restore_proc(p.pid, proc_state, f"{removed}({p.pid})")
        self._last_proc_names = set(managed)

        for name, action in managed.items():
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
                    cur = proc_state.get(p.pid, "normal")
                    if want != cur:
                        fn = core.suspend_process if want == "suspended" else core.resume_process
                        if fn(p.pid):
                            proc_state[p.pid] = want
                            if want == "suspended":
                                self.log.emit(f"{name}({p.pid}) 空闲，已挂起省资源")
                            else:
                                self.log.emit(f"{name}({p.pid}) 恢复运行")
                else:
                    want = "idle_priority" if (idle and not active) else "normal"
                    cur = proc_state.get(p.pid, "normal")
                    if want != cur:
                        cls = (core.BELOW_NORMAL_PRIORITY_CLASS if want == "idle_priority"
                               else core.NORMAL_PRIORITY_CLASS)
                        if core.set_process_priority(p.pid, cls):
                            proc_state[p.pid] = want
                            self.log.emit(
                                f"{name}({p.pid}) 优先级已{'降低' if want == 'idle_priority' else '恢复'}")

        for pid in list(proc_state):
            if pid not in alive:
                proc_state.pop(pid, None)
                idle_since.pop(pid, None)

        if now - last_svc >= 15:
            last_svc = now
            self._svc_tick(svc_idle, hinted, idle_sec, now)
        return last_svc

    def _restore_proc(self, pid: int, proc_state: dict, label: str):
        state = proc_state.pop(pid, None)
        if state == "suspended":
            core.resume_process(pid)
            self.log.emit(f"{label} 已恢复运行（取消托管）")
        elif state == "idle_priority":
            core.set_process_priority(pid, core.NORMAL_PRIORITY_CLASS)
            self.log.emit(f"{label} 优先级已恢复（取消托管）")

    def _svc_tick(self, svc_idle: dict, hinted: set, idle_sec: int, now: float):
        cfg = self._cfg
        for name, info in dict(cfg.get("managed_services", {})).items():
            if not isinstance(info, dict):
                continue
            mode = info.get("mode", "always_off")
            watch = str(info.get("watch") or "").strip()
            st = core.service_query(name)
            if st is None:
                continue
            if mode == "ondemand" and watch:
                self._svc_ondemand(name, info, watch, st, svc_idle, idle_sec, now)
            else:
                if mode == "ondemand" and name not in hinted:
                    hinted.add(name)
                    self.log.emit(f"服务 {name} 未绑定进程，按“始终禁用”处理")
                if st.running or st.start != "disabled":
                    core.service_stop(name)
                    ok, err = core.service_set_start(name, "disabled")
                    if ok or not st.running:
                        self.log.emit(f"服务 {name} 已停止并禁用")
                    else:
                        self.log.emit(f"禁用服务 {name} 失败：{err}")

    def _svc_ondemand(self, name: str, info: dict, watch: str, st,
                      svc_idle: dict, idle_sec: int, now: float):
        if core.process_exists(watch):
            svc_idle.pop(name, None)
            if not st.running:
                orig = info.get("orig_start") or "auto"
                if st.start == "disabled":
                    ok, err = core.service_set_start(name, orig)
                    if not ok:
                        self.log.emit(f"恢复服务 {name} 启动类型失败：{err}")
                        return
                ok, err = core.service_start(name)
                if ok:
                    self.log.emit(f"检测到 {watch}，服务 {name} 已自动启动")
                else:
                    self.log.emit(f"启动服务 {name} 失败：{err}")
        else:
            if st.running or st.start != "disabled":
                since = svc_idle.setdefault(name, now)
                if now - since >= idle_sec:
                    if "orig_start" not in info and st.start != "disabled":
                        info["orig_start"] = st.start  # 记录原始启动类型以便恢复
                        core.save_manager_config(self._cfg)
                    ok, err = core.service_stop(name)
                    ok2, err2 = core.service_set_start(name, "disabled")
                    if ok and ok2:
                        self.log.emit(f"{watch} 未运行超 {idle_sec} 秒，服务 {name} 已停用")
                    else:
                        self.log.emit(f"停用服务 {name} 失败：{err or err2}")
                    svc_idle.pop(name, None)
            else:
                svc_idle.pop(name, None)


class LeftoverDialog(QDialog):
    """卸载完成后的残留清理对话框。"""

    def __init__(self, parent, app: core.InstalledApp):
        super().__init__(parent)
        self.setWindowTitle(f"残留清理 - {app.name}")
        self.resize(640, 420)

        self._app = app
        self._key_exists = core.uninstall_key_exists(app)
        candidates = core.scan_leftover_candidates(app.name, app.publisher, app.install_location)

        layout = QVBoxLayout(self)
        hint = QLabel("以下项目可能在卸载后残留，勾选需要删除的项目：")
        layout.addWidget(hint)

        self._list = QListWidget()
        if self._key_exists:
            item = QListWidgetItem("注册表卸载键（残留）")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, "registry")
            self._list.addItem(item)
        for path in candidates:
            item = QListWidgetItem(path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, path)
            self._list.addItem(item)
        layout.addWidget(self._list)

        if self._list.count() == 0:
            layout.addWidget(QLabel("未发现明显残留，该软件卸载得很干净。"))

        buttons = QHBoxLayout()
        buttons.addStretch()
        delete_btn = QPushButton("删除勾选项")
        delete_btn.setObjectName("dangerBtn")
        delete_btn.clicked.connect(self._delete_checked)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(delete_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _delete_checked(self):
        deleted, failed = 0, 0
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() != Qt.Checked:
                continue
            target = item.data(Qt.UserRole)
            if target == "registry":
                ok = core.delete_app_registry_key(self._app)
            else:
                ok = core.delete_path(target)
            if ok:
                deleted += 1
            else:
                failed += 1
        QMessageBox.information(
            self, "清理完成",
            f"已删除 {deleted} 项" + (f"，{failed} 项删除失败（可能需要管理员权限）" if failed else ""),
        )
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("卸载助手")
        self.resize(980, 680)
        self.setStyleSheet(DARK_QSS)
        self._worker = None
        self._icon_provider = QFileIconProvider(self)
        self._smart_cfg = core.load_manager_config()
        self._watchdog: SmartWatchdog | None = None
        self._services_loaded = False

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航
        sidebar = QFrame()
        sidebar.setFixedWidth(150)
        sidebar.setStyleSheet("QFrame { background: #181b20; border: none; }")
        nav_layout = QVBoxLayout(sidebar)
        nav_layout.setContentsMargins(8, 16, 8, 16)
        nav_layout.setSpacing(6)
        title = QLabel("卸载助手")
        title.setObjectName("title")
        nav_layout.addWidget(title)
        self._btn_apps = QPushButton("已安装软件")
        self._btn_startup = QPushButton("启动项管理")
        self._btn_process = QPushButton("进程管理")
        self._btn_service = QPushButton("服务管理")
        self._btn_smart = QPushButton("智能省电")
        self._nav_buttons = [self._btn_apps, self._btn_startup, self._btn_process,
                             self._btn_service, self._btn_smart]
        for btn in self._nav_buttons:
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            nav_layout.addWidget(btn)
        nav_layout.addStretch()
        root.addWidget(sidebar)

        # 右侧页面
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_apps_page())
        self._stack.addWidget(self._build_startup_page())
        self._stack.addWidget(self._build_process_page())
        self._stack.addWidget(self._build_service_page())
        self._stack.addWidget(self._build_smart_page())

        for i, btn in enumerate(self._nav_buttons):
            btn.clicked.connect(lambda _, idx=i: self._switch_page(idx))
        self._btn_apps.setChecked(True)

        self.setCentralWidget(central)
        self.statusBar().showMessage(
            "提示：卸载系统级软件（需要管理员权限）时，请右键以管理员身份运行本程序")

        self.refresh_apps()
        self.refresh_startup()

        # 进程页定时刷新（仅进程页可见时才真正刷新）
        self._proc_timer = QTimer(self)
        self._proc_timer.setInterval(2000)
        self._proc_timer.timeout.connect(self._on_proc_timer)
        self._proc_timer.start()

    # ------------------------------------------------------------ 页面构建

    def _build_apps_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索名称 / 发布者…")
        self._search.textChanged.connect(self._filter_apps)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_apps)
        top.addWidget(self._search, 1)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self._apps_table = QTableWidget(0, 6)
        self._apps_table.setHorizontalHeaderLabels(["", "名称", "版本", "发布者", "大小", "安装日期"])
        self._apps_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._apps_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._apps_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._apps_table.verticalHeader().setVisible(False)
        self._apps_table.setSortingEnabled(True)
        header = self._apps_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self._apps_table.setColumnWidth(0, 36)
        layout.addWidget(self._apps_table, 1)

        actions = QHBoxLayout()
        uninstall_btn = QPushButton("卸载（勾选或选中）")
        uninstall_btn.setObjectName("dangerBtn")
        uninstall_btn.clicked.connect(self.uninstall_selected)
        force_btn = QPushButton("强制卸载…")
        force_btn.setToolTip("软件自带卸载程序损坏/丢失时，直接删除注册表项与目录")
        force_btn.clicked.connect(self.force_uninstall_selected)
        open_dir_btn = QPushButton("打开安装目录")
        open_dir_btn.clicked.connect(self.open_install_dir)
        leftover_btn = QPushButton("残留扫描…")
        leftover_btn.clicked.connect(self.scan_leftovers_manual)
        actions.addWidget(uninstall_btn)
        actions.addWidget(force_btn)
        actions.addWidget(open_dir_btn)
        actions.addWidget(leftover_btn)
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def _build_startup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(QLabel("管理开机自动启动的程序："))
        top.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_startup)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self._startup_table = QTableWidget(0, 4)
        self._startup_table.setHorizontalHeaderLabels(["名称", "命令", "位置", "状态"])
        self._startup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._startup_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._startup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._startup_table.verticalHeader().setVisible(False)
        self._startup_table.setSortingEnabled(True)
        header = self._startup_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self._startup_table, 1)

        actions = QHBoxLayout()
        toggle_btn = QPushButton("启用 / 禁用")
        toggle_btn.clicked.connect(self.toggle_startup)
        delete_btn = QPushButton("删除启动项")
        delete_btn.setObjectName("dangerBtn")
        delete_btn.clicked.connect(self.delete_startup)
        actions.addWidget(toggle_btn)
        actions.addWidget(delete_btn)
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def _switch_page(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        if index == 2:
            self.refresh_process_table()
        elif index == 3 and not self._services_loaded:
            self.refresh_services()

    def _on_proc_timer(self):
        if self._stack.currentIndex() == 2:
            self.refresh_process_table()

    def closeEvent(self, event):
        # 退出前停止看门狗并恢复托管进程
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog.wait(8000)
            self._watchdog = None
        event.accept()

    # ------------------------------------------------------------ 已安装软件

    def refresh_apps(self):
        self._apps_table.setSortingEnabled(False)
        self._apps_table.setRowCount(0)
        self._apps = core.scan_installed_apps()
        for app in self._apps:
            row = self._apps_table.rowCount()
            self._apps_table.insertRow(row)

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Unchecked)
            self._apps_table.setItem(row, 0, check)

            name_item = QTableWidgetItem(app.name)
            name_item.setData(Qt.UserRole, app)
            icon_path = app.icon_path
            if icon_path and os.path.isfile(icon_path):
                try:
                    name_item.setIcon(self._icon_provider.icon(icon_path))
                except Exception:
                    pass
            self._apps_table.setItem(row, 1, name_item)

            self._apps_table.setItem(row, 2, QTableWidgetItem(app.version))
            self._apps_table.setItem(row, 3, QTableWidgetItem(app.publisher))
            self._apps_table.setItem(row, 4, QTableWidgetItem(core.format_size(app.estimated_size_kb)))
            self._apps_table.setItem(row, 5, QTableWidgetItem(core.format_install_date(app.install_date)))
        self._apps_table.setSortingEnabled(True)
        self._filter_apps(self._search.text())
        self.statusBar().showMessage(f"共找到 {len(self._apps)} 个已安装软件", 5000)

    def _filter_apps(self, text: str):
        text = text.strip().lower()
        for row in range(self._apps_table.rowCount()):
            name = self._apps_table.item(row, 1).text().lower()
            publisher = self._apps_table.item(row, 3).text().lower()
            self._apps_table.setRowHidden(row, bool(text) and text not in name and text not in publisher)

    def _collect_target_apps(self) -> list[core.InstalledApp]:
        """收集勾选的软件；没有勾选时用当前选中行。"""
        result: list[core.InstalledApp] = []
        seen = set()
        for row in range(self._apps_table.rowCount()):
            if self._apps_table.item(row, 0).checkState() == Qt.Checked:
                app = self._apps_table.item(row, 1).data(Qt.UserRole)
                if id(app) not in seen:
                    seen.add(id(app))
                    result.append(app)
        if result:
            return result
        for item in self._apps_table.selectedItems():
            app = self._apps_table.item(item.row(), 1).data(Qt.UserRole)
            if id(app) not in seen:
                seen.add(id(app))
                result.append(app)
        return result

    def uninstall_selected(self):
        if self._worker is not None:
            QMessageBox.warning(self, "提示", "已有卸载任务正在进行中")
            return
        targets = self._collect_target_apps()
        if not targets:
            QMessageBox.information(self, "提示", "请先勾选或选中要卸载的软件")
            return

        # 进程占用预检：检测 InstallLocation 下是否有运行中的进程
        busy_apps = []
        for app in targets:
            procs = core.list_running_procs_in_dir(app.install_location)
            if procs:
                busy_apps.append((app.name, procs))
        if busy_apps:
            lines = []
            for name, procs in busy_apps:
                exes = "\n      ".join(sorted(os.path.basename(p) for p in procs))
                lines.append(f"  · {name}：\n      {exes}")
            answer = QMessageBox.question(
                self, "检测到进程占用",
                "以下软件有进程正在运行，卸载可能失败或文件被占用删不掉：\n\n"
                + "\n".join(lines)
                + "\n\n仍要继续卸载吗？（建议先手动关闭这些程序）",
            )
            if answer != QMessageBox.Yes:
                return

        names = "\n".join(f"· {a.name}" for a in targets)
        answer = QMessageBox.question(
            self, "确认卸载",
            f"确定卸载以下 {len(targets)} 个软件吗？\n\n{names}\n\n"
            "将运行软件自带的卸载程序，请在弹出的窗口中完成操作。",
        )
        if answer != QMessageBox.Yes:
            return

        self._set_apps_busy(True)
        self._last_targets = targets
        self._worker = UninstallWorker(targets, self)
        self._worker.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self._worker.one_done.connect(self._on_one_uninstalled)
        self._worker.all_done.connect(self._on_uninstall_finished)
        self._worker.start()

    def force_uninstall_selected(self):
        """软件自带卸载程序损坏/丢失时的兜底：直接删注册表项+目录。"""
        targets = self._collect_target_apps()
        if len(targets) != 1:
            QMessageBox.information(self, "提示", "强制卸载请勾选或选中一个软件")
            return
        app = targets[0]

        # 进程占用预检
        procs = core.list_running_procs_in_dir(app.install_location)
        if procs:
            exes = "\n  ".join(sorted(os.path.basename(p) for p in procs))
            QMessageBox.warning(
                self, "进程占用", f"{app.name} 有进程正在运行：\n  {exes}\n\n"
                "请先关闭这些程序再强制卸载，否则目录可能删不干净。")
            return

        detail = []
        if app.install_location:
            detail.append(f"  · 安装目录：{app.install_location}")
        detail.append(f"  · 注册表项：{app.reg_path}")
        answer = QMessageBox.question(
            self, "强制卸载确认",
            f"将直接删除「{app.name}」的注册表项与目录，该操作不可恢复：\n\n"
            + "\n".join(detail) + "\n\n"
            "确认强制卸载吗？",
        )
        if answer != QMessageBox.Yes:
            return

        result = core.force_uninstall(app)
        deleted = len(result["deleted"])
        failed = result["failed"]
        msg = f"强制卸载完成：删除 {deleted} 项"
        if failed:
            msg += f"\n\n以下 {len(failed)} 项删除失败（可能需要管理员权限或文件占用）：\n  " \
                + "\n  ".join(failed)
        QMessageBox.information(self, "完成", msg)
        self.refresh_apps()

    def _on_one_uninstalled(self, app, ok: bool):
        if not ok:
            answer = QMessageBox.question(
                self, "卸载失败",
                f"无法启动卸载程序：{app.name}\n"
                "可能是卸载程序损坏或需要管理员权限。\n\n"
                "是否改用「强制卸载」直接删除该项？",
            )
            if answer == QMessageBox.Yes:
                result = core.force_uninstall(app)
                QMessageBox.information(
                    self, "强制卸载完成",
                    f"删除 {len(result['deleted'])} 项"
                    + (f"\n失败 {len(result['failed'])} 项" if result["failed"] else ""))
                self.refresh_apps()

    def _on_uninstall_finished(self):
        self._worker = None
        self._set_apps_busy(False)
        self.refresh_apps()
        self.statusBar().showMessage("卸载任务结束", 5000)
        # 单个卸载时直接弹出残留清理对话框
        if self._last_targets and len(self._last_targets) == 1:
            LeftoverDialog(self, self._last_targets[0]).exec()
        else:
            QMessageBox.information(self, "完成", "批量卸载完成，可逐个使用\"残留扫描\"进一步清理。")

    def _set_apps_busy(self, busy: bool):
        for btn in self.findChildren(QPushButton):
            if btn is not None and btn.objectName() != "navBtn":
                btn.setEnabled(not busy)

    def open_install_dir(self):
        targets = self._collect_target_apps()
        if len(targets) != 1:
            QMessageBox.information(self, "提示", "请勾选或选中一个软件")
            return
        app = targets[0]
        path = app.install_location
        if not path or not os.path.isdir(path):
            # 从卸载命令里猜目录
            parts = core.split_command(app.uninstall_string) if app.uninstall_string else []
            if parts and os.path.isfile(parts[0]):
                path = os.path.dirname(parts[0])
        if path and os.path.isdir(path):
            os.startfile(path)  # noqa: S606
        else:
            QMessageBox.warning(self, "未找到目录", "该软件没有记录安装目录。")

    def scan_leftovers_manual(self):
        targets = self._collect_target_apps()
        if len(targets) != 1:
            QMessageBox.information(self, "提示", "请勾选或选中一个软件")
            return
        LeftoverDialog(self, targets[0]).exec()

    # ------------------------------------------------------------ 启动项

    def refresh_startup(self):
        self._startup_table.setSortingEnabled(False)
        self._startup_table.setRowCount(0)
        self._startup_items = core.scan_startup_items()
        for item in self._startup_items:
            row = self._startup_table.rowCount()
            self._startup_table.insertRow(row)
            name_item = QTableWidgetItem(item.name)
            name_item.setData(Qt.UserRole, item)
            self._startup_table.setItem(row, 0, name_item)
            self._startup_table.setItem(row, 1, QTableWidgetItem(item.command))
            self._startup_table.setItem(row, 2, QTableWidgetItem(item.location))
            status = QTableWidgetItem("启用" if item.enabled else "禁用")
            status.setForeground(QColor("#4caf50") if item.enabled else QColor("#9aa3af"))
            self._startup_table.setItem(row, 3, status)
        self._startup_table.setSortingEnabled(True)
        self.statusBar().showMessage(f"共 {len(self._startup_items)} 个启动项", 5000)

    def _selected_startup_items(self) -> list[core.StartupItem]:
        result = []
        for item in self._startup_table.selectedItems():
            row_item = self._startup_table.item(item.row(), 0)
            startup = row_item.data(Qt.UserRole)
            if startup not in result:
                result.append(startup)
        return result

    def toggle_startup(self):
        items = self._selected_startup_items()
        if not items:
            QMessageBox.information(self, "提示", "请先选中启动项")
            return
        failed = []
        for item in items:
            if not core.set_startup_enabled(item, not item.enabled):
                failed.append(item.name)
        if failed:
            QMessageBox.warning(self, "失败", "以下项修改失败（系统级启动项需要管理员权限）：\n"
                                + "\n".join(f"· {n}" for n in failed))
        self.refresh_startup()

    def delete_startup(self):
        items = self._selected_startup_items()
        if not items:
            QMessageBox.information(self, "提示", "请先选中要删除的启动项")
            return
        names = "\n".join(f"· {i.name}" for i in items)
        answer = QMessageBox.question(self, "确认删除", f"确定删除以下 {len(items)} 个启动项吗？\n\n{names}")
        if answer != QMessageBox.Yes:
            return
        failed = [i.name for i in items if not core.delete_startup_item(i)]
        if failed:
            QMessageBox.warning(self, "失败", "以下项删除失败（可能需要管理员权限）：\n"
                                + "\n".join(f"· {n}" for n in failed))
        self.refresh_startup()

    # ------------------------------------------------------------ 进程管理

    def _build_process_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self._proc_search = QLineEdit()
        self._proc_search.setPlaceholderText("搜索进程名 / 路径…")
        self._proc_search.textChanged.connect(self._filter_process)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_process_table)
        top.addWidget(self._proc_search, 1)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self._proc_table = QTableWidget(0, 5)
        self._proc_table.setHorizontalHeaderLabels(["PID", "进程名", "CPU %", "内存", "路径"])
        self._proc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._proc_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._proc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._proc_table.verticalHeader().setVisible(False)
        self._proc_table.setSortingEnabled(True)
        header = self._proc_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self._proc_table.setColumnWidth(0, 70)
        self._proc_table.setColumnWidth(2, 70)
        self._proc_table.setColumnWidth(3, 90)
        layout.addWidget(self._proc_table, 1)

        actions = QHBoxLayout()
        kill_btn = QPushButton("结束进程")
        kill_btn.setObjectName("dangerBtn")
        kill_btn.clicked.connect(self.kill_selected)
        manage_btn = QPushButton("加入智能托管")
        manage_btn.setToolTip("在\"智能省电\"页自动挂起空闲或降低其优先级")
        manage_btn.clicked.connect(self.manage_selected_procs)
        actions.addWidget(kill_btn)
        actions.addWidget(manage_btn)
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def refresh_process_table(self):
        table = self._proc_table
        selected_pids = {table.item(it.row(), 0).data(Qt.UserRole)
                         for it in table.selectedItems()} if table.rowCount() else set()
        table.setSortingEnabled(False)
        table.setRowCount(0)
        procs = core.list_processes()
        for p in procs:
            row = table.rowCount()
            table.insertRow(row)

            pid_item = _NumItem(str(p.pid))
            pid_item.setData(Qt.UserRole, p.pid)
            table.setItem(row, 0, pid_item)

            name_item = QTableWidgetItem(p.name)
            name_item.setToolTip(p.exe_path)
            table.setItem(row, 1, name_item)

            cpu_item = _NumItem(f"{p.cpu_percent:.1f}")
            cpu_item.setData(Qt.UserRole, round(p.cpu_percent, 2))
            table.setItem(row, 2, cpu_item)

            mem_item = _NumItem(core.format_bytes(p.mem_bytes))
            mem_item.setData(Qt.UserRole, p.mem_bytes)
            table.setItem(row, 3, mem_item)

            table.setItem(row, 4, QTableWidgetItem(p.exe_path))

            if p.pid in selected_pids:
                table.selectRow(row)
        table.setSortingEnabled(True)
        self._filter_process(self._proc_search.text())

    def _filter_process(self, text: str):
        text = text.strip().lower()
        for row in range(self._proc_table.rowCount()):
            name = self._proc_table.item(row, 1).text().lower()
            path = self._proc_table.item(row, 4).text().lower()
            self._proc_table.setRowHidden(
                row, bool(text) and text not in name and text not in path)

    def _selected_process_rows(self) -> list[tuple[int, str]]:
        rows, seen = [], set()
        for item in self._proc_table.selectedItems():
            row = item.row()
            if row in seen:
                continue
            seen.add(row)
            pid = self._proc_table.item(row, 0).data(Qt.UserRole)
            name = self._proc_table.item(row, 1).text()
            rows.append((pid, name))
        return rows

    def kill_selected(self):
        rows = self._selected_process_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要结束的进程")
            return
        names = "\n".join(f"· {n}（PID {p}）" for p, n in rows)
        answer = QMessageBox.question(
            self, "确认结束进程",
            f"确定结束以下 {len(rows)} 个进程吗？（未保存的数据会丢失）\n\n{names}")
        if answer != QMessageBox.Yes:
            return
        fails = []
        for pid, name in rows:
            ok, err = core.kill_process(pid, name)
            if not ok:
                fails.append(f"· {name}（PID {pid}）：{err}")
        self.refresh_process_table()
        if fails:
            QMessageBox.warning(self, "结束失败（可能需要管理员权限）", "\n".join(fails))

    def manage_selected_procs(self):
        rows = self._selected_process_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要托管的进程")
            return
        added = 0
        for _, name in rows:
            if name in self._smart_cfg.setdefault("managed_processes", {}):
                continue
            self._smart_cfg["managed_processes"][name] = "suspend"
            self._add_proc_managed_row(name, "suspend")
            added += 1
        if added:
            core.save_manager_config(self._smart_cfg)
            self._smart_log(f"已加入托管 {added} 个进程（默认空闲挂起）")
        else:
            self.statusBar().showMessage("选中的进程已在托管列表中", 3000)

    # ------------------------------------------------------------ 服务管理

    def _build_service_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self._svc_search = QLineEdit()
        self._svc_search.setPlaceholderText("搜索服务名 / 显示名称…")
        self._svc_search.textChanged.connect(self._filter_services)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_services)
        top.addWidget(self._svc_search, 1)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self._svc_table = QTableWidget(0, 5)
        self._svc_table.setHorizontalHeaderLabels(["服务名", "显示名称", "状态", "启动类型", "二进制路径"])
        self._svc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._svc_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._svc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._svc_table.verticalHeader().setVisible(False)
        self._svc_table.setSortingEnabled(True)
        header = self._svc_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self._svc_table, 1)

        actions = QHBoxLayout()
        start_btn = QPushButton("启动")
        start_btn.clicked.connect(self.service_start_selected)
        stop_btn = QPushButton("停止")
        stop_btn.clicked.connect(self.service_stop_selected)
        actions.addWidget(start_btn)
        actions.addWidget(stop_btn)
        actions.addWidget(QLabel("启动类型："))
        self._svc_kind_combo = QComboBox()
        for text, kind in (("自动", "auto"), ("自动(延迟)", "auto_delayed"),
                           ("手动", "manual"), ("禁用", "disabled")):
            self._svc_kind_combo.addItem(text, kind)
        actions.addWidget(self._svc_kind_combo)
        apply_kind_btn = QPushButton("应用")
        apply_kind_btn.clicked.connect(self.service_apply_start_kind)
        actions.addWidget(apply_kind_btn)
        manage_btn = QPushButton("加入智能托管")
        manage_btn.setToolTip("在\"智能省电\"页自动停用不用的服务，绑定的程序运行时再启动")
        manage_btn.clicked.connect(self.manage_selected_services)
        actions.addWidget(manage_btn)
        actions.addStretch()
        layout.addLayout(actions)

        hint = QLabel("灰色行是系统关键服务，禁止停止/禁用，防止系统异常。")
        hint.setStyleSheet("color: #9aa3af;")
        layout.addWidget(hint)
        return page

    def refresh_services(self):
        table = self._svc_table
        selected = {table.item(it.row(), 0).data(Qt.UserRole)
                    for it in table.selectedItems()} if table.rowCount() else set()
        table.setSortingEnabled(False)
        table.setRowCount(0)
        self._services = core.list_services()
        for info in self._services:
            row = table.rowCount()
            table.insertRow(row)
            name_item = QTableWidgetItem(info.name)
            name_item.setData(Qt.UserRole, info.name)
            if info.protected:
                name_item.setForeground(QColor("#7c8494"))
                name_item.setToolTip("系统关键服务，禁止操作")
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(info.display))
            state_item = QTableWidgetItem(core.STATE_LABELS.get(info.state, str(info.state)))
            state_item.setForeground(QColor("#4caf50") if info.state == 4 else QColor("#d8dde6"))
            table.setItem(row, 2, state_item)
            table.setItem(row, 3, QTableWidgetItem(core.START_LABELS.get(info.start, info.start)))
            table.setItem(row, 4, QTableWidgetItem(info.binary))
            if info.name in selected:
                table.selectRow(row)
        table.setSortingEnabled(True)
        self._services_loaded = True
        self._filter_services(self._svc_search.text())
        self.statusBar().showMessage(f"共 {len(self._services)} 个服务", 5000)

    def _filter_services(self, text: str):
        text = text.strip().lower()
        for row in range(self._svc_table.rowCount()):
            name = self._svc_table.item(row, 0).text().lower()
            display = self._svc_table.item(row, 1).text().lower()
            self._svc_table.setRowHidden(
                row, bool(text) and text not in name and text not in display)

    def _selected_services(self) -> list[core.ServiceInfo]:
        result, seen = [], set()
        for item in self._svc_table.selectedItems():
            name = self._svc_table.item(item.row(), 0).data(Qt.UserRole)
            if name in seen:
                continue
            seen.add(name)
            for info in self._services:
                if info.name == name:
                    result.append(info)
                    break
        return result

    def _guard_service_targets(self) -> list[core.ServiceInfo] | None:
        targets = self._selected_services()
        if not targets:
            QMessageBox.information(self, "提示", "请先选中服务")
            return None
        protected = [t.name for t in targets if t.protected]
        if protected:
            QMessageBox.warning(
                self, "受保护的服务",
                "以下系统关键服务禁止操作，否则可能导致系统异常：\n"
                + "\n".join(f"· {n}" for n in protected))
            return None
        return targets

    def service_start_selected(self):
        targets = self._guard_service_targets()
        if not targets:
            return
        fails = []
        for info in targets:
            ok, err = core.service_start(info.name)
            if not ok:
                fails.append(f"· {info.name}：{err}")
        self.refresh_services()
        if fails:
            QMessageBox.warning(self, "启动失败", "\n".join(fails))

    def service_stop_selected(self):
        targets = self._guard_service_targets()
        if not targets:
            return
        names = "\n".join(f"· {t.name}" for t in targets)
        answer = QMessageBox.question(
            self, "确认停止",
            f"确定停止以下 {len(targets)} 个服务吗？依赖它们的功能会暂时失效。\n\n{names}")
        if answer != QMessageBox.Yes:
            return
        fails = []
        for info in targets:
            ok, err = core.service_stop(info.name)
            if not ok:
                fails.append(f"· {info.name}：{err}")
        self.refresh_services()
        if fails:
            QMessageBox.warning(self, "停止失败", "\n".join(fails))

    def service_apply_start_kind(self):
        targets = self._guard_service_targets()
        if not targets:
            return
        kind = self._svc_kind_combo.currentData()
        label = core.START_LABELS[kind]
        fails = []
        for info in targets:
            ok, err = core.service_set_start(info.name, kind)
            if not ok:
                fails.append(f"· {info.name}：{err}")
        self.refresh_services()
        if fails:
            QMessageBox.warning(self, "修改失败（需要管理员权限）", "\n".join(fails))
        else:
            self.statusBar().showMessage(f"已把 {len(targets)} 个服务设为「{label}」，重启后生效", 5000)

    def manage_selected_services(self):
        targets = self._selected_services()
        if not targets:
            QMessageBox.information(self, "提示", "请先选中服务")
            return
        added = 0
        for info in targets:
            if info.name in self._smart_cfg.setdefault("managed_services", {}):
                continue
            watch = ""
            mode = "always_off"
            exe = os.path.basename(info.binary.split(" -k")[0].strip().strip('"')) \
                if info.binary else ""
            if exe and exe.lower() != "svchost.exe":
                mode, watch = "ondemand", exe
            self._smart_cfg["managed_services"][info.name] = {"mode": mode, "watch": watch}
            self._add_svc_managed_row(info.name, mode, watch)
            added += 1
        if added:
            core.save_manager_config(self._smart_cfg)
            self._smart_log(f"已加入托管 {added} 个服务")
        else:
            self.statusBar().showMessage("选中的服务已在托管列表中", 3000)

    # ------------------------------------------------------------ 智能省电

    def _build_smart_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        settings = QGroupBox("自动优化")
        s_lay = QHBoxLayout(settings)
        self._smart_enable = QCheckBox("启用自动优化")
        self._smart_enable.toggled.connect(self._toggle_watchdog)
        s_lay.addWidget(self._smart_enable)
        s_lay.addWidget(QLabel("空闲判定："))
        self._idle_spin = QSpinBox()
        self._idle_spin.setRange(10, 600)
        self._idle_spin.setSuffix(" 秒")
        self._idle_spin.setValue(int(self._smart_cfg.get("idle_seconds", 60)))
        self._idle_spin.valueChanged.connect(self._save_idle_seconds)
        s_lay.addWidget(self._idle_spin)
        s_lay.addWidget(QLabel("CPU 阈值："))
        self._cpu_spin = QDoubleSpinBox()
        self._cpu_spin.setRange(0.5, 50.0)
        self._cpu_spin.setSingleStep(0.5)
        self._cpu_spin.setSuffix(" %")
        self._cpu_spin.setValue(float(self._smart_cfg.get("cpu_threshold", 1.0)))
        self._cpu_spin.valueChanged.connect(self._save_cpu_threshold)
        s_lay.addWidget(self._cpu_spin)
        s_lay.addStretch()
        layout.addWidget(settings)

        proc_group = QGroupBox("托管进程（空闲且不在前台时自动挂起/降优先级，回到前台自动恢复）")
        p_lay = QVBoxLayout(proc_group)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加进程名…")
        add_btn.clicked.connect(self._add_proc_by_name)
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        btn_row.addWidget(QLabel("提示：也可在\"进程管理\"页选中后点\"加入智能托管\""))
        btn_row.addStretch()
        p_lay.addLayout(btn_row)
        self._proc_managed_list = QListWidget()
        self._proc_managed_list.setMaximumHeight(120)
        p_lay.addWidget(self._proc_managed_list)
        layout.addWidget(proc_group)

        svc_group = QGroupBox("托管服务（不用的服务自动停止并禁用；绑定的程序运行时自动启动）")
        s2_lay = QVBoxLayout(svc_group)
        self._svc_managed_list = QListWidget()
        self._svc_managed_list.setMaximumHeight(140)
        s2_lay.addWidget(self._svc_managed_list)
        s2_hint = QLabel("始终禁用 = 立即停止并禁用；按需启动 = 绑定进程运行时才启动该服务。"
                         "提示：也可在\"服务管理\"页选中后点\"加入智能托管\"。")
        s2_hint.setStyleSheet("color: #9aa3af;")
        s2_hint.setWordWrap(True)
        s2_lay.addWidget(s2_hint)
        layout.addWidget(svc_group)

        layout.addWidget(QLabel("操作日志："))
        self._smart_log_list = QListWidget()
        layout.addWidget(self._smart_log_list, 1)

        self._rebuild_smart_lists()
        return page

    def _save_idle_seconds(self, value: int):
        self._smart_cfg["idle_seconds"] = int(value)
        core.save_manager_config(self._smart_cfg)

    def _save_cpu_threshold(self, value: float):
        self._smart_cfg["cpu_threshold"] = float(value)
        core.save_manager_config(self._smart_cfg)

    def _toggle_watchdog(self, on: bool):
        if on:
            self._watchdog = SmartWatchdog(self._smart_cfg, self)
            self._watchdog.log.connect(self._smart_log)
            self._watchdog.start()
            self.statusBar().showMessage("自动优化已开启", 3000)
        else:
            if self._watchdog is not None:
                self._watchdog.stop()
                self._watchdog.wait(8000)
                self._watchdog = None

    def _smart_log(self, msg: str):
        self._smart_log_list.insertItem(0, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        if self._smart_log_list.count() > 300:
            self._smart_log_list.takeItem(self._smart_log_list.count() - 1)

    def _rebuild_smart_lists(self):
        self._proc_managed_list.clear()
        for name, action in self._smart_cfg.get("managed_processes", {}).items():
            self._add_proc_managed_row(name, action)
        self._svc_managed_list.clear()
        for name, info in self._smart_cfg.get("managed_services", {}).items():
            if isinstance(info, dict):
                self._add_svc_managed_row(name, info.get("mode", "always_off"),
                                          str(info.get("watch") or ""))

    # ---- 托管进程列表行

    def _add_proc_managed_row(self, name: str, action: str):
        widget = QWidget()
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(QLabel(name))
        combo = QComboBox()
        combo.addItem("空闲挂起", "suspend")
        combo.addItem("降低优先级", "priority")
        combo.setCurrentIndex(0 if action == "suspend" else 1)
        combo.currentIndexChanged.connect(
            lambda _, n=name, c=combo: self._set_proc_action(n, c.currentData()))
        lay.addWidget(combo)
        lay.addStretch()
        remove_btn = QPushButton("移除")
        remove_btn.setFixedWidth(64)
        remove_btn.clicked.connect(lambda _, n=name: self._remove_proc_managed(n))
        lay.addWidget(remove_btn)
        item = QListWidgetItem()
        item.setData(Qt.UserRole, name)
        self._proc_managed_list.addItem(item)
        self._proc_managed_list.setItemWidget(item, widget)

    def _set_proc_action(self, name: str, action: str):
        if name in self._smart_cfg.setdefault("managed_processes", {}):
            self._smart_cfg["managed_processes"][name] = action
            core.save_manager_config(self._smart_cfg)

    def _remove_proc_managed(self, name: str):
        self._smart_cfg.get("managed_processes", {}).pop(name, None)
        core.save_manager_config(self._smart_cfg)
        for row in range(self._proc_managed_list.count()):
            if self._proc_managed_list.item(row).data(Qt.UserRole) == name:
                self._proc_managed_list.takeItem(row)
                break
        self._smart_log(f"已取消托管进程 {name}")

    def _add_proc_by_name(self):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "添加托管进程", "输入进程 exe 文件名（如 app.exe，多个用分号分隔）：")
        if not ok or not text.strip():
            return
        added = 0
        for name in [s.strip() for s in text.replace("；", ";").split(";") if s.strip()]:
            if not name.lower().endswith(".exe"):
                name += ".exe"
            if name in self._smart_cfg.setdefault("managed_processes", {}):
                continue
            self._smart_cfg["managed_processes"][name] = "suspend"
            self._add_proc_managed_row(name, "suspend")
            added += 1
        if added:
            core.save_manager_config(self._smart_cfg)
            self._smart_log(f"已加入托管 {added} 个进程")

    # ---- 托管服务列表行

    def _add_svc_managed_row(self, name: str, mode: str, watch: str):
        widget = QWidget()
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(QLabel(name))
        combo = QComboBox()
        combo.addItem("始终禁用", "always_off")
        combo.addItem("按需启动", "ondemand")
        combo.setCurrentIndex(0 if mode == "always_off" else 1)
        combo.currentIndexChanged.connect(
            lambda _, n=name, c=combo: self._set_svc_mode(n, c.currentData()))
        lay.addWidget(combo)
        lay.addWidget(QLabel("绑定进程："))
        watch_edit = QLineEdit(watch)
        watch_edit.setPlaceholderText("如 app.exe")
        watch_edit.setFixedWidth(150)
        watch_edit.editingFinished.connect(
            lambda e=watch_edit, n=name: self._set_svc_watch(n, e.text()))
        lay.addWidget(watch_edit)
        lay.addStretch()
        remove_btn = QPushButton("移除")
        remove_btn.setFixedWidth(64)
        remove_btn.clicked.connect(lambda _, n=name: self._remove_svc_managed(n))
        lay.addWidget(remove_btn)
        item = QListWidgetItem()
        item.setData(Qt.UserRole, name)
        self._svc_managed_list.addItem(item)
        self._svc_managed_list.setItemWidget(item, widget)

    def _set_svc_mode(self, name: str, mode: str):
        info = self._smart_cfg.setdefault("managed_services", {}).setdefault(
            name, {"mode": mode, "watch": ""})
        info["mode"] = mode
        core.save_manager_config(self._smart_cfg)

    def _set_svc_watch(self, name: str, watch: str):
        info = self._smart_cfg.setdefault("managed_services", {}).setdefault(
            name, {"mode": "ondemand", "watch": ""})
        info["watch"] = watch.strip()
        core.save_manager_config(self._smart_cfg)

    def _remove_svc_managed(self, name: str):
        self._smart_cfg.get("managed_services", {}).pop(name, None)
        core.save_manager_config(self._smart_cfg)
        for row in range(self._svc_managed_list.count()):
            if self._svc_managed_list.item(row).data(Qt.UserRole) == name:
                self._svc_managed_list.takeItem(row)
                break
        self._smart_log(f"已取消托管服务 {name}")
