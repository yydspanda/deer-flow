# Adapter Field Audits

## 常规语义核对接入验证

外网验证当前默认 `globalai-deepseek-v4.1-flash`；本机配置保留 V4 Flash/Pro，内网配置不改。
`normalization_workbench_review.py --confirm-live --alert-ids ... --output <新目录>`
通过既有 DEV API 真跑核对与主研判，再离线合入同一份冻结建议，比较 Adapter v5、Adapter v6
与补充后 v6 的特征，检查完整模型投影。网页保持 shadow，不改旧分组/经验；主研判真实调用
但没有使用这些 shadow 建议，不能把该 verdict 当成 apply 质量。使用 `--collect-only
--collection-id <新名称>` 只读收集，不新增模型调用。读取完整 DB Run 校验哈希，不能使用
去空值的 Web DTO 重建请求；新 Run 必须有新 run_id 且后台任务完成，避免把旧结果当新结果。

`normalization_runtime_review.py --capture-output` 可在隔离诊断目录保存可见回答正文；
不保存 reasoning、鉴权头，也不在日志/错误摘要中回显正文。输出目录受保护、忽略 Git。
源文件摘要在调用前记录；超时从 `SOC_LLM_CALL_TIMEOUT_SECONDS` 读取（缺省 270 秒）。

`normalization_runtime_review.py` 使用正式 Runtime 的新核对节点，只调用一次真实补充模型。
v2 增加 `03-output-schema.json`（当前输出契约）和 `06-matching-features.json`
（Profile 8 / v6 的实际特征投影）。`04-review-result.json` 的 `observation_changes`
记录对象/检测/补充事实前后值，`model_input_status` 表示是否进入真实构建的模型输入。
本次实现只做离线回归；历史 v1 模型产物不代表 v2 质量。脚本仍需 `--confirm-live`，
不会写运营 Memory 或自动替换工作台分组。
后续主研判显式使用 stub，仅验证链路，不把其 verdict 当作模型质量结果；不写运营库或 Memory。
输入、Prompt、核对结果、完整 Run、journal 和指标保存到新建的受保护目录。首批仅主证据和
九类主机/进程/文件/账号字段，补充消息及指纹消费未验收；不是所有无指纹组的修复完成证明。

```bash
backend/.venv/bin/python validation/compact_zeus/audits/normalization_runtime_review.py \
  --confirm-live --alert-id 2448412 \
  --output backend/.deer-flow/soc-validation/normalization-runtime-review-NEW
```

不覆盖已有目录、不自动重试或增加输出预算；默认不覆盖模型的 max_tokens 配置。
09-14 首次真实调用返回 length/8192，报告 failed；只有原有路径完成，不能记为补充成功。

该目录对统一 corpus 中各 PingAn topic 做批量字段流向审计，回答“解析出的字段进入了
canonical provenance、fact、scenario、LLM evidence 中的哪一条通道”。它用于发现覆盖
缺口和错误语义，不是检测准确率评测，也不会修改 Runtime 决策。

当前审计组：

- `build_pingan_nids_field_audit.py`：NIDS 字段、五元组、HTTP 与编码压缩。
- `build_pingan_edr_field_audit.py`：EDR endpoint/process/file/hash 语义。
- `build_pingan_ndr_hids_field_audit.py`：NDR/APT 与 HIDS message-first 字段流向。
- `build_pingan_ti_siem_field_audit.py`：Threat Intel 与可信 SIEM subtype。

输出写入 `validation/compact_zeus/data/audits/`，包含内部语料统计或派生信息，已 Git
忽略。对应代表样本由相邻的 `reviews/` 构建。

```bash
backend/.venv/bin/python -m pytest -q validation/compact_zeus/audits
```

## 单消息事实补充试验

`normalization_assistance_trial.py` 是隔离的可行性试验，不是在线 Adapter 或自动触发器。
从正文 SQLite 只读提取一个告警，保留旧 v2 KV matcher 的残留对照，再通过既有 DeerFlow SOC
client 做一次真实事实抽取，复用生产实体/事实/Memory facets 代码比较补充前后。
首版只支持一条 message、五个主机/进程/文件目标，不支持多事件合并或全语义覆盖。
输出预算使用显式 `--max-tokens`（当前试验默认 8192），不自动递增或重试；
这是隔离试验参数，不修改线上全局模型配置。输入 Token 不占此生成额度，不能只按最终 JSON 大小估算预算。

```bash
backend/.venv/bin/python validation/compact_zeus/audits/normalization_assistance_trial.py \
  --alert-id 2448412 \
  --model globalai-deepseek-v4-flash-0731 \
  --max-tokens 8192 \
  --output backend/.deer-flow/soc-validation/normalization-assist-2448412-new-attempt \
  --confirm-live
```

输出目录必须是新的。`01` 原文、`02` Adapter、`03` 检查、`04/05` 模型请求/返回、
`06/07` 来源校验与采纳事实、`08/09` 生产消费者前后结果、`10` 对比、`11` 模型/版本/用量清单。
内部数据均为忽略提交的受限文件；不会重新写 PKL、运营数据库、分组索引、Candidate 或 Memory。
没有主研判、角色复核、Memory 实际检索或 Policy 执行；有指纹不等于已证明分组准确。
此试验也不代表线上覆盖检查/归一化运维降噪已经交付。

已有响应可离线检查，不重复计费、不改写原试验文件：

```bash
backend/.venv/bin/python validation/compact_zeus/audits/normalization_assistance_trial.py \
  --saved-attempt backend/.deer-flow/soc-validation/normalization-assist-2448412-20260914-raw-text \
  --output backend/.deer-flow/soc-validation/normalization-assist-2448412-20260914-review
```

2026-09-14 两次 GlobalAI Flash 试验均请求关闭推理，但响应记录仍含推理，达到
2048/4096 输出上限，没有最终 JSON；采纳 0 条事实。失败与离线对比均保留，不声称补充成功。
同日 8192 单变量对照返回 stop，实际输出 5573 Token，五字段均采纳；产物在
`backend/.deer-flow/soc-validation/normalization-assist-2448412-20260914-8192/`。
先看 `06-validation-and-merge.json` 和 `10-comparison.json`。这证明该样本能完成抽取，
不证明线上已启用或跨样本指纹/Memory 召回准确性；方案 §13.4 已修正早先对截断的归因。
在线和离线均先检查 `finish_reason=length`；在线失败记录增加
`provider_output_truncated`，不再将输出截断只呈现为 JSON 解析错误。

### 下游字段与分组对照（不调用模型）

`normalization_assistance_consumers.py` 复用一条成功模型回答，并给其余指定真实样本提供
明确标注的原文字段测试输入，验证“即使这些字段已补好，现有消费者会怎样”。
不是生产映射器，不是 8 次成功 LLM 抽取，也不读写运营 Memory；最多选 20 个不同 ID。

```bash
backend/.venv/bin/python validation/compact_zeus/audits/normalization_assistance_consumers.py \
  --saved-attempt backend/.deer-flow/soc-validation/normalization-assist-2448412-20260914-8192 \
  --output backend/.deer-flow/soc-validation/normalization-assist-consumers-20260914
```

输出必须是新目录。先看 summary.json 的缺口，再看逐条 consumers.json 的原文、来源合并、
field_destinations、标准结构、模型输入和实际 facets；pairs.json 展示同指纹的具体差异，
manifest.json 保留版本与 Hash。2026-09-14 对照发现 5 条不同检测内容的 explorer.exe 告警
生成相同默认必需条件；其中 7 对带检测标签差异、3 对仅文件/出现上下文差异。
后者不必然要求拆组。参见方案 §13.5，不能将结果解释为已发生错误 Memory 改判。
线上补充与检查降噪尚未实现；本脚本不改变现有工作台。

### 模型通道排查

2026-09-14：真实模型工厂 + MockTransport 已确认关闭参数正确发出，SDK 不把推理填入正文。
绕过 SDK 的一次 GlobalAI 短请求仍返回 reasoning_content，说明需要排查远端兼容行为。
该探针返回正确 JSON，但 23.322 秒才完成；99 输入 / 17 输出 / 116 总 Token。
不是完整告警测试，不能证明抽取质量。更细结论记录在
`.notes/ai_soc/architecture/normalization-assistance-design.md` §13.2。

以下是同请求的复现方法，**会计费；排查时才执行一次**。不输出凭证或推理内容，
不修改模型配置；只有虚构提示词，无告警/Memory/数据库操作：

```bash
backend/.venv/bin/python - <<'PY'
import json, time
import httpx
from dotenv import load_dotenv
from deerflow.config.app_config import get_app_config
from deerflow.models.factory import create_chat_model

load_dotenv('.env')
model = create_chat_model(
    name='globalai-deepseek-v4-flash-0731', thinking_enabled=False,
    app_config=get_app_config(), attach_tracing=False,
    model_overrides={'disable_streaming': True, 'max_tokens': 256,
                     'max_retries': 0, 'timeout': 45, 'temperature': 0},
)
body = model._get_request_payload([
    {'role': 'user', 'content': '只输出一个 JSON 对象：{"ok":true}。不要解释。'}
])
body.update(body.pop('extra_body', {}) or {})
started = time.monotonic()
with httpx.Client(timeout=45) as client:
    response = client.post(
        str(model.root_client.base_url) + 'chat/completions', json=body,
        headers={'Authorization': 'Bearer ' + model.root_client.api_key},
    )
response.raise_for_status()
result = response.json()
choice = result['choices'][0]
message = choice['message']
content = message.get('content') or ''
reasoning = message.get('reasoning_content') or message.get('reasoning') or ''
try:
    expected_json = json.loads(content) == {'ok': True}
except ValueError:
    expected_json = False
print(json.dumps({
    'thinking_sent': body.get('thinking'), 'http_status': response.status_code,
    'model_returned': result.get('model'), 'finish_reason': choice.get('finish_reason'),
    'content_chars': len(content), 'reasoning_chars': len(reasoning),
    'content_equals_reasoning': bool(reasoning) and content == reasoning,
    'expected_json_returned': expected_json, 'usage': result.get('usage'),
    'duration_ms': round((time.monotonic() - started) * 1000),
}, ensure_ascii=False, indent=2))
PY
```
## Quoted-KV Parser Comparison

Run the production parser and bounded-input builder against up to 20 selected real
messages, using the frozen v2 matcher as the baseline. No model, Memory, index mutation,
or operational database write is performed. Default selection is eight AV alerts.

```bash
backend/.venv/bin/python validation/compact_zeus/audits/quoted_kv_parser_audit.py \
  --output backend/.deer-flow/soc-validation/quoted-kv-parser-review
```

The output directory must be new. `summary.json` records source/code/config hashes,
hardware, duration and changed-field visibility. Each `<alert_id>.parsing.json` contains
raw source, v2 fields, current `syntax_coverage`, and current canonical entities.
Offsets address the original string; syntax `complete=true` is not canonical or Memory
coverage. Files are private, ignored artifacts. Historical spike KV inspection is now
explicitly the frozen v2 comparison; it must not be described as current parser output.

## Real DEV Review And Frozen Consumer Comparison

`normalization_workbench_review.py` runs selected alerts through the real loopback DEV
Workbench, saving new results for frontend review. It requires the existing shadow mode.
The same frozen supplement is then merged offline through canonical consumers and the
PingAn opt-in fingerprint projector, without extra LLM calls or group/index migration.

```bash
backend/.venv/bin/python validation/compact_zeus/audits/normalization_workbench_review.py \
  --confirm-live --alert-ids 2448412 2448932 2448580 2464210 2455416 \
  --output backend/.deer-flow/soc-validation/semantic-five-new
```

This submits paid model runs and updates operational DEV results. Use a new output
directory. To collect already submitted results, reuse the directory with `--collect-only`
and a fresh `--collection-id`; do not resubmit after a report-generation error.
The DB defaults to the Docker Memory DEV SQLite; override `--database-file` for another
local deployment. Full private requests and run IDs verify the audit association.
Reports separate `adapter_v5`, `adapter_v6`, and `supplemented_v6`: a Profile-only
fingerprint is not a successful LLM extraction. `model_input_present` checks the entire
structured model projection, not only the primary raw evidence string.
Tests: `PYTHONPATH=backend:. backend/.venv/bin/pytest validation/compact_zeus/audits/test_normalization_workbench_review.py -q`.

## Saved Memory Scope Coverage

`memory_scope_coverage.py` compares saved apply-mode runs for 2457581, 2457177,
2480991 and 2488405 using historical and current production PingAn projectors.
It opens the database read-only and creates a synthetic reference scope from
2457581; it does not approve a Memory, submit an alert, or invoke a model.

```bash
backend/.venv/bin/python validation/compact_zeus/audits/memory_scope_coverage.py \
  --database backend/.deer-flow/soc-validation/memory-dev-web/soc-memory-dev.sqlite \
  --output backend/.deer-flow/soc-validation/memory-scope-remediation-20260916/saved-run-comparison.json
```

The ignored private report includes saved request hashes, old/new core features,
scope outcomes, upstream revision and command. These checks validate matching
structure, not live extraction consistency or operational risk accuracy.
# Stable semantic identities and saved facts

`normalization_identity_stability.py` opens the operator database read-only and writes
test runs only to a temporary SQLite. It compares saved LLM ID choices, identical-input
fact reuse, explicit recheck and current Memory applicability. `--live` makes one real
semantic-review call plus a cached rerun; the primary analyzer remains a Stub. A passing
report is not a primary-analysis accuracy or internal-connectivity acceptance.

```bash
backend/.venv/bin/python validation/compact_zeus/audits/normalization_identity_stability.py \
  --database backend/.deer-flow/soc-validation/memory-dev-web/soc-memory-dev.sqlite \
  --before-run RUN-38035FBF0935 --after-run RUN-CD776CCF86B2 \
  --memory-id MEM-2E214287F75A \
  --output backend/.deer-flow/soc-validation/normalization-stability-20260917/saved-choices.json
```

These IDs refer to the saved local experiment. Use reviewed IDs from the target database
for a different dataset; the script does not recreate missing records or approve Memory.
