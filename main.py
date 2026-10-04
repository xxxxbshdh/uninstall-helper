"""卸载助手入口。"""
import ctypes
import os
import sys


def is_admin() -> bool:
    """检测当前进程是否以管理员权限运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> None:
    """以管理员身份重启本程序；原进程退出。"""
    # 打包成 exe 时命令行就是 exe 自己，参数从 argv[1] 开始；
    # 以脚本运行时必须把脚本路径也带上，否则提权后的进程拿不到 main.py 会直接退出
    if getattr(sys, "frozen", False):
        args = list(sys.argv[1:])
    else:
        args = [os.path.abspath(sys.argv[0])] + list(sys.argv[1:])
    params = " ".join(f'"{a}"' for a in args)
    # ShellExecuteW 返回值 > 32 表示成功
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )
    if rc <= 32:
        # 用户拒绝 UAC 或失败，回退到普通模式继续运行
        return
    sys.exit(0)


if __name__ == "__main__":
    if not is_admin():
        relaunch_as_admin()
        # 提权失败（用户拒绝）时落到这里，继续以普通权限启动
    from PySide6.QtWidgets import QApplication

    from main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
