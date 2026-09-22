# 内网 Mac：保留第一批，清理并重跑第二批

需要重新配置并重跑第二批，请使用[网页全部重跑流程](soc-validation-restart.md)，保留全部历史。下面仅为另行明确授权删除历史时的离线维护文档；当前重跑需求不执行本流程。

本流程用于操作人已明确决定丢弃第二批全部旧结果的情况，包括成功、失败、历次重跑和排队任务。第一批结果、候选经验和已审核经验保留。普通升级、暂停和继续不执行此流程。

先按交付包的 `PINGAN-INTERNAL-MAC-UPGRADE-RUNBOOK.md` 完成**保留数据升级**，确认仓库包含 `scripts/soc_pingan_validation_database.py` 和 `backend/scripts/soc_validation_reset_store.py`。不要使用完全初始化手册；旧交付包没有下面的新维护命令。

## 1. 暂停并停服

在网页暂停第二批，等运行中变为 0。`configuration_changed` 且运行中为 0 时可以直接停服。关闭同事和本机的演练页面，以及打开数据库的编辑器连接。

在 VS Code 的终端执行：

```bash
cd "$HOME/deer-flow"
python3.12 scripts/soc_pingan_macos_host_dev.py stop
```

从两批配置的只读诊断报告中取同一个 `experiment_id`。以下命令只询问这个 ID，原样粘贴诊断里的值后按回车：

```bash
read -r 'SOC_VALIDATION_EXPERIMENT?请输入诊断中的 experiment_id：'
export SOC_VALIDATION_EXPERIMENT
```

上面的输入命令适用于 Mac 默认的 zsh。若当前使用 bash，改用 `read -r -p '请输入诊断中的 experiment_id：' SOC_VALIDATION_EXPERIMENT`，然后执行同一条 export。

## 2. 预览清理范围

```bash
python3.12 scripts/soc_pingan_validation_database.py \
  --experiment "$SOC_VALIDATION_EXPERIMENT"
```

这是只读检查。确认 `validation_members` 为第二批唯一告警数，`first_batch_options` 与第一批实际设置相同。`job_counts_including_history` 包含历次重跑，可能大于网页按唯一告警显示的数量。

工具检查 DEV 环境、schema `0032_corpus_revision_index`、进程和文件占用、任务归属、第一批配置及其他记录的引用。任何未完成任务、未明确选择的不同第一批配置、共享引用或未知状态都会停止清理。请保留错误输出供核对，不要绕过检查或手工 DELETE。

若第一批早期试跑与后续正式积累的配置不同，在预览和 `--apply` 两条命令中都加 `--learning-round`，指定已经核实的第一批轮次 ID。工具读取该轮次完整设置，并检查它属于当前实验的第一批且有完成记录；不会修改该轮次、其他第一批历史或审核经验。没有指定时仍要求第一批历史配置一致，不会擅自按多数或最新轮次选择。

已停服但需要新版维护工具时，可以从仓库外运行交付的独立维护工具：命令使用外部工具的 `scripts/soc_pingan_validation_database.py`，并加 `--root "$HOME/deer-flow"`。它加载工具自身的配套 SQL 模块，只对目标 DEV 数据库执行本教程的维护操作；不把工具文件覆盖进应用仓库，也不需要重新部署应用。此时第 2、3 步必须都使用该外部工具及同一个基准参数；清理成功后，第 4、5 步仍在原应用仓库执行。

## 3. 完整备份后清理第二批

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
mkdir -p backend/.deer-flow/soc-internal-validation
python3.12 scripts/soc_pingan_validation_database.py \
  --experiment "$SOC_VALIDATION_EXPERIMENT" \
  --apply \
  > backend/.deer-flow/soc-internal-validation/validation-reset.json
cat backend/.deer-flow/soc-internal-validation/validation-reset.json
BASH
```

看到 `"status": "applied"` 才表示完成。备份位置记录在 `backup_directory`，包含完整数据库及存在的 SQLite 附属文件，SHA256 校验通过后才开始删除。约 10 GiB 的数据库需要至少同等备份空间，还需为清理事务日志留出额外空间；备份、校验和关联检查可能持续几分钟。复制时会显示阶段和进度。

清理在一次事务中完成，异常会回滚，完整备份保留。数据库文件大小可能暂时不变，空闲页会被后续写入复用；不需要 VACUUM，也不切换日志模式。请保留这份备份，至少等第二批重跑验收完成。

## 4. 启动服务

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
python3.12 scripts/soc_pingan_macos_host_dev.py status
BASH
```

本节适用于原本使用 `--demo-no-auth` 的可信内网 DEV。其他认证部署应沿用原来的启动参数和认证方式。

## 5. 按第一批完整设置创建全量第二批

在部署 Mac 上执行下面一块。它读取第 3 步保存的报告，明确提交第一批全部设置，冻结当前已审核并开放的经验，并使用服务器保存的并发值。创建请求使用固定去重键；网络中断后可重复执行同一块，不会重复创建任务。需要服务已经按第 4 节启动。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path
import httpx

path = Path("backend/.deer-flow/soc-internal-validation/validation-reset.json")
receipt = json.loads(path.read_text(encoding="utf-8"))
if receipt.get("status") != "applied":
    raise SystemExit("没有成功清理记录，未提交任务。")
result = receipt["result"]
options = result["first_batch_options"]
request_key = "validation-reset-" + hashlib.sha256(
    receipt["backup_directory"].encode("utf-8")
).hexdigest()
base = "http://127.0.0.1:2026/api/soc/dev/corpus-workbench"
with httpx.Client(timeout=180, trust_env=False) as client:
    config = client.get(base + "/experiments/configuration", params={"batch": "validation"})
    config.raise_for_status()
    body = {
        "experiment_id": receipt["experiment_id"],
        "selection": {"batch": "validation", "scope": "all"},
        "purpose": "full_flow" if options["tenant_policy_enabled"] else "memory",
        "options": options,
        "memory_mode": "snapshot",
        "execution_limit": 2147483647,
        "concurrency": config.json()["max_concurrency"],
    }
    response = client.post(base + "/rounds", json=body, headers={"Idempotency-Key": request_key})
    if response.is_error:
        raise SystemExit(f"创建失败 HTTP {response.status_code}: {response.text[:1500]}")
    round_id = response.json()["round_id"]
    response = client.post(base + "/rounds/" + round_id + "/start", json={})
    if response.is_error:
        raise SystemExit(f"启动失败 HTTP {response.status_code}: {response.text[:1500]}")
    print("第二批已提交：", round_id)
    print("使用设置：", json.dumps(options, ensure_ascii=False))
    print("请刷新告警演练页面，查看第二批顶部进度。")
PY
```

若没有可用的已审核经验，创建会停止并提示先审核；不要切换成无经验执行来绕过。界面刷新后显示服务器保存的新设置；同事可以继续使用这些设置运行和暂停，只有部署 Mac 能修改。

确认第一批完成数及审核经验保留，第二批从新的任务开始计数。到这里不再重复第 3 步；之后暂停、继续都在网页操作。运行配置一致不代表模型输出逐字相同，第二批仍使用当前版本和当前审核经验。

## 失败处理与恢复

预览/备份失败不会清理原库；事务失败会回滚。保留错误及完整备份目录，先处理对应原因。

如果清理已成功但需要恢复旧第二批，先停服；使用现有 Host DEV `reset-dev-data` 将当前库独立归档，然后使用 `restore-dev-data` 恢复本次报告里的 `backup_directory`。现有恢复命令拒绝覆盖仍存在的数据库，请按其 `--help` 执行。恢复会回到整库备份时间点，因此之后产生的所有变化都属于另一个归档。不要覆盖或删除数据库的 `-wal`、`-shm`、`-journal` 文件。
