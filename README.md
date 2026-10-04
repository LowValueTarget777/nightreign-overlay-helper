# Nightreign Overlay Helper

本 Fork 的 Python 维护主线为 `main`。当前功能、验收状态和本地运行包见 [项目进度](docs/PROJECT_STATUS.md)，历史分支及回退入口见 [分支索引](docs/BRANCHES.md)。C# 重构单独保存在实验分支。

[中文README](#黑夜君临悬浮助手)

Nightreign Overlay Helper is a utility program developed with PyQt6, designed to display various useful information and features while playing the game, **currently supporting only the Chinese language**.

## Features

- Displays countdowns for night rain circle shrinking and fast damage of night rain, triggered by hotkeys or automatic detection.
- Map recognition and floating map information.
- Displays health percentage markers corresponding to "trigger when health is low" and "trigger when health is full" entries.
- Displays countdowns for art buffs of certain characters.
- Displays Nightreign teammate Steam names, ping, and connection stability in a separate overlay.
- Optionally displays teammates' public Nightreign playtime; left-click a teammate's name to open their profile in the Steam client.
- Optionally blocks communication with ping-ready nonfriends to show Steam names in game, then restores communication when they leave. Recovery records survive an overlay crash or temporary network failure.
- Shows a separate two-minute commendation window for departed teammates; clicking a name opens the Steam profile, and a separate Like button posts your custom profile comment, with a seven-day interval per player.
- Optionally records cumulative nonfriend encounters, once per confirmed DAY1 expedition.

See [teammate features and recovery](docs/MATCH_NAMES.md) for settings, limits, and validation status.

## Build Instructions

#### Prerequisites
- Windows 10 or 11 (64-bit)
- Python 3.13
- .NET 8 SDK (build only; release packages include a self-contained helper)

#### Steps

1. Clone the repository and navigate to the project directory.

2. Build the executable using build script:

    ```bash
    .\build.bat
    ```

    You can find the built executable in the `dist/nightreign-overlay-helper` directory.


## Usage
Double-click `nightreign-overlay-helper.exe` to run the program. Right-click the overlay window or the taskbar icon to open the menu and access the settings window. Refer to the help in the settings UI for configuration guidance.

## Safety
The program recognizes game information through screenshots and reads local Steam connection metadata. It does not modify game data, read/write game memory, inject code, or hook the game/Steam process. ETW events are processed in memory without writing ETL files or displaying/logging peer IP addresses. No third-party tool can guarantee zero anti-cheat risk.

Optional teammate features change Steam communication settings and submit profile comments through Steam's web component. Automatic blocking is disabled by default; comments are sent only when you click the Like button in the commendation window. Pending restoration is saved locally and retried, but requires the original Steam account to be online.

## Acknowledgements

- All image resources used in this program are copyrighted by their respective owners.
- Thanks to [Fuwish](https://github.com/Fuwishx) for map data support.
- Thanks to [雀煊](https://space.bilibili.com/391379672) for sharing the Great Hollow crystal layout.
- P2P connection-query and ETW latency concepts are adapted from [SteamP2PInfo](https://github.com/tremwil/SteamP2PInfo), licensed under MIT.

---

# 黑夜君临悬浮助手

[English README](#Nightreign-Overlay-Helper)

基于PyQt6开发的用于在游戏中显示各种实用信息和功能的辅助程序，目前界面仅支持中文语言。

## 功能

- 显示缩圈和雨中冒险倒计时，支持快捷键触发或自动检测。
- 地图识别与地图信息悬浮。
- 显示“血量较低触发”与“满血时触发”的词条对应百分比血量位置标记。
- 显示部分角色的绝招buff倒计时。
- 默认按游戏客户区自动适配 16:9 的五组识别区域（以 2560×1440 默认 HUD 为基准）；特殊 HUD 或其他宽高比可在设置中关闭自动适配，恢复手动框选。原手动坐标会保留。
- 通过独立悬浮窗显示《黑夜君临》队友的 Steam 昵称、Ping 与连接稳定度。
- 可选显示队友公开的《黑夜君临》游戏时长，优先免 Key 查询；隐藏或无法访问的资料不保证可查询。
- 左键点击队友名字，在 Steam 客户端内打开该队友资料页；拖动不会误触，右键仍打开助手菜单。
- 保留地图处理缓存和后台任务等性能优化；实验性地图信息丢失推断模式已移除。
- 可选在读到有效 Ping 后自动屏蔽非好友通信，让游戏显示 Steam 名字；连续确认离队后解除。恢复记录独立保存，断网或助手崩溃后继续尝试恢复。
- 队友离开后显示独立点赞窗口，两分钟自动收起；点击名字打开 Steam 主页，点击右侧点赞按钮发送自定义主页留言，同一账号对同一玩家间隔七天。
- 可选记录非好友累计匹配次数，确认 DAY1 后按 SteamID 每局计一次，在延迟与点赞窗口显示。

队友功能的设置、恢复方法和验收边界见[队友名字与留言点赞](docs/MATCH_NAMES.md)。当前试用版本 `0.10.10.dev10`；已通过 127 项 Python 离线回归，原生组件未改，沿用 dev9 的 53 项离线回归。dev9 用户反馈基本可用；新窗口交互及真实断网与崩溃恢复等专项验收仍待完成。

地图处理使用区域裁剪、受限缓存和独立后台任务，候选图逐步显示。基准数据、验证范围及复现方法见[性能优化记录](docs/performance.md)。

## 构建

#### 环境要求

- Windows 10/11（64 位）
- Python 3.13
- .NET 8 SDK（仅构建需要，发行包中的 Helper 已自包含运行环境）

#### 构建步骤

1. 克隆代码库并进入项目目录。

2. 使用构建脚本生成可执行文件：

    ```bash
    .\build.bat
    ```

    构建完成的可执行文件位于 `dist/nightreign-overlay-helper` 目录下。


## 使用方法

双击 nightreign-overlay-helper.exe 运行程序，直接右键悬浮窗或右键任务栏图标打开菜单打开设置窗口，参考设置界面中的帮助进行配置。

## 安全性

本程序通过截屏识别游戏画面，并读取本机 Steam 连接元数据；不会修改游戏数据、读写游戏内存、注入代码或 Hook 游戏/Steam 进程。ETW 事件只在内存中处理，不生成 ETL 文件，也不显示或记录玩家 IP。任何第三方工具都无法保证反作弊风险绝对为零。

可选队友功能会通过 Steam 网页组件修改通信屏蔽状态或提交主页留言。自动屏蔽默认关闭，留言只在点击点赞窗口中的点赞按钮后发送。未完成的恢复记录保存在本地并重试，需要原 Steam 账号在线才能完成。

## 声明

- 本程序使用的图片资源所有版权归其合法所有者所有。
- 感谢来自 [Fuwish](https://github.com/Fuwishx) 的地图解包数据支持。
- 感谢来自 [雀煊](https://space.bilibili.com/391379672) 的大空洞水晶布局分享。
- P2P 连接查询及 ETW 延迟思路基于 MIT 许可的 [SteamP2PInfo](https://github.com/tremwil/SteamP2PInfo)。
