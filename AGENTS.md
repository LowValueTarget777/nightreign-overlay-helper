# 项目维护入口

- 先读 `docs/PROJECT_STATUS.md` 获取当前进度、验收边界和运行包，再读 `docs/BRANCHES.md` 确认分支用途。
- `main` 维护 Python 版本；`LVT/stable-python` 保留重构前基线；C# 重构位于 `LVT/experimental/csharp-rewrite`。`LVT/archive/*` 是历史定位。
- 新任务开始时检查 Git 状态，依据用户当前目标选择分支；不要因磁盘仍有被忽略的 C# 构建目录就认定正在开发 C#。
- 在一个阶段完成后同步维护进度记录，明确区分离线测试、真实游戏验收和已经发布的版本。
- 已纳入 Git 的相关回归入口：`scripts.test_p2p_profile_click`、`scripts.test_settings_cleanup`、`scripts.test_performance`。按变更范围选择运行；本地被忽略的测试不能作为干净克隆可复现的依据。
