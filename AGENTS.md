# 项目维护入口

- 先读 [项目进度](docs/PROJECT_STATUS.md)，再读 [分支索引](docs/BRANCHES.md)；迁移任务同时读 [迁移清单](docs/FORK_MIGRATION.md)。新任务先检查 Git 状态，再选择分支。
- `main` 自 2026-10-20 起基于上游新版 Python / PyQt6 插件架构，源码入口为 `src/nightreign_overlay/`。当前固定的上游起点是 `f1e5224`，不要用旧版目录覆盖新版。
- `LVT/archive/legacy-dev13` 保留我们完整的旧开发版；`LVT/archive/main-before-rewrite` 保留同步前的 main。归档作为迁移来源，不继续追加功能。
- 后续从当时的 `main` 新建 `LVT/migrate/*`，按功能迁移、验证后再合入。不要把旧开发分支整体 merge/rebase 到新版，也不要把旧依赖、配置或原生构建输出直接混用。
- `LVT/stable-python` 是更早的 Python 稳定基线；`LVT/experimental/csharp-rewrite` 是独立 C# 实验，不是本次上游新版。以 Git 跟踪文件确认当前代码，不能根据本地构建残留判断。
- 修改前阅读 [上游开发约定](docs/development/contributing.md)。配置沿用 `ConfigGroup`；插件之间通过 `api.py` 通信；资源按插件生命周期清理。
- 新版验证入口为 `tests/`、`uv run pytest -q`、`uv run ruff check .`、`uv run ruff format --check .`；涉及原生组件时按 [联机文档](docs/features/p2p.md) 验证。仅文档变更检查 UTF-8、相对链接、索引及 `git diff --check`。
- 旧版 `scripts.test_p2p_profile_click`、`scripts.test_settings_cleanup`、`scripts.test_performance` 等仅在归档分支有效。迁移时将相关回归纳入新版跟踪的测试目录；本地被忽略的测试不能作为干净克隆的验证依据。
- 每阶段同步维护进度，分别记录源码与推送、离线测试、运行包、真实游戏/Steam/音频验收；历史测试结果不能直接沿用为新版验收。
