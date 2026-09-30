# 分支与提交索引

整理日期：2026-09-10。下面是本地分支；归档通过改名完成，提交及分叉历史完整保留。

## 日常入口

| 分支 | 用途 | 整理时位置 |
| --- | --- | --- |
| `main` | Python 维护主线 | `bcb79c4` 功能提交，随后追加本次整理文档 |
| `LVT/match-name-block` | 当前：非好友屏蔽与恢复、独立逐人点赞窗口、可选累计匹配次数 | `0.10.10.dev9`；124 项 Python、53 项原生离线测试，监督/任务生命周期重新验证；中途误解除与 Steam 退出状态修复，完整实机闭环待验收 |
| `LVT/teammate-hud` | 已停止推进的头像左侧 HUD 实验，保留历史 | `0.10.9.dev8` / `b8550a8`；未合入 main |
| `LVT/stable-python` | 重构前稳定基线 | `41d91b1` |
| `LVT/experimental/csharp-rewrite` | C# 重构实验 | `eb44e42` |

原 `LVT/performance-optimization` 已改名为 `LVT/stable-python`；原 `LVT/csharp-rewrite` 已改名为 `LVT/experimental/csharp-rewrite`。

## 归档映射

除旧 `main` 新增备份引用外，以下均为原分支改名。归档不表示其全部改动已合入 `main`：截图试验和旧地图分支仍保留各自独立提交。

| 原分支 | 当前归档分支 | 提交 | 内容 |
| --- | --- | --- | --- |
| `main`（整理前） | `LVT/archive/main-before-maintenance` | `a7d4d68` | 原本地 0.10.5 主线位置 |
| `LVT/nightreign-p2p-only` | `LVT/archive/nightreign-p2p-only` | `9c551f3` | 队友连接信息初版 |
| `LVT/nightreign-playtime` | `LVT/archive/nightreign-playtime` | `8a54b82` | 可选队友时长 |
| `LVT/nightreign-stable` | `LVT/archive/nightreign-stable` | `2a27514` | 较早截图和输入稳定节点，非最新基线 |
| `LVT/adaptive-detection-regions` | `LVT/archive/adaptive-detection-regions` | `ee9ab23` | 识别区域适配 |
| `LVT/partial-map-inference` | `LVT/archive/partial-map-inference` | `f93a3b6` | 历史地图推断及免 Key 时长阶段 |
| `LVT/steam-profile-click` | `LVT/archive/steam-profile-click` | `bcb79c4` | 队友资料页点击，已进入 main |
| `LVT/maa-window-startup-fix` | `LVT/archive/maa-window-startup-fix` | `b7d3c9d` | 截图启动和焦点修复试验 |
| `LVT/mss-capture-with-p2p` | `LVT/archive/mss-capture-with-p2p` | `9cfdb6f` | MSS 截图后端试验 |
| `LVT/nightreign-playtime-fixes-backup` | `LVT/archive/nightreign-playtime-fixes-backup` | `d6fab35` | 早期截图重连修复备份 |
| `LVT/nightreign-p2p-map-inference` | `LVT/archive/nightreign-p2p-map-inference` | `ba49f1b` | 基于较早上游版本的地图 / 联机试验 |

## 关键节点

按 Python 维护线的顺序：

`75c0c47` 上游基线 → `9c551f3` 队友信息 → `8a54b82` 时长 → `2a27514` 截图/输入稳定 → `ee9ab23` 区域适配 → `f93a3b6` 免 Key 时长 → `4edd527` 性能优化 → `41d91b1` 移除实验地图模式 → `bcb79c4` 点击资料页。

C# 从 `41d91b1` 分叉：`b704abc` 初版 → `65243d2` 便携运行库补齐 → `eb44e42` 输入消息泵及窗口资源耗尽修复。其代码未合入 Python 主线。

## 查看与继续

```powershell
# 查看实际当前状态
git status --short --branch
git log -8 --oneline --decorate

# 回到日常维护入口（切换前先妥善处理未提交改动）
git switch main

# 查看重构前稳定基线
git show --stat LVT/stable-python

# 查看 C# 实验线
git log -3 --oneline LVT/experimental/csharp-rewrite
```

要继续一个旧试验，可以从对应归档引用新建功能分支。保留归档引用作为历史定位，不在归档分支上继续堆叠新工作。

## 远端和工作区

- `origin`：`LowValueTarget777/nightreign-overlay-helper`，自己的 Fork。
- `upstream`：`NeuraXmy/nightreign-overlay-helper`，原项目。
- `origin/LVT/nightreign-p2p-only` 仍保留原远端名称；本地改名不会同步改动 GitHub。
- 历史 `upstream-review/main` 是本地残留远端跟踪引用，没有对应的现行 remote 配置，本次保留于原位及备份，不作为维护入口。
- 两个额外工作区 `../nightreign-overlay-helper-6da5940-test`、`../nightreign-overlay-helper-af5240b-test` 都是 detached HEAD，检查时跟踪文件干净，保留作旧版本对照。
- Python 工作区中可能仍看到被忽略的 `csharp/` 构建残留；是否属于当前版本以 `git ls-files` 为准，不能仅看磁盘目录。

本次未推送远端，也没有改写旧提交。整理前完整引用清单及 bundle 位于 `build/repository-organization/20260910-133717/`。
