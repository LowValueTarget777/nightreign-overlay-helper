# 自定义功能迁移清单

更新日期：2026-10-20。当前仅完成基线和归档整理，以下功能迁移均未开始。

## 起点和方法

- 新架构起点：上游 `f1e52248e3c4e84a9e2002cda1503a5be7eb9268`，源码版本 `1.0.1`。
- 旧功能来源：`LVT/archive/legacy-dev13` / `5a23ed1a917001b54d8aad35390855a5ecf371fc`。更早的原始提交用于理解变更，不整批合入新版。
- 每批从当时的 main 新建功能分支；保留上游插件、配置、UI 和生命周期结构，移植行为及相应回归，不直接替换旧文件。
- 先检查上游已有实现，特别是 P2P 和音频服务；已有能力不重复开发。每批可独立测试、审阅、打包和回退。

## 批次

| 顺序 | 建议分支 | 范围 | 状态 |
| --- | --- | --- | --- |
| 1 | `LVT/migrate/steam-profile-playtime` | 名字点击进入 Steam 主页、可选游戏时长 | 未开始 |
| 2 | `LVT/migrate/voice-alerts` | 用户语音资源、播报时机、默认与试听设置 | 未开始 |
| 3 | `LVT/migrate/encounters-commendation` | 非好友遇见次数、独立限时点赞窗口及留言 | 未开始 |
| 4 | `LVT/migrate/teammate-block-recovery` | 非好友屏蔽/恢复、重试、异常恢复与状态显示 | 未开始 |

### 第一批：资料页与时长

旧实现参照 `src/ui/p2p_overlay.py`、`src/p2p/playtime.py`、`src/p2p/public_playtime.py`。新入口位于 `src/nightreign_overlay/plugins/p2p_latency/`，应复用其队友模型、配置和悬浮窗。

- 名字通过 Steam URI 在 Steam 客户端进入对应主页；保留右键菜单、拖动防误触和刷新期间点击目标稳定。
- 游戏时长为可选查询；保留隐私不可用等状态，网络请求不阻塞 UI。
- 对照旧 `scripts/test_p2p_profile_click.py` 迁移交互回归；新测试纳入新版 `tests/`。真实 Steam 跳转与不同隐私设置另行验收。

### 第二批：语音提醒

旧实现参照 `src/audio_alerts.py`、`assets/sounds/` 与 `scripts/test_audio_alerts.py`；旧语音提交为 `5928609`，默认开启调整为 `5a23ed1`。新版已有 `plugins/audio/`、`core/audio.py` 和计时插件，优先接入它们的服务契约。

- 保留四条离线语音：缩圈开始前 30 秒、缩圈结束前 30 秒、雨中冒险剩 10 秒、绝招剩 3 秒。
- 新配置或缺少开关时默认开启，已明确关闭的设置继续关闭；保留音量、分类开关及逐条试听。
- 保留去重、不补播、播放互斥、前台门控和退出清理。新版配置、实际播放设备和计时触发分别验证，不照搬旧播放器。

### 第三批：遇见次数与点赞

旧实现参照 `src/p2p/encounters.py`、`src/p2p/commendation.py`、`src/ui/commendation_overlay.py`，以及原生 `CommendationWorker.cs` / `CommendationHistory.cs`。

- 遇见次数可选且只记录非好友；按 Steam 账号和目标保存，每局去重，保留改名、重连、重启和写入失败的处理。
- 点赞与延迟使用独立窗口；按队友离队逐人出现并限时关闭，留房好友不阻止其他人的入口。离队只是结束提示的近似依据，不宣称精确识别结算页。
- 名字点击进入主页，独立按钮发送可自定义留言；按账号与目标保存七天间隔，处理重复点击与不确定结果。
- 原生网页会话若与恢复组件共用，先提取本批需要的明确接口，不提前启用第四批的自动屏蔽。留言验证与离线模拟分开记录。

### 第四批：屏蔽、恢复与状态

旧实现参照 `src/p2p/match_block.py`、`src/p2p/identity_bridge.py`、`src/p2p/block_status.py`、原生 `Identity*.cs` 及 `scripts/identity_native_tests/`。详细约束保留在 [旧队友功能说明](https://github.com/LowValueTarget777/nightreign-overlay-helper/blob/5a23ed1a917001b54d8aad35390855a5ecf371fc/docs/MATCH_NAMES.md)。

- 设置独立开关；好友一律跳过，非好友进入有效延迟名单后尝试屏蔽，不再依赖 DAY1。现有数据无法严格证明邀请或匹配来源，好友判断不能描述成来源识别。
- 两名队友独立处理，准备失败按策略重试；结果不明优先恢复，不盲目重复提交屏蔽请求。
- 短暂名单丢失、连接错误和心跳延迟不提前解除；确认离队、游戏退出、关闭功能和助手退出进入恢复流程。
- 保留原有屏蔽状态，持久记录本软件发起的事务；独立监督与下次启动恢复覆盖崩溃和断网。网络不可用时只能保留待恢复状态并重试，不能保证即时解除。
- 区分请求已提交、已确认屏蔽、解除中、恢复失败与恢复完成；状态展示必须依据实际确认。
- 游戏退出后辅助进程释放 Steam API，避免持续显示在游戏；验证双人、连续多局、断网、助手崩溃、Steam 退出/切账号等边界。

## 每批完成条件

1. 确认与当时上游实现的差异，代码符合 [开发约定](development/contributing.md)，配置接入 `ConfigGroup`，插件间只依赖公开 API。
2. 将相关回归纳入 Git；在新版环境执行 `pytest`、Ruff 和 `git diff --check`，涉及原生组件时运行原生回归。旧分支测试成绩不代替新环境结果。
3. 需要试用时构建独立运行包，记录提交、资源和依赖；原有 dev13 包保留。新版与旧版的数据兼容先检查，不能直接覆盖用户设置或丢弃待恢复事务。
4. 在进度文档分别记录离线通过、包检查、真实游戏/Steam/音频结果及尚未验收的边界，再合入 main。

当前不恢复已经停止的头像侧边 HUD 实验，也不合入独立 C# 重构。它们继续保留在历史分支。

[返回当前进度](PROJECT_STATUS.md) · [分支索引](BRANCHES.md) · [文档总索引](INDEX.md)
