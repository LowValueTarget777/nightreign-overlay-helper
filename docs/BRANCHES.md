# Fork 分支与提交索引

更新日期：2026-10-20。main 已切换至上游新版；旧分支上的维护说明是历史快照。

## 当前分工

| 分支 | 整理时位置 | 用途 |
| --- | --- | --- |
| `main` | 上游 `f1e5224` + fork 维护文档提交 | 新版开发主线，跟踪 `origin/main` |
| `LVT/archive/legacy-dev13` | `5a23ed1a917001b54d8aad35390855a5ecf371fc` | 完整旧开发版，已同步 fork，后续迁移的固定来源 |
| `LVT/archive/main-before-rewrite` | `80b7c5ad5f767b21c82ba7e0358df7da41fc5b4a` | 更新前的 main，已同步 fork，保留旧主线 |
| `LVT/status-sound-alerts` | `5a23ed1` | 保留旧开发分支原名；后续以 dev13 归档为来源 |
| `LVT/commendation-actions` | `90c88a8` | 旧 dev10：名字与点赞按钮分离 |
| `LVT/match-name-block` | `091bc22` | 旧 dev9：屏蔽/恢复、独立点赞、遇见次数的原始开发历史 |
| `LVT/teammate-hud` | `b8550a8` | 已停止推进的队友头像侧边 HUD 实验 |
| `LVT/stable-python` | `41d91b1` | 更早的 Python 稳定基线 |
| `LVT/experimental/csharp-rewrite` | `eb44e42` | 独立 C# 实验 |
| `LVT/migrate/*` | 尚未创建 | 每批从当时 main 新建，完成验证后合回 main |

原有 `LVT/archive/*` 全部保留，包括 `main-before-maintenance`、`steam-profile-click` 等；两条新归档是新增引用，不改写旧提交。[旧分支完整映射](https://github.com/LowValueTarget777/nightreign-overlay-helper/blob/5a23ed1a917001b54d8aad35390855a5ecf371fc/docs/BRANCHES.md)仅用于历史定位。

## 远端

- `origin`：[LowValueTarget777/nightreign-overlay-helper](https://github.com/LowValueTarget777/nightreign-overlay-helper)，自己的 fork；发布本地开发成果到这里。
- `upstream`：[NeuraXmy/nightreign-overlay-helper](https://github.com/NeuraXmy/nightreign-overlay-helper)，上游；后续新版更新从 `upstream/main` 获取。
- 本次固定起点为 `f1e52248e3c4e84a9e2002cda1503a5be7eb9268`。上游 `legacy` / `legacy-dev` 保留旧架构，不合入当前新版主线。
- 历史 `upstream-review/main` 跟踪引用继续保留，没有对应的现行 remote，不作为更新来源。

## 继续开发

```powershell
git status --short --branch
git log -5 --oneline --decorate

# 妥善保存未提交修改后，从新版 main 开始一个迁移批次
git switch main
git switch -c LVT/migrate/steam-profile-playtime

# 只读查看旧实现，不需要把整份旧目录覆盖回来
git show LVT/archive/legacy-dev13:src/ui/p2p_overlay.py
git show LVT/archive/legacy-dev13:src/p2p/playtime.py
```

迁移分支名、顺序与验收范围见 [迁移清单](FORK_MIGRATION.md)。每批从届时最新 main 分叉，避免并行复制同一套旧基础设施。

## 备份与本地工作区

整理前完整 Git bundle、引用清单、旧维护说明和本地残留文件保存在 `build/repository-organization/`；具体位置由本地 `current-upstream-rewrite-backup.txt` 记录。Git bundle 保存 Git 历史，未跟踪文件另有备份，不能混为一谈。现有 dev13 运行包原位保留。

`../nightreign-overlay-helper-6da5940-test` 与 `../nightreign-overlay-helper-af5240b-test` 是原有 detached HEAD 历史工作区，本次未修改。不要在这些工作区或归档分支上继续新版功能。

[返回当前进度](PROJECT_STATUS.md) · [返回文档索引](INDEX.md)
