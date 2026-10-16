# 内网 Mac：从空 SOC DEV 数据库重新开始

适用于明确需要从空 SOC DEV 数据库开始的情况。
如果只是第一批运行选项选错，新版页面已经支持 **重新配置并全部重跑**，优先使用
[页面重跑流程](soc-validation-restart.md)，无需清理数据库。
**会重置整个 SOC DEV 数据库**：所有批次、研判记录、经验候选和已审核经验都会从当前运行库移出，不仅是最近一批。
默认归档备份原数据库及 SQLite 附属文件；明确选择 `--no-backup` 时直接清空，不创建备份。

保留登录账号库、STG 数据库、原始 `.pkl` 及语料索引/载荷文件、配置、私钥和并发设置。
不需要重新传语料，不需要运行 `INSTALL-PINGAN-MAC.sh`。
第一批完成后继续第二批、普通服务重启、升级部署，都不要执行此重置。
只想重跑第二批并保留第一批经验时，参照已安装项目中的 `docs/soc-validation-reset.md`。

## 文件准备（一次）

将以下四个文件放到内网 Mac 的 `$HOME/READY-TO-TRANSFER/`：

- `RESET-SOC-DEV.sh`
- `RESET-SOC-DEV-README.md`（本说明）
- `RESET-SOC-DEV-SOURCE.json`（工具来源与本地验证记录）
- `RESET-SOC-DEV-SHA256SUMS`

校验传输是否完整：

```bash
cd "$HOME/READY-TO-TRANSFER"
shasum -a 256 -c RESET-SOC-DEV-SHA256SUMS
```

三项均应显示 `OK`。脚本可独立存放，调用已安装项目中的 Host 管理命令，不会覆盖项目代码或安装依赖。
要求已有的安装提供 `reset-dev-data` 命令及 `backend/.venv/bin/python`。
使用不备份模式前，确认安装版本支持该选项（只传新版 wrapper 不会更新 Host）：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py reset-dev-data --help
```

帮助中须有 `--no-backup`，否则先安装包含此功能的新版源码包。
仓库中的维护版本是 `scripts/soc_pingan_reset_dev.sh`，也可直接用 `bash` 执行。

## 每次需要重新开始时

### 1. 暂停任务并预览

在页面暂停批量任务，等待运行中数量为 **0**。然后执行：

```bash
bash "$HOME/READY-TO-TRANSFER/RESET-SOC-DEV.sh"
```

默认项目目录是 `$HOME/deer-flow`。确认输出 `database` 指向该项目的
`backend/.deer-flow/data/soc_agent_dev.db`。
此步只预览，服务尚在运行时出现 `ready=false` 和服务占用提示是正常的。
STG、目标不匹配或回执不合法会直接退出。

### 2. 选择是否备份，再确认重置

以下两种只执行一种。确认要清空当前全部 DEV 研判与经验、重新开始后执行。

**A. 备份后重置（默认）：**

```bash
bash "$HOME/READY-TO-TRANSFER/RESET-SOC-DEV.sh" --confirm RESET-SOC-DEV
```

**B. 不备份，直接重置：** 不压缩、不复制、不计算整库哈希；不能通过本次重置恢复。

```bash
bash "$HOME/READY-TO-TRANSFER/RESET-SOC-DEV.sh" --no-backup
bash "$HOME/READY-TO-TRANSFER/RESET-SOC-DEV.sh" --no-backup --confirm RESET-SOC-DEV
```

选择B时，第1节预览也可加 `--no-backup`，避免读取整库计算哈希。
如已在部署教程中备份整个项目，`deer-flow-backups` 压缩包已包含数据库，可选B避免再次备份。
项目压缩备份和数据库重置备份独立选择；两者都跳过时，本次不会保留恢复副本。

脚本先核对目标，再停服、重新预览。只有 `ready=true` 且 `blockers=[]` 才继续重置。
实际操作由已安装的 Host 命令执行，两种模式均保留维护锁和进程占用检查；备份模式额外校验归档。
脚本成功时输出 `status: reset`（没有活动数据库时为 `already_empty`）以及回执路径，**不会自动启动**。

若提示仍有占用，先解决输出中的具体阻塞原因；不要手动删除数据库或绕过检查。
若已有 `reset` 成功输出、只是后续回执保存失败，先保留终端显示的文件和备份路径，不要重复重置。

### 3. 单独启动服务

沿用当前内网可信 DEV 演示的启动方式：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py status
BASH
```

如果平时启用登录认证，启动时去掉 `--demo-no-auth`，保持原来的认证方式。
普通启动会初始化空数据库结构，无需手动建表。

若启动失败或端口仍被占用，**不用再次重置，也不用重新安装**。
先运行已安装 Host 的 `stop`，核对具体占用，再重新执行本步骤的启动命令。

### 4. 确认后重新跑第一批

刷新工作台，确认新一轮已处理数、经验候选和已审核经验均为空；原始语料数量仍在。
核对最大并发仍为 **16**，设置本轮所需的语义核对等运行选项，再启动第一批。
第一批积累并审核经验后直接继续第二批，不要再运行重置命令。

## 备份与回执

路径均相对于目标项目目录：

| 文件/目录 | 用途 |
| --- | --- |
| `backend/.deer-flow/data/soc-dev-reset-backups/<时间戳>-<标识>/` | 原 DEV 数据库、附属文件及哈希清单；以回执中的 `backup_directory` 为准 |
| `backend/.deer-flow/internal-host-dev/dev-reset-receipt.json` | 最近一次成功重置回执 |
| `backend/.deer-flow/internal-host-dev/reset-receipts/*.json` | 每次成功重置的独立回执，保留之前的备份位置 |

不备份模式回执为 `backup_enabled=false`、`backup_directory=null`、`recovery=unavailable_no_backup`。
它不会清理以前的备份；备份模式仍记录实际的 `backup_directory`。
`already_empty` 不产生新的数据库备份，之前的备份和历史回执仍保留。
重置失败不会覆盖上次成功回执。
恢复旧库应使用 Host 的 `restore-dev-data` 受控流程；不要向正在运行的新库直接覆盖备份文件。

## 项目不在默认目录时

预览和确认都显式指定同一个安装目录，例如：

```bash
bash "$HOME/READY-TO-TRANSFER/RESET-SOC-DEV.sh" --root "/实际路径/deer-flow"
bash "$HOME/READY-TO-TRANSFER/RESET-SOC-DEV.sh" --root "/实际路径/deer-flow" --confirm RESET-SOC-DEV
```

启动步骤中的 `cd` 也应改为该目录。请用 `bash` 执行脚本，不要 `source`。
