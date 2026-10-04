# Uninstall Helper · Windows 系统维护工具集

一套用 Python 写的 Windows 系统维护小工具：**卸载助手**（管理已装软件 / 启动项 / 进程 / 服务）、**WiFi 守护**（断网自动重连）、**驱动助手**、**智能省电**，以及一个把外部 AI 当「大脑」的 **双脑一体** 面板。

> 学习性质的开源项目。**欢迎任何人 fork、修改、提交 PR**。原创设计保留，代码自由使用。

---

## ⚠️ 安全提示（务必先读）

本工具会**读写 Windows 注册表、操作服务与进程、结束进程、删除文件**。请务必：

1. **先读懂代码再运行**，尤其是 `core.py` 中的注册表与服务操作
2. 涉及删除的操作（清理残留、删驱动包）请先用 `--dry`（试运行）确认
3. 建议在虚拟机或非关键机器上先测试
4. 作者不对任何误操作造成的损失负责（见 LICENSE）

## 包含什么

| 模块 | 文件 | 说明 |
|---|---|---|
| **卸载助手（命令行）** | `卸载助手命令行版.py` | 主程序，交互菜单 + 全部子命令 |
| **核心逻辑** | `core.py` | 只用公开文档化的注册表接口（Uninstall / Run / StartupApproved） |
| **图形界面** | `main_window.py` / `main.py` | Tkinter GUI |
| **WiFi 守护** | `wifi_keeper.py` | 断网自动重连；连得上但没网时自动切换到其他连过的 WiFi |
| **双脑一体** | `双脑一体.py` | 把两个外部 AI 当两个「大脑」，本机执行器当「身体」，统一可视化面板 |
| 辅助 | `fetch_wtg.py` / `install_git.py` | Rufus/Ventoy 下载、国内镜像静默装 Git |

## 快速开始

```bash
# 环境：Windows + Python 3.10+
# 无第三方依赖（全部用标准库），可直接运行

# 进入交互菜单
python 卸载助手命令行版.py

# 常用子命令
python 卸载助手命令行版.py apps            # 列出已安装软件
python 卸载助手命令行版.py startup         # 列出启动项
python 卸载助手命令行版.py procs           # 列出进程
python 卸载助手命令行版.py svcs            # 列出服务
python 卸载助手命令行版.py wifi status     # WiFi 守护状态
python 卸载助手命令行版.py drv problems    # 异常设备

# 图形界面
python main.py
```

## 双脑一体配置

复制并填写 `dualbrain_config.json`：

```json
{
  "api_base": "https://open.bigmodel.cn/api/paas/v4",
  "api_key": "在此填入你的APIKEY",
  "api_model": "glm-4-flash-250414",
  "openclaw_cmd": "",
  "hermes_cmd": "",
  "web_port": 8765,
  "web_token": "请改成你自己的随机串"
}
```

> ⚠️ **切勿把真实 API 密钥提交到公开仓库。** `dualbrain_config.json` 已加入 `.gitignore`；仓库里只保留不含密钥的模板。若你不小心提交过密钥，请立即去平台后台**重置密钥**。

## 设计亮点

- **只用公开接口**：已装软件来自 `Uninstall` 注册表键（含 32 位视图），启动项来自 `Run` 键与「启动」文件夹，遵循 `StartupApproved` 规范 —— 不碰未文档化的黑接口
- **卸载 = 跑软件自带的 `UninstallString`**，随后检测并清理注册表键 / 目录残留
- **WiFi 守护三者联动**：开关状态 + 开机自启（注册表）+ 后台进程，开启时同时写开关/加自启/拉起进程，关闭时全部反向清理
- **双脑一体架构**：外部 Node/Python AI 分别当「大脑 A / 大脑 B」，本机执行器当「身体」，可按路由规则分配任务

## 参与贡献

欢迎任何改进！

1. **Fork** 本仓库
2. 新建分支：`git checkout -b feature/你的改动`
3. 提交：`git commit -m "描述你的改动"`
4. 推送并发起 **Pull Request**

修 bug、加功能、改进文档、补充测试都欢迎。也欢迎提 Issue。

## 许可

本项目采用 **MIT License**，详见 [LICENSE](LICENSE)。

---

*Windows 系统维护小工具集，原创开源分享。使用前请务必理解其行为。*
