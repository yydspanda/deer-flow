# Git 工作流完整教程（Fork 项目）

## 一、基础概念

### 1.1 远程仓库的关系

```
upstream  =  VectifyAI/PageIndex（上游原仓库，别人维护的）
origin    =  yydspanda/PageIndex（你的 fork，你自己的副本）
local     =  本地电脑上的代码
```

三者的数据流向：

```
upstream ──fetch──→ local ──push──→ origin
                        ↑                 │
                        └────pull/clone───┘
```

- `fetch`：从远程下载代码到本地，不合并
- `pull`：fetch + merge（拉取并合并）
- `push`：把本地提交推到远程
- 你只能 push 到 `origin`（你的 fork），不能 push 到 `upstream`

### 1.2 初始配置（只需做一次）

```bash
# 1. clone 你自己的 fork
git clone git@github.com:yydspanda/PageIndex.git
cd PageIndex

# 2. 添加上游仓库
git remote add upstream git@github.com:VectifyAI/PageIndex.git

# 3. 禁止直接推到上游（防止误操作）
git remote set-url --push upstream no_push

# 4. 验证
git remote -v
# origin    git@github.com:yydspanda/PageIndex.git (fetch)
# origin    git@github.com:yydspanda/PageIndex.git (push)
# upstream  git@github.com:VectifyAI/PageIndex.git (fetch)
# upstream  no_push (push)  ← 安全保护，push 会报错
```

---

## 二、必须理解的核心概念

### 2.1 什么是 Commit（提交）

每次 `git commit` 就像给项目拍了一个快照。Git 用一条链表记录所有快照：

```
A ← B ← C ← D ← E（HEAD，当前位置）
```

每个字母是一个 commit，`←` 表示"基于上一个 commit"。`HEAD` 指向你当前所在的位置。

### 2.2 什么是 Branch（分支）

分支就是给某个 commit 起的名字，方便引用。

```
A ← B ← C ← D ← E  (main)
         └── F ← G  (feature)
```

- `main` 指向 E
- `feature` 指向 G
- F 和 G 基于 C 分叉出去

### 2.3 什么是 Merge（合并）

把两个分支的修改合到一起，会产生一个新的 **merge commit**：

```
合并前：
A ← B ← C ← D ← E  (main)
         └── F ← G  (feature)

合并后（git merge feature）：
A ← B ← C ← D ← E ← ─ ─ M  (main)
         └── F ← G ← ─ ─ ┘
                         ↑
                    merge commit（有两个父提交）
```

M 是一个特殊的提交，它同时有两个父亲（E 和 G），代表"在这里把两条线合并了"。

**缺点**：如果频繁同步上游，会产生大量无意义的 merge commit，历史变成一团乱麻。

### 2.4 什么是 Rebase（变基）⭐ 重点

Rebase 的意思是"重新设置基点"——把你的提交"拔起来"，接到另一个基础之上。

#### 图解

```
变基前（你要同步上游，上游已经到了 D）：
A ← B ← C  (upstream/main，上游最新)
     └── X ← Y  (你的本地 main，基于 B 开发的)

执行 git rebase upstream/main 后：
A ← B ← C ← X' ← Y'  (你的本地 main)
            ↑
      你的提交被"重放"到 C 之后
```

**发生了什么：**

1. Git 找到你的提交（X, Y）和目标基础（C）的分叉点（B）
2. 临时保存 X, Y 的修改内容
3. 把 main 指向 C（上游最新）
4. 在 C 之后依次重新应用 X → X'，Y → Y'
5. X' 和 X 内容一样，但 commit hash 不同（因为父提交变了）

#### 和 merge 的对比

```
merge 结果：   A ← B ← C ← ─ M ← X ← Y     ← 多了一个 merge commit M
                       └← X ← Y ← ─ ┘

rebase 结果：  A ← B ← C ← X' ← Y'          ← 干净的直线
```

**rebase 的优点**：历史是一条直线，干净清晰
**rebase 的缺点**：改写了 commit hash（因为父提交变了），如果已经 push 过，再次 push 需要 `--force-with-lease`

#### 什么时候用 rebase

- ✅ 同步上游更新时用 rebase（保持历史干净）
- ✅ 在自己还没 push 的分支上用 rebase
- ❌ 不要在多人协作的公共分支上 rebase（会改写别人的历史）

#### rebase 冲突怎么处理

```
# rebase 过程中出现冲突：
CONFLICT (content): Merge conflict in pageindex/utils.py

# 1. 打开冲突文件，会看到：
<<<<<<< HEAD
上游的内容
=======
你的内容
>>>>>>> 你的提交

# 2. 手动选择保留哪边，或者两边都保留，删除标记符号

# 3. 标记为已解决
git add <冲突文件>

# 4. 继续 rebase
git rebase --continue

# 5. 如果搞砸了，放弃重来
git rebase --abort
```

### 2.5 什么是 Reset（重置）⭐ 重点

Reset 是把 `HEAD`（当前位置）强制移动到另一个 commit。

#### 三种模式

```bash
# 假设当前状态：A ← B ← C ← D（HEAD 在 D）

# 1. --soft：只移动 HEAD，改动保留在暂存区
git reset --soft B
# 结果：A ← B（HEAD）
# C 和 D 的改动还在，并且在 git add 状态（绿色）
# 用途：合并多个提交为一个（reset --soft 后重新 commit）

# 2. --mixed（默认）：移动 HEAD，改动保留在工作区但不在暂存区
git reset --mixed B
# 或简写为
git reset B
# 结果：A ← B（HEAD）
# C 和 D 的改动还在，但不在 git add 状态（红色）
# 用途：撤销 git add，或者重新整理提交

# 3. --hard：移动 HEAD，改动全部丢弃⚠️
git reset --hard B
# 结果：A ← B（HEAD）
# C 和 D 的改动完全消失，找不回来了（其实可以通过 git reflog 找回）
# 用途：彻底放弃最近的修改
```

#### 图解对比

```
原始状态：A ← B ← C ← D
                     ↑ HEAD

git reset --soft B:    保留改动（暂存区）  →  A ← B (HEAD)，改动在 git add 状态
git reset --mixed B:   保留改动（工作区）  →  A ← B (HEAD)，改动在未 add 状态
git reset --hard B:    丢弃改动           →  A ← B (HEAD)，什么都没了
```

#### 常见用法

```bash
# 撤销最后一次 commit（改动保留，重新编辑）
git reset --soft HEAD~1

# 撤销 git add（unstage 所有文件）
git reset HEAD

# 撤销 git add（unstage 单个文件）
git reset HEAD <file>

# 彻底放弃最近 3 个 commit（危险！）
git reset --hard HEAD~3
```

#### reset --hard 后怎么救回来

```bash
# 查看所有操作历史
git reflog
# 输出类似：
# e123c81 HEAD@{0}: reset: moving to HEAD~3
# a1b2c3d HEAD@{1}: commit: 我的提交 D
# f4e5d6a HEAD@{2}: commit: 我的提交 C
# ...

# 找到想回去的那一步，恢复
git reset --hard a1b2c3d
# 就回到了"我的提交 D"的状态
```

**reflog 是 Git 的后悔药**，几乎所有"误操作"都能通过 reflog 找回，前提是不要太久（默认保留 90 天）。

### 2.6 什么是 Fetch（获取）

```bash
git fetch upstream
```

只是从远程下载最新的 commit 信息和文件到本地，**不会修改你的工作区**。

下载的内容存在 `upstream/main` 这个"远程分支"上，你可以：

```bash
# 看上游更新了什么
git log HEAD..upstream/main --oneline

# 看具体改了哪些文件
git diff HEAD..upstream/main --stat

# 看具体改了什么内容
git diff HEAD..upstream/main
```

确认没问题后再 `git rebase upstream/main` 合进来。

### 2.7 什么是 Force Push（强制推送）

Rebase 之后，你的 commit hash 变了（比如从 X 变成 X'），远程还记录着旧的 hash。普通 `git push` 会被拒绝，因为 Git 认为你"丢失了"提交。

```bash
# 安全的强制推送（推荐）
git push --force-with-lease

# 不安全的强制推送（不推荐）
git push --force
```

**区别**：

- `--force`：无条件覆盖远程
- `--force-with-lease`：只有当远程还是你预期的状态时才覆盖，如果别人在你之前推了新提交，会拒绝推送（保护机制）

---

## 三、日常工作流程

### 3.1 同步上游更新（最常用）

```bash
# 第一步：拉取上游最新代码（不影响本地）
git fetch upstream

# 第二步：看看上游更新了什么
git log HEAD..upstream/main --oneline

# 第三步：变基到上游最新
git rebase upstream/main

# 第四步：推送到你的 fork
git push --force-with-lease
```

**记住这个口诀：拉(fetch)、看(log)、接(rebase)、推(push)**

### 3.2 在 main 上直接开发（你目前的用法）

```bash
# 1. 先确保同步了上游
git fetch upstream
git rebase upstream/main

# 2. 修改代码...

# 3. 提交
git add .
git commit -m "添加中文注释"

# 4. 推到自己的 fork
git push
# 如果之前 rebase 过，用：
git push --force-with-lease
```

### 3.3 使用功能分支开发（推荐）

```bash
# 1. 基于上游最新创建分支
git fetch upstream
git checkout -b my-feature upstream/main

# 2. 开发、提交
git add .
git commit -m "新功能"

# 3. 推到你的 fork
git push -u origin my-feature

# 4.（可选）在 GitHub 网页上向上游提 Pull Request

# 5. 开发完成后，切回 main
git checkout main

# 6. 删除功能分支（可选）
git branch -d my-feature
git push origin --delete my-feature
```

### 3.4 向上游提 PR（Pull Request）

```bash
# 1. 确保分支基于最新上游
git fetch upstream
git checkout my-feature
git rebase upstream/main

# 2. 推到你的 fork
git push -u origin my-feature
# （如果 rebase 过）git push --force-with-lease

# 3. 去 GitHub 网页操作：
#    yydspanda/PageIndex → Pull requests → New pull request
#    base: VectifyAI/PageIndex:main  ←  compare: yydspanda/PageIndex:my-feature
```

---

## 四、常用命令速查

### 查看状态

```bash
git status                    # 当前工作区状态
git log --oneline -10         # 最近 10 条提交
git log --oneline --graph     # 图形化查看分支历史
git diff                      # 未暂存的修改
git diff --staged             # 已暂存的修改
git remote -v                 # 查看远程仓库
git branch -vv                # 查看分支及跟踪关系
```

### 撤销操作

```bash
git reset --soft HEAD~1       # 撤销最后一次 commit，改动在暂存区
git reset HEAD~1              # 撤销最后一次 commit，改动在工作区
git reset --hard HEAD~1       # 撤销最后一次 commit，改动全丢
git reset HEAD <file>         # 撤销 git add（unstage）
git checkout -- <file>        # 撤销工作区修改（恢复到最后一次 commit）
git reflog                    # 查看操作历史（后悔药）
```

### 同步与推送

```bash
git fetch upstream            # 拉取上游
git rebase upstream/main      # 变基到上游
git push                      # 推送到 origin
git push --force-with-lease   # 安全的强制推送
```

---

## 五、VS Code 对应操作

| 操作 | VS Code 路径 | 命令行 |
|------|-------------|--------|
| 查看修改 | 左侧源代码管理面板 | `git status` / `git diff` |
| 暂存文件 | 文件旁的 + 号 | `git add <file>` |
| 提交 | 输入消息 → ✓ | `git commit -m "..."` |
| 拉取上游 | ... → 获取从 → upstream | `git fetch upstream` |
| 变基 | ... → 将分支变基到 → upstream/main | `git rebase upstream/main` |
| 推送 | 状态栏 ↑ 或 ... → 推送 | `git push` |
| 强制推送 | ... → 推送（强制） | `git push --force-with-lease` |
| 查看历史 | GitLens 或 ... → 查看历史 | `git log` |

---

## 六、常见场景 FAQ

### Q1：push 被拒绝，说 "failed to push some refs"

说明远程有你本地没有的提交。两种情况：

```bash
# 情况A：别人推了新提交（或你在网页上 sync fork 了）
git fetch upstream
git rebase upstream/main
git push --force-with-lease

# 情况B：你之前 rebase 过，commit hash 变了
git push --force-with-lease
```

### Q2：rebase 过程中冲突了，不想处理了

```bash
git rebase --abort    # 放弃 rebase，回到 rebase 前的状态
```

### Q3：不小心 reset --hard，代码丢了

```bash
git reflog                    # 找到丢失的 commit hash
git reset --hard <hash>       # 恢复过去
```

### Q4：想合并多个 commit 为一个

```bash
# 假设最近 3 个 commit 想合并成一个：
git reset --soft HEAD~3       # 回退 3 步，改动保留在暂存区
git commit -m "合并后的提交信息"  # 重新提交
git push --force-with-lease
```

### Q5：GitHub 网页上点了 Sync fork，本地怎么办

```bash
# Sync fork 等于在 origin 上做了一次 merge
# 本地同步：
git fetch origin
git rebase origin/main
# 后续推送需要 force push
git push --force-with-lease
```

**建议**：尽量不要在 GitHub 网页上点 Sync fork，用本地 `git fetch upstream && git rebase upstream/main` 替代，历史更干净。

---

## 七、开源协作完整流程：从 Fork 到 PR 被合入 ⭐ 实战

这一节用一个真实案例，手把手教你：**怎么从零开始给开源项目贡献代码**。

### 7.1 真实案例：PR #2588 的完整故事

你在 `git log` 里看到的这条提交：

```
4ead2c6 fix(config): reset config-backed singletons on hot reload (#2588)
```

它背后的完整过程是这样的：

```
时间线：
4/26  KiteEater 发现 Issue #2540，开始修 Bug
4/26  第 1 个 commit：Fix stale config singletons on reload
4/30  upstream 有了新提交，merge main 到自己的分支（解冲突）
5/1   第 3 个 commit：update checkpointer imports
5/3   第 4 个 commit：Fix config reload singleton mutation
5/3   再次 merge main 解冲突
5/6   rayhpeng（维护者）Review + Approve + Merge
      → GitHub 自动 squash 成 1 个 commit 合入 main
```

**KiteEater 不是字节员工**，就是一个外部贡献者，和你一样 fork 了项目。

### 7.2 你要做的完整流程（10 步）

#### 第 1 步：找一个你想修的 Issue

```
去 github.com/bytedance/deer-flow/issues
筛选条件：
  - good first issue（适合新手的标签）
  - bug（修 bug 比加新功能更容易被接受）
  - 你实际遇到过的问题（最有动力）
```

#### 第 2 步：Fork + 建分支

```bash
# 你已经 fork 过了，跳过 fork 步骤

# 同步到最新
git fetch upstream
git rebase upstream/main

# 建一个有意义的分支名（项目惯例：类型/issue号-简短描述）
git checkout -b fix/2540-config-reload-reset-singletons
# 或：git checkout -b feat/add-streaming-support
# 或：git checkout -b docs/fix-readme-typos
```

#### 第 3 步：改代码 + 测试

```bash
# 改代码...
# 跑测试
cd backend && make lint && make test
cd frontend && pnpm lint && pnpm typecheck
```

#### 第 4 步：提交（注意格式）

```bash
git add <相关文件>
git commit -m "fix(config): reset singletons on hot reload"
```

**Conventional Commits 格式**（DeerFlow 项目的规范）：

```
类型(范围): 简短描述

类型：
  feat     新功能
  fix      Bug 修复
  docs     文档
  refactor 重构（不改功能）
  test     测试
  chore    构建/工具/杂务

范围（可选）：config, agent, gateway, frontend 等模块名

示例：
  feat(agent): add streaming support for sub-agents
  fix(gateway): handle missing config section gracefully
  docs: update API documentation
```

#### 第 5 步：推到你自己的 Fork

```bash
git push -u origin fix/2540-config-reload-reset-singletons
```

#### 第 6 步：在 GitHub 网页上创建 PR

```
1. 打开 github.com/yydspanda/deer-flow
2. GitHub 会自动提示 "fix/2540-... had recent pushes"，点 "Compare & pull request"
3. 或者：Pull requests → New pull request → "compare across forks"
   base: bytedance/deer-flow:main  ←  head: yydspanda/deer-flow:fix/2540-...
4. 填写 PR 模板：
```

**PR 标题格式**（和 commit 一样用 Conventional Commits）：
```
fix(config): reset config-backed singletons on hot reload
```

**PR 描述模板**（DeerFlow 用 What / How / Test / Verification）：
```markdown
## What
<!-- 解决什么问题 -->
Fixes #2540
修复配置热重载时，删除某个配置段后内存单例不清理的问题。

## How
<!-- 怎么改的，列出关键改动 -->
- 在 AppConfig.from_file() 中始终刷新单例配置段
- checkpointer 和 stream_bridge 配置加载器处理缺失段
- 重载后重置持久化单例

## Test
<!-- 加了什么测试 -->
- 新增 test_app_config_reload.py 中的回归测试
- 验证删除配置段后单例被正确重置

## Verification
<!-- 怎么验证的，跑了什么命令 -->
- `cd backend && make lint && make test`
```

#### 第 7 步：等待 Review

```
提交 PR 后，维护者会：
  1. 自动检查：CI 跑 lint + test + build（GitHub Actions 自动执行）
  2. 代码审查：维护者或 Copilot 会评论你的代码
  3. 可能要求修改

心态：被要求修改是正常的，不代表你做得差。
      Linux 之父也会被 Review。这是学习的机会。
```

#### 第 8 步：根据反馈修改

```bash
# 不需要新建 PR，在同一个分支上继续提交
git add .
git commit -m "fix(config): address review feedback"
git push
# PR 自动更新（因为指向的是同一个分支）
```

#### 第 9 步：被合入（Merge）

```
维护者会选一种方式合入：

1. Squash and merge（最常见）
   → 你的 N 个 commit 被压缩成 1 个
   → 标题用 PR 标题
   → commit message 里保留每个子 commit 的标题
   → 这就是为什么 4ead2c6 里有 * Fix stale... * fix(config)... 这样的格式

2. Merge commit
   → 保留所有 commit，加一个 merge commit

3. Rebase and merge
   → 保留所有 commit，线性历史
```

#### 第 10 步：同步到你本地

```bash
git fetch upstream
git rebase upstream/main
git push --force-with-lease
```

### 7.3 提交信息解读：为什么 `#2588` 会出现在 commit 里

```
fix(config): reset config-backed singletons on hot reload (#2588)
│   │       │                                         │      └── GitHub 自动追加的 PR 编号
│   │       │                                         └── PR 标题
│   │       └── 影响范围
│   └── 类型：fix
└── 前缀
```

`#2588` 不是 KiteEater 写的，是 **GitHub 在 squash merge 时自动追加的**。
效果：在 GitHub 网页上，`#2588` 会变成一个可点击的链接，直接跳到 PR 页面。

### 7.4 完整流程图

```
你（yydspanda）
  │
  │ 1. 在 upstream 看到感兴趣的 Issue
  │
  │ 2. 本地建分支
  │    git checkout -b fix/xxx upstream/main
  │
  │ 3. 改代码 + 测试
  │    make lint && make test
  │
  │ 4. 提交
  │    git commit -m "fix(xxx): description"
  │
  │ 5. 推到你自己的 fork
  │    git push -u origin fix/xxx
  │
  ▼
GitHub（yydspanda/deer-flow）
  │
  │ 6. 创建 Pull Request
  │    base: bytedance/deer-flow:main
  │    ←  compare: yydspanda/deer-flow:fix/xxx
  │
  ▼
upstream 维护者（bytedance/deer-flow）
  │
  │ 7. CI 自动检查（lint + test + build）
  │ 8. 人工 Review（可能提意见、要求修改）
  │ 9. 你根据反馈继续提交 → PR 自动更新
  │ 10. 维护者点击 Merge
  │
  ▼
upstream/main 有了你的代码
  │
  │ git fetch upstream && git rebase upstream/main
  │
  ▼
你的本地也同步了
```

### 7.5 第一次贡献的心理建设

**你觉得害怕的事情，其实每个人都会经历：**

| 害怕 | 现实 |
|------|------|
| "我不够好，代码会被嘲笑" | 维护者见过更糟糕的代码。他们只会给建设性意见。 |
| "我提了 PR 被拒绝怎么办" | 被拒绝很正常。Linus Torvalds 每天拒绝几十个 PR。关键是学到了什么。 |
| "我不知道该贡献什么" | 从 good first issue 开始，或者修你遇到过的 bug，或者改文档错别字。 |
| "英语不好怎么办" | PR 描述可以用简单英语，甚至用 AI 帮你写。代码是通用语言。 |

**最安全的第一次贡献路径：**

1. 修文档错别字（docs 類型的 PR，几乎 100% 会被合入）
2. 修你实际遇到的 bug（你已经踩过 summarization 的坑了）
3. 补测试（test 類型，维护者最喜欢）
4. 翻译（如果项目接受多语言）

**你已经有的优势：**
- 你有 AI 安全背景（框架安全视角是稀缺的）
- 你已经把整个项目读了一遍（比 90% 的贡献者更了解架构）
- 你已经踩过 bug 并理解了原因（summarization 那个问题就是个好 PR 素材）

---

## 八、实操演练：Step by Step 完整走一遍 ⭐ 动手做过

> 这一节是你亲手执行的操作记录。项目 `yydspanda/hello-collab` 还在 GitHub 上，你可以随时查看。
> 一共做了两个 PR：PR #2（divide by zero）和 PR #4（power negative exponent）。
> PR #4 是你一步一步自己完成的。

### 8.1 两个 PR 的完整记录

| PR | Bug | Issue | 分支名 | 状态 |
|----|-----|-------|--------|------|
| #2 | `divide(1, 0)` 崩溃 | #1 | `fix/1-divide-by-zero` | ✅ Merged |
| #4 | `power(2, -1)` 返回 1 而非 0.5 | #3 | `fix/3-power-negative-exponent` | ✅ Merged |

### 8.2 PR #4 完整过程（你亲手做的）

#### Step 1：建 Issue（GitHub 网页）

1. 打开 `github.com/yydspanda/hello-collab/issues/new`
2. Title：`fix: power() returns wrong result for negative exponents`
3. Body：描述 bug、复现步骤、期望行为
4. 点 **Submit new issue** → Issue #3

#### Step 2：建分支（本地）

```bash
git checkout -b fix/3-power-negative-exponent
```

分支命名惯例：`类型/Issue号-简短描述`（fix/3-power-negative-exponent）

#### Step 3：改代码（编辑器）

修改 `calculator.py` 的 `power` 函数，加负数指数支持和类型检查：

```python
def power(base, exponent):
    if not isinstance(exponent, int):
        raise TypeError("Exponent must be an integer")
    if exponent < 0:
        return 1 / power(base, -exponent)
    result = 1
    for i in range(exponent):
        result = result * base
    return result
```

#### Step 4：写测试（编辑器）

在 `test_calculator.py` 末尾加：

```python
def test_power_negative_exponent():
    assert power(2, -1) == 0.5
    assert power(2, -2) == 0.25
    assert power(3, -1) == pytest.approx(1 / 3)

def test_power_float_exponent():
    with pytest.raises(TypeError, match="Exponent must be an integer"):
        power(2, 1.5)
```

#### Step 5：跑测试

```bash
# 如果没有 .venv，先创建（clone 后 .venv 不会跟着过来，因为在 .gitignore 里）
uv venv .venv && uv pip install pytest

# 跑测试
.venv/bin/pytest test_calculator.py -v
# 10 passed ✅
```

#### Step 6：提交

```bash
git add calculator.py test_calculator.py .gitignore
git commit -m "fix(calculator): support negative exponents in power()"
```

#### Step 7：推送

```bash
git push -u origin fix/3-power-negative-exponent
```

VS Code 会提示 "3 outgoing changes"，就是你这 3 个文件的提交，正常。

#### Step 8：创建 PR

```bash
gh pr create --repo yydspanda/hello-collab \
  --base main \
  --head fix/3-power-negative-exponent \
  --title "fix(calculator): support negative exponents in power()" \
  --body "$(cat <<'EOF'
## What
Fixes #3

## How
- Added negative exponent handling: 1 / power(base, -exponent)
- Added type check: float exponents raise TypeError

## Test
- Added test_power_negative_exponent
- Added test_power_float_exponent
- All 10 tests pass
EOF
)"
# → PR #4 创建成功
```

#### Step 9：检查 CI

```bash
gh pr checks 4 --repo yydspanda/hello-collab
# ✅ pass — GitHub Actions 自动跑了 pytest
```

#### Step 10：切换身份为维护者，Review + Merge

命令行方式：

```bash
gh pr merge 4 --repo yydspanda/hello-collab --squash --delete-branch
```

网页方式：

1. 打开 PR 页面 → 找到绿色 **Merge pull request** 按钮
2. 点旁边的 **▼ 小箭头** → 选 **Squash and merge**
3. 确认 commit message → 点 **Confirm squash and merge**

#### Step 11：同步到本地

```bash
git checkout main && git pull origin main
git log --oneline -5
# 看到：fix(calculator): support negative exponents in power() (#4) ✅
```

### 8.3 三种 Merge 方式对比

维护者合入 PR 时有三种选择：

| 方式 | 效果 | 适用场景 |
|------|------|---------|
| **Squash and merge** ✅ | N 个 commit 压成 1 个 | **最常用**。贡献者可能改了 10 次，维护者只想保留一个干净的 commit |
| **Rebase and merge** | 保留所有 commit，线性历史 | 每个 commit 都有意义、粒度合适时 |
| **Create a merge commit** | 保留所有 commit + 加 merge commit | 大项目（如 Linux），保留完整分支拓扑 |

**为什么大多数项目用 Squash and merge：**

```
贡献者的分支历史可能是：
  "fix bug" → "fix typo" → "oops" → "actually fix" → "add test"

Squash 后只剩：
  "fix(calculator): support negative exponents (#4)"

维护者只关心"这个 PR 做了什么"，不关心你中间改了几次
```

### 8.4 你亲身体验的 GitHub 自动功能

| 功能 | 触发条件 | 效果 |
|------|---------|------|
| **CI 自动检查** | PR 创建时 | GitHub Actions 自动跑 `pytest`，结果挂到 PR 页面 |
| **`Fixes #3`** | PR body 里写 `Fixes #<号>` | PR 合入时 **自动关闭** 对应 Issue |
| **`(#4)`** | Squash Merge | GitHub 自动追加 PR 编号到 commit message |
| **分支删除** | `--delete-branch` | 合并后自动清理远程分支 |
| **Squash Merge** | `--squash` | 多个 commit 压成 1 个，历史干净 |

### 8.5 Commit message 里的 Co-authored-by

合并后你会看到：

```
fix(calculator): support negative exponents in power() (#4)

Co-authored-by: yydspei <yydspei@gmail.com>
```

这条 `Co-authored-by` 出现是因为 **你是同一个人**——贡献者和维护者都是 yydspanda。GitHub 检测到 merge 操作的人和 commit 作者不同，自动加了 co-author。

**真实协作中**：

```
贡献者 KiteEater 提交代码 → Author: KiteEater
维护者 rayhpeng 点 Merge → Co-authored-by: Willem Jiang

→ 贡献者的名字在 Author 里，不会被抢走
→ 维护者 merge 时不会变成代码作者
```

### 8.6 Issue 和 PR 的编号规则

你可能会问：为什么第一个 Issue 是 #1，但 PR 是 #2？

```
GitHub 的编号规则：
  Issues 和 PR 共享同一个计数器

  #1 → Issue "divide() crashes on zero divisor"     ← 第一个
  #2 → PR "fix(calculator): handle divide by zero"   ← 第二个
  #3 → Issue "power() returns wrong result"           ← 第三个
  #4 → PR "fix(calculator): support negative..."      ← 第四个

它们不是"对应关系"，只是创建顺序。
PR #4 关联 Issue #3 是因为 body 里写了 "Fixes #3"。
```

### 8.7 GitHub 权限：谁能做什么

| 操作 | 谁能做 | 说明 |
|------|--------|------|
| 建 Issue | 任何人（public 仓库） | 维护者可以关 |
| 提 PR | 任何人 | 代码不会自动进入 |
| **Merge PR** | **只有维护者** | 你不点，代码永远进不来 |
| Push 到 main | 只有维护者 | 别人改不了你的代码 |

**所以不用担心**——别人提 PR 不等于代码进来了，必须你 Review + Merge 才行。

如果想限制，在仓库 Settings → Features 里可以关闭 Issues 功能。

### 8.8 过程中踩的坑

#### `__pycache__` 是什么

```
calculator.py  →  __pycache__/calculator.cpython-313.pyc
                  ↑ Python 3.13 编译出的字节码缓存
```

每次跑 `pytest` 时 Python 自动生成，为了下次跑更快。不应该提交到 Git，加到 `.gitignore`：

```
__pycache__/
```

#### `.venv` 和 `.gitignore`

`clone` 后 `.venv` 没了——因为它在 `.gitignore` 里，不会被 Git 追踪。需要重新创建：

```bash
uv venv .venv && uv pip install pytest
```

#### `.git/info/exclude` vs `.gitignore`

```
.gitignore        → 提交到仓库，所有人共享（如 __pycache__/）
.git/info/exclude → 只在你本地生效，不提交（如 .history/）
```

用 exclude 放你个人的 IDE 插件产生的文件，避免修改 upstream 的 `.gitignore`。

### 8.9 真实协作 vs 模拟

```
模拟（你一人）：                  真实协作（你和别人）：
  同一台电脑                        两台不同的电脑
  同一个 GitHub 账号                两个不同的 GitHub 账号
  直接建分支                        别人先 fork，再在 fork 上建分支
  gh pr create（同一个 repo）        gh pr create（从 fork → upstream）
  自己 merge 自己                   维护者 merge 你的 PR

但 Git 层面做的事情完全一样：
  都是 "分支 → PR → Review → Merge → 同步"
```

### 8.10 下一步：用真实项目练手

你现在已经走通了完整流程（两次！）。接下来可以：

1. **去 DeerFlow 提一个真实 PR**（修文档 typo、补测试、修 summarization bug）
2. **继续用 hello-collab 练**：README 里的 bug 都修完了，可以加新功能（如 `is_prime()`）

---

## 九、真实同步案例：DeerFlow upstream 更新 + 本地有未提交修改 ⭐ 实战

> 这是你在 DeerFlow 项目上亲手执行的真实操作记录。
> 场景：upstream 有新提交，但你本地也有未提交的修改（AGENTS.md、LEARNING_PATH.md 等）。
> 这是最常见的"同步上游"场景，比理想情况（本地干净）多了一步 stash。

### 9.1 问题场景

```
upstream（bytedance/deer-flow）有了 3 个新提交：
  cef42243 fix(skills): enforce allowed-tools metadata (#2626)
  2b0e62f6 [security] fix(auth): reject cross-site auth POSTs (#2740)
  1336872b fix(channels): authenticate gateway command requests (#2742)

本地 main 有未提交的修改：
  modified:   AGENTS.md
  modified:   GIT_TUTORIAL.md
  modified:   LEARNING_PATH.md
  modified:   PROJECT_STANDARDS.md
  ...
  untracked:  AI_ENGINEER_METHODLOGY.md
  untracked:  backend/tests/test_my_learning.py
```

**直接 rebase 会怎样？** 可能有冲突，而且未提交的修改会让 rebase 过程更混乱。

### 9.2 解决方案：stash → rebase → stash pop

```bash
# 第 0 步：安全检查——确保 upstream push 被禁用
git remote set-url --push upstream no_push
# 验证
git remote -v
# upstream  git@github.com:bytedance/deer-flow.git (fetch)
# upstream  no_push (push)  ← 安全保护

# 第 1 步：拉取上游最新
git fetch upstream
# From github.com:bytedance/deer-flow
#    4ead2c6b..cef42243  main  -> upstream/main

# 第 2 步：看看上游更新了什么
git log HEAD..upstream/main --oneline
# cef42243 fix(skills): enforce allowed-tools metadata (#2626)
# 2b0e62f6 [security] fix(auth): reject cross-site auth POSTs (#2740)
# 1336872b fix(channels): authenticate gateway command requests (#2742)

# 看改了哪些文件
git diff HEAD..upstream/main --stat
# 注意：upstream 删了 AGENTS.md、GIT_TUTORIAL.md、LEARNING_PATH.md 等文件
# 但你本地有修改，这就是为什么需要 stash

# 第 3 步：暂存你的本地修改
git stash push -m "local learning notes before upstream sync"
# Saved working directory and index state...

# 第 4 步：rebase（此时工作区是干净的，不会冲突）
git rebase upstream/main
# Successfully rebased and updated refs/main.

# 第 5 步：恢复你的修改
git stash pop
# 恢复了所有 modified 和 untracked 文件

# 第 6 步：验证
git log --oneline -5
# b6099346 add learning docs: tutorials, guides, and study notes  ← 你的提交
# cef42243 fix(skills): enforce allowed-tools metadata (#2626)     ← upstream 新
# 2b0e62f6 [security] fix(auth): reject cross-site auth POSTs (#2740)
# 1336872b fix(channels): authenticate gateway command requests (#2742)
# 4ead2c6b fix(config): reset config-backed singletons on hot reload (#2588)
```

### 9.3 完整流程图

```
你有本地修改的情况：              本地干净的情况（更简单）：

git stash push                    git fetch upstream
        ↓                                ↓
git fetch upstream                 git log HEAD..upstream/main
        ↓                                ↓
git log HEAD..upstream/main        git rebase upstream/main
        ↓                                ↓
git rebase upstream/main           git push --force-with-lease
        ↓
git stash pop
        ↓
git push --force-with-lease
```

### 9.4 关键知识点

#### stash 是什么

`git stash` 把你的工作区修改（包括已修改和暂存的文件）临时"藏起来"，让工作区变干净。

```bash
# 藏起来
git stash push -m "描述信息"

# 看藏了什么
git stash list
# stash@{0}: On main: local learning notes before upstream sync

# 恢复并删除 stash
git stash pop

# 恢复但保留 stash（可以多次应用）
git stash apply

# 删除 stash
git stash drop stash@{0}
```

#### 为什么 stash → rebase → pop 而不是直接 rebase

```
直接 rebase 的问题：
  1. 未提交的修改可能导致 rebase 冲突更难处理
  2. 如果 rebase 过程中修改的文件也被 upstream 改了，Git 不知道怎么处理
  3. 最坏情况：rebase 中断，你的修改和工作区都乱了

stash → rebase → pop 的好处：
  1. rebase 在干净的工作区上执行，不容易出问题
  2. stash pop 时如果冲突，只需要处理你本地修改和 upstream 的冲突
  3. 搞砸了随时 git stash drop，重新来过
```

#### `no_push` 安全保护

```bash
# 设置（只需一次）
git remote set-url --push upstream no_push

# 效果：任何时候尝试 push 到 upstream 都会报错
git push upstream main
# fatal: 'no_push' does not appear to be a git repository
```

这是防止误操作的最佳实践。你没有权限 push 到 upstream，但设置 `no_push` 可以让报错信息更明确，避免浪费时间等待 GitHub 认证。

### 9.5 这个模式适用于所有 Fork 项目

```
DeerFlow：   upstream = bytedance/deer-flow，  origin = yydspanda/deer-flow
PageIndex：  upstream = VectifyAI/PageIndex，   origin = yydspanda/PageIndex
任何 Fork：  upstream = 原作者/原项目，          origin = 你的用户名/项目
```

同步步骤完全一样：`fetch → 看看 → stash（如有修改）→ rebase → pop → push`

---

## 十、Fork 项目源码学习工作流 ⭐ 可复用到任何项目

> 这一节解决一个实际问题：fork 了别人的项目想深度学习源码，需要在源文件上加中文注释、改代码实验，
> 但 upstream 也在持续更新，rebase 时总是冲突怎么办？
>
> 适用场景：学习任何开源项目源码（DeerFlow、LangChain、FastAPI……都可以用这套）。

### 10.1 问题本质

```
你想做的：  在 upstream 源码上加注释、改代码实验
upstream：   也在持续更新，改的就是你注释过的文件

直接在 main 上加注释 + rebase upstream：
  → upstream 改了你注释的那几行 → 冲突
  → 每次同步都要手动解冲突 → 很烦，最后放弃同步
```

### 10.2 解决方案：三件套

```
main            ← upstream 镜像，永远不自己 commit
<你的分支名>    ← 注释分支，rebase 到 main
.notes/         ← 在 .git/info/exclude 里，零冲突（可选）
```

```
upstream/main:  A ← B ← C ← D ← E ← F
                                ↑
main:           A ← B ← C ← D ← E ← F    (git reset --hard upstream/main)
                                ↑
learning:                        X' ← Y'  (git rebase main，注释在最新代码之上)
```

**为什么不用 merge 而用 rebase：**
- merge：每次同步产生一个 merge commit，历史越来越乱
- rebase：历史永远是一条直线，干净清晰

### 10.3 一次性设置（新项目复制这套操作）

```bash
# ====== 第 1 步：确保 remote 配置正确 ======
git remote add upstream <上游仓库地址>          # 如果还没加的话
git remote set-url --push upstream no_push      # 禁止误推到 upstream

# ====== 第 2 步：创建你的注释分支 ======
git checkout -b learning    # 分支名随意：learning、my-study、yyds-learning 都行

# ====== 第 3 步：把 main 设为 upstream 镜像 ======
git checkout main
git fetch upstream
git reset --hard upstream/main
git push --force-with-lease origin main

# ====== 第 4 步（可选）：把笔记目录加入 exclude ======
echo ".notes/" >> .git/info/exclude
# 这样 .notes/ 下的文件不被 Git 追踪，永远不会冲突

# ====== 第 5 步：回到注释分支，开始学习 ======
git checkout learning
```

### 10.4 日常同步（每次 upstream 更新时）

```bash
# ====== 同步 main（两条命令）======
git checkout main
git fetch upstream && git reset --hard upstream/main && git push --force-with-lease origin main

# ====== 更新你的注释分支（三条命令）======
git checkout learning

# 如果有未提交的修改，先藏起来
git stash push -u -m "wip before sync"

# rebase 到最新 main
git rebase main
# ↑ 如果冲突：手动解决 → git add → git rebase --continue
# ↑ 如果搞砸了：git rebase --abort

# 恢复未提交的修改
git stash pop

# 推送到你的 fork
git push --force-with-lease origin learning
```

**口诀：sync main → rebase learning → push**

### 10.5 冲突处理技巧

rebase 时的冲突模式很固定：

```python
<<<<<<< HEAD (upstream 最新代码)
    def _make_lead_agent(config: RunnableConfig, *, app_config: AppConfig):
        # upstream 改了函数签名
=======
    # 你的中文注释：这是组装 Agent 的核心函数
    def _make_lead_agent(config: RunnableConfig):
>>>>>>> 你的注释
```

**解决原则：两边都保留——保留 upstream 的代码改动 + 保留你的注释**

```python
    # 你的中文注释：这是组装 Agent 的核心函数
    def _make_lead_agent(config: RunnableConfig, *, app_config: AppConfig):
        # upstream 改了函数签名
```

### 10.6 降低冲突的策略

1. **注释写在函数/类上方**，不要插在代码行之间——upstream 改函数体的概率远大于改函数签名
2. **注释里不要写行号**——每加一条注释，后面的行号就全部偏移。下次 rebase 后行号就对不上了。用函数名/变量名定位代替行号
3. **学完一个文件后，注释可以删掉**——注释是给自己看的，不是永久的
4. **系统化笔记放 `.notes/`**——在 `.git/info/exclude` 里，零冲突
5. **实验性代码改完就提交**——不要在工作区积攒太多修改，容易冲突

#### 10.6.1 反面教材：行号引用问题

```python
# ❌ 错误写法：写了行号，加注释后行号全变了
"""yyds: 真正的组装车间。6 步把 Agent 组装出来：
  ① 提取运行时参数（352-359 行）    ← 这个行号现在已经是错的
  ② 解析模型名（367 行）            ← 这个也错了
"""

# ✅ 正确写法：用描述定位，不写行号
"""yyds: 真正的组装车间。6 步把 Agent 组装出来：
  ① 提取运行时参数（变量 thinking_enabled, is_plan_mode 等）
  ② 解析模型名（调 _resolve_model_name）
"""
```

### 10.7 真实案例：DeerFlow 项目

```bash
# 项目结构
upstream = bytedance/deer-flow
origin   = yydspanda/deer-flow
分支      = yyds-learning

# main 是 upstream 镜像（2025-05-07 同步）
$ git log --oneline main -3
6c220a9a fix(chat): prevent first user message from being swallowed...
daa3ffc2 feat(loop-detection): make loop detection configurable...
27559f36 fix(frontend): defer thread id to onStart to avoid 404...

# yyds-learning 在 main 之上加了自己的 commit
$ git log --oneline yyds-learning -3
ae81fd5c add learning docs: tutorials, guides, and study notes   ← 我的
6c220a9a fix(chat): prevent first user message from being swallowed...  ← upstream
daa3ffc2 feat(loop-detection): make loop detection configurable...

# .notes/ 在 .git/info/exclude 里，所有分支都能看到，零冲突
```

### 10.8 复用到其他项目的清单

```
新项目到手，复制这套操作：

□ git clone <你的fork>
□ git remote add upstream <上游>
□ git remote set-url --push upstream no_push
□ git checkout -b learning          # 或你喜欢的名字
□ echo ".notes/" >> .git/info/exclude
□ git checkout main && git fetch upstream && git reset --hard upstream/main
□ git push --force-with-lease origin main
□ git checkout learning
□ 开始学源码，加注释，提交到 learning 分支
```

### 10.9 源码注释规范（yyds 风格）⭐ 四层注释法

> 这套注释风格从 `sandbox_audit_middleware.py` 总结而来，
> 已在整个 DeerFlow 项目的 ~160 个 .py 文件中统一使用。
> 目的：让 3 个月后的自己能快速回忆"这个文件做什么、为什么这么设计、执行流程是什么"。

#### 四层注释结构

```
第一层：文件顶部 docstring（做什么/为什么/位置/关键设计）
第二层：类 docstring（执行时机 + 数据流图）
第三层：函数体内步骤标注（# yyds: ①②③）
第四层：正则/常量行内注释（# yyds: 简短说明）
```

#### 第一层：文件顶部 docstring

**位置**：文件第 1 行（import 之前）
**格式**：`"""yyds: 一句话标题 — 详细说明。`
**必填四项**：`【做什么】` `【为什么存在】` `【在链中的位置】` `【关键设计】`

```python
"""yyds: 沙箱审计中间件 — 对 bash 工具执行的安全审计和命令拦截。

【做什么】拦截所有 bash 工具调用，对 shell 命令进行安全分类（block/warn/pass），
   阻止高危命令执行，对中危命令追加警告，并记录所有 bash 调用的结构化审计日志。
【为什么存在】Agent 拥有执行 shell 命令的能力，存在安全风险。如果模型被诱导执行
   "rm -rf /" 或 "curl ... | bash" 等破坏性命令，会造成严重后果。此中间件是安全防线。
【在链中的位置】wrap_tool_call 阶段执行，包裹 bash 工具的调用过程，在命令实际执行前拦截。
【关键设计】
   - 命令分类策略：
     - 高危（block）：rm -rf /、curl|bash、dd if=、mkfs、fork bomb、LD_PRELOAD、/dev/tcp 等，
       直接阻止执行，返回错误 ToolMessage。
     - 中危（warn）：pip install、apt install、chmod 777、sudo/su、PATH= 等，
       正常执行但在结果中追加警告文本，提醒模型注意。
     - 安全（pass）：正常执行。
   - 输入清洗：拒绝空命令、超长命令（>10000字符）、包含 null 字节的命令。
   - 支持复合命令拆分（以 &&、||、; 分隔），对每个子命令独立分类，取最严重结果。
   - 使用 shlex 解析 + 正则匹配双保险，即使引号未闭合也能安全处理。
   - 审计日志为结构化 JSON，包含时间戳、线程ID、命令内容、分类结果，写入 langgraph.log。
   - 同时覆盖同步（wrap_tool_call）和异步（awrap_tool_call）两条调用路径。
"""
```

**要点**：
- 第一行是"一句话标题"，用 `—` 连接补充说明
- `【做什么】` 写功能（是什么）
- `【为什么存在】` 写设计动机（为什么不删掉这个文件）
- `【在链中的位置】` 写执行时机（哪个阶段、在什么之前/之后）
- `【关键设计】` 列出最重要的 3-5 个设计决策，用 `- ` 列表

#### 第二层：类 docstring

**位置**：`class Xxx:` 下方第一行
**格式**：`"""yyds: 类名中文说明 — 一句话概括。`
**必填三项**：`执行时机` + `操作模式` + `数据流图`

```python
class SandboxAuditMiddleware(AgentMiddleware[ThreadState]):
    """yyds: 沙箱审计中间件 — bash 命令的安全门卫。

    执行时机：wrap_tool_call（包裹 bash 工具的调用）
    操作模式：wrap_tool_call（精确拦截）— 和 DanglingToolCall 的 wrap_model_call 不同，
      这里拦截的是"工具调用"而非"模型调用"。在命令实际执行前拦截。

    只拦截 name="bash" 的工具调用，其他工具直接放行。

    三级分类：
      block（高危）：rm -rf /、curl|bash、dd、mkfs、fork bomb 等 → 不调用 handler，直接返回错误 ToolMessage
      warn（中危）：pip install、sudo、chmod 777 等 → 调用 handler 执行，但追加警告到结果
      pass（安全）：正常执行，原样返回 handler 结果

    数据流：
      bash 工具调用 → _pre_process()
                       ├─ _validate_input()  → 拒绝空/null/超长
                       ├─ _classify_command() → 两遍扫描分类
                       └─ _write_audit()      → 结构化日志
      然后：
        block → _build_block_message()（不执行 handler）
        warn  → handler() + _append_warn_to_result()（执行但追加警告）
        pass  → handler()（正常执行）
    """
```

**要点**：
- **数据流图**是最重要的部分——用 `→` 和 `├─ └─` 画出数据流转路径
- 写清楚"这个类和其他类的区别"（如"和 DanglingToolCall 的 wrap_model_call 不同"）
- 用缩进对齐，看起来像流程图

#### 第三层：函数体内步骤标注

**位置**：函数体的关键步骤前
**格式**：`# yyds: ① 步骤描述` `# yyds: ② 步骤描述`
**规则**：步骤号用圆圈数字（①②③④⑤），顺序递增

```python
def _pre_process(self, request: ToolCallRequest) -> tuple[str, str | None, str, str | None]:
    """yyds: 预处理 — 从 ToolCallRequest 提取命令，清洗+分类+写审计日志。

    yyds 执行顺序：
      ① 从 request.tool_call["args"]["command"] 提取命令字符串
         - None 或非字符串 → 当作空字符串处理（后续 _validate_input 会拦截）
      ② 输入清洗 _validate_input → 不通过则直接 block，写审计日志（truncate=True）
      ③ 命令分类 _classify_command → 两遍扫描，得到 verdict
      ④ 写审计日志（正常命令不截断）
      ⑤ block/warn 级别额外写 logger.warning

    返回 (command, thread_id, verdict, reject_reason)：
      - reject_reason 非 None → 输入清洗阶段被拒绝
      - reject_reason 为 None → 正常分类结果在 verdict 里
    """
    # yyds: ① 提取命令
    args = request.tool_call.get("args", {})
    raw_command = args.get("command")
    command = raw_command if isinstance(raw_command, str) else ""

    # yyds: ② 输入清洗 — 拒绝空/null/超长，不通过直接 block
    reject_reason = self._validate_input(command)
    if reject_reason:
        self._write_audit(thread_id, command, "block", truncate=True)
        return command, thread_id, "block", reject_reason

    # yyds: ③ 命令分类 — 两遍扫描（整条+拆分）
    verdict = _classify_command(command)

    # yyds: ④ 写审计日志
    self._write_audit(thread_id, command, verdict)

    # yyds: ⑤ 额外 warning 日志
    if verdict == "block":
        logger.warning("[SandboxAudit] BLOCKED ...")
    return command, thread_id, verdict, None
```

**要点**：
- 函数的 **docstring 里也写执行顺序**（用 ①②③ 列出），和函数体内的 `# yyds:` 一一对应
- docstring 里写"为什么"（设计理由），行内注释写"做什么"（当前步骤）
- 如果某步有分支逻辑（如"失败则短路返回"），在步骤描述里说明

#### 第四层：正则/常量行内注释

**位置**：正则表达式、常量定义的行尾
**格式**：`# yyds: 简短说明（这个正则拦截什么攻击/这个常量的含义）`

```python
_HIGH_RISK_PATTERNS: list[re.Pattern[str]] = [
    # yyds: 高危命令正则列表 — import 时编译一次，运行时 O(n) 逐条匹配
    #   匹配到任意一条 → 返回 "block"，阻止命令执行
    #   每条注释说明了它拦截的攻击类型
    re.compile(r"rm\s+-[^\s]*r[^\s]*\s+(/\*?|~/?\*?|/home\b|/root\b)\s*$"),  # yyds: rm -rf 递归删除根/家目录
    re.compile(r"dd\s+if="),  # yyds: dd 磁盘覆写
    re.compile(r"mkfs"),  # yyds: 格式化文件系统
    re.compile(r"cat\s+/etc/shadow"),  # yyds: 读取影子密码文件
    re.compile(r"\|\s*(ba)?sh\b"),  # yyds: 管道注入 shell（curl|bash、echo|sh 等）
]

_MAX_COMMAND_LENGTH = 10_000  # yyds: 正常 bash 命令很少超过几百字符，10000 是远超合法用例的上限
```

**要点**：
- 正则列表顶部加一个 **块注释**，说明整体策略（编译时机、匹配方式、命中后果）
- 每条正则后面加行内注释，说明**拦截的攻击类型**（不是解释正则语法）
- 常量行内注释说明**为什么是这个值**

#### 四层注释法的核心原则

| 原则 | 说明 |
|------|------|
| **不写行号** | 行号会随着注释增加而偏移，下次 rebase 后全部失效。用函数名/变量名定位 |
| **注释写在函数/类上方** | upstream 改函数体的概率远大于改函数签名，上方注释不容易冲突 |
| **docstring 写为什么，行内注释写做什么** | docstring 是设计文档，行内注释是执行步骤 |
| **步骤号用圆圈数字** | ①②③④⑤，和普通数字区分，一眼看出是步骤标注 |
| **数据流图用 ASCII** | `→` 和 `├─ └─` 画在类 docstring 里，3 个月后一眼看懂数据流向 |
| **正则注释写攻击类型** | 不是解释正则语法，是说明"这条正则防什么攻击" |
| **注释是给自己看的** | 学完一个文件后可以删掉，不是永久文档 |

#### 快速模板

```python
"""yyds: <文件名中文说明> — <一句话概括>。

【做什么】
【为什么存在】
【在链中的位置】
【关键设计】
"""

import ...

class XxxMiddleware(AgentMiddleware[ThreadState]):
    """yyds: <类名中文说明> — <一句话概括>。

    执行时机：
    操作模式：

    数据流：
      输入 → 方法A
              ├─ 方法B → 子流程
              └─ 方法C → 子流程
      然后：
        情况1 → 路径A
        情况2 → 路径B
    """

    def _helper(self, ...):
        """yyds: <方法中文说明> — <一句话概括>。

        yyds 执行顺序：
          ① ...
          ② ...
          ③ ...
        """
        # yyds: ① ...
        ...
        # yyds: ② ...
        ...

    @override
    def wrap_tool_call(self, ...):
        """yyds: <方法中文说明>。

        yyds 执行顺序：
          ① ...
          ② ...
        """
        # yyds: ① ...
        ...
        # yyds: ② ...
        ...
```

---

## 十一、总结

### 日常同步 upstream（三件套工作流）

```bash
# 1. sync main（upstream 镜像）
git checkout main && git fetch upstream && git reset --hard upstream/main && git push --force-with-lease origin main

# 2. rebase 注释分支
git checkout learning && git rebase main && git push --force-with-lease origin learning

# 3. .notes/ 不用管，永远零冲突
```

### 四条命令走天下（本地有修改时五条）

```bash
git fetch upstream            # 拉
git log HEAD..upstream/main   # 看（上游更新了什么）
git stash push -m "msg"       # 藏（如有未提交修改）
git rebase upstream/main      # 接
git stash pop                 # 恢复（如果 stash 了）
git push --force-with-lease   # 推
```

### 核心原则

1. **永远不要在 GitHub 网页上点 Sync fork**，用本地 fetch + rebase
2. **rebase 后 push 需要 `--force-with-lease`**，不用害怕，这是正常的
3. **搞砸了不要慌**，`git reflog` 能救回来
4. **不确定就看 `git log --oneline --graph`**，图形化历史一目了然
5. **贡献者只能提 PR，不能直接改代码**——维护者不点 Merge，代码永远进不来
6. **PR 里写 `Fixes #Issue号`**，合入时 GitHub 自动关闭对应 Issue
7. **`.gitignore` 放共享忽略，`.git/info/exclude` 放个人忽略**
