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

## 七、总结

### 三条命令走天下

```bash
git fetch upstream            # 拉
git rebase upstream/main      # 接
git push                      # 推
```

### 核心原则

1. **永远不要在 GitHub 网页上点 Sync fork**，用本地 fetch + rebase
2. **rebase 后 push 需要 `--force-with-lease`**，不用害怕，这是正常的
3. **搞砸了不要慌**，`git reflog` 能救回来
4. **不确定就看 `git log --oneline --graph`**，图形化历史一目了然
