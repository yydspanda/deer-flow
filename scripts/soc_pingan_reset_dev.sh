#!/usr/bin/env bash
# Standalone operator wrapper; database safeguards belong to the installed Host CLI.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  echo '请使用 bash 执行此脚本，不要 source。' >&2
  return 1
fi
set -euo pipefail
umask 077

usage() {
  cat <<'USAGE'
用法：bash RESET-SOC-DEV.sh [--root PATH] [--confirm RESET-SOC-DEV]

默认只预览；目标默认为 $HOME/deer-flow。
--confirm RESET-SOC-DEV  明确确认后：检查目标、停服、再次检查、归档重置。
--root PATH             指定已安装的 DeerFlow 目录。
--help                  显示说明。

执行前先暂停页面中的批量任务，等待运行中数量为 0。
会清空整个 SOC DEV 的研判、批次及经验；原数据库归档备份。
不会自动启动服务。账号、STG、原始语料和配置不在重置范围内。
USAGE
}

fail() {
  echo "错误：$*" >&2
  exit 1
}

target_root="${HOME:?HOME is required}/deer-flow"
confirmation=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail '--root 需要目录路径'
      target_root="$2"
      shift 2
      ;;
    --confirm)
      [[ $# -ge 2 && "$2" == RESET-SOC-DEV ]] || fail '确认参数必须为 --confirm RESET-SOC-DEV'
      confirmation="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *) fail "不支持的参数：$1" ;;
  esac
done

[[ -d "$target_root" ]] || fail "安装目录不存在：$target_root"
target_root="$(cd "$target_root" && pwd -P)"
host_python="$target_root/backend/.venv/bin/python"
host_script="$target_root/scripts/soc_pingan_macos_host_dev.py"
[[ -x "$host_python" && -f "$host_script" ]] || fail '目标目录缺少已安装的 Python 环境或 Host 管理脚本'
cd "$target_root"

preview_dir="$(mktemp -d "${TMPDIR:-/tmp}/soc-dev-reset.XXXXXX")"
receipt_pending=''
canonical_pending=''
cleanup() {
  local result=$?
  rm -rf "$preview_dir"
  if [[ -n "$canonical_pending" && -f "$canonical_pending" ]]; then
    rm -f "$canonical_pending"
  fi
  if [[ -n "$receipt_pending" && -f "$receipt_pending" ]]; then
    echo "未发布的重置输出已保留：$receipt_pending" >&2
    echo '请先核对该输出及数据库备份，不要因回执发布失败再次重置。' >&2
  fi
  return "$result"
}
trap cleanup EXIT

validate_report() {
  "$host_python" - "$1" "$target_root" "$2" <<'PY'
import json
import sys
from pathlib import Path

report_path, root, phase = sys.argv[1:]
try:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Host 返回值不是 JSON 对象")
    if report.get("schema_version") != "soc.pingan_dev_database_reset.v1":
        raise ValueError("不支持的重置回执版本")
    expected = Path(root) / "backend/.deer-flow/data/soc_agent_dev.db"
    database = report.get("database")
    if not isinstance(database, str) or not Path(database).is_absolute():
        raise ValueError("Host 未返回数据库绝对路径")
    if Path(database).resolve() != expected.resolve():
        raise ValueError(f"目标不匹配；只允许重置 {expected}")
    statuses = ("reset", "already_empty") if phase == "receipt" else ("preview",)
    if report.get("status") not in statuses:
        raise ValueError("Host 返回了不符合当前步骤的状态")
    if type(report.get("ready")) is not bool or not isinstance(report.get("blockers"), list):
        raise ValueError("Host 返回的 ready/blockers 格式不正确")
    if phase != "initial" and (report["ready"] is not True or report["blockers"] != []):
        raise ValueError("数据库仍不可重置；要求 ready=true 且 blockers=[]")
except (OSError, ValueError, RuntimeError) as error:
    print(f"验证失败：{error}", file=sys.stderr)
    sys.exit(1)
print(json.dumps(report, ensure_ascii=False, indent=2))
PY
}

# Verify DEV and target identity before even stopping the installed services.
echo "目标安装目录：$target_root"
"$host_python" "$host_script" reset-dev-data > "$preview_dir/initial.json"
validate_report "$preview_dir/initial.json" initial
if [[ -z "$confirmation" ]]; then
  echo '以上仅为预览，未停服、未重置。确认需要从空经验库重跑时，再加 --confirm RESET-SOC-DEV。'
  exit 0
fi

"$host_python" "$host_script" stop
"$host_python" "$host_script" reset-dev-data > "$preview_dir/stopped.json"
validate_report "$preview_dir/stopped.json" stopped

receipt_dir="$target_root/backend/.deer-flow/internal-host-dev"
history_dir="$receipt_dir/reset-receipts"
mkdir -p "$history_dir"
receipt_pending="$(mktemp "$history_dir/reset-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
"$host_python" "$host_script" reset-dev-data --confirm RESET-SOC-DEV > "$receipt_pending"
validate_report "$receipt_pending" receipt

canonical_receipt="$receipt_dir/dev-reset-receipt.json"
canonical_pending="$(mktemp "$canonical_receipt.XXXXXX")"
cp "$receipt_pending" "$canonical_pending"
history_receipt="$receipt_pending.json"
mv "$receipt_pending" "$history_receipt"
receipt_pending=''
echo "本次历史回执：$history_receipt"
mv -f "$canonical_pending" "$canonical_receipt"
canonical_pending=''
echo "最新回执：$canonical_receipt"
echo '重置完成，服务保持停止。请按说明单独启动；普通重启或启动失败不需要再次重置。'
