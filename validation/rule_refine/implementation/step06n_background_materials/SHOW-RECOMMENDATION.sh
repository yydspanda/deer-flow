bash <<'SOC_SHOW_RECOMMENDATION'
set -euo pipefail
soc_result_dir="$HOME/sec-rule-refine-data/background-materials-check-b_1iqpsj/result"
test -f "$soc_result_dir/REPORT.md"
sed '/^## 执行摘要/,$d' "$soc_result_dir/REPORT.md"
SOC_SHOW_RECOMMENDATION
