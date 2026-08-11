# Tenant Policy Shadow Validation

该目录验证租户处置策略是在通用 SOC Runtime **之后**执行的独立影子层。它不重新跑
LLM，而是读取已经保存的真实 Runtime 结果，检查：

- 检测结论、置信度和 Runtime 决策保持不变；
- PingAn 策略只读取 canonical/analysis/authorization 通用契约；
- 命中策略后只产生运营建议，不关单、不执行动作、不写 Memory；
- 未命中时保留标准 Runtime 处置路径，不猜测平安语义。

默认使用 Checkpoint D 保存的 `1965449` 和 `1966442` 真实模型结果：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/policy/validate_tenant_policy_shadow.py
```

也可以显式提供一个或多个 Runtime JSON：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/policy/validate_tenant_policy_shadow.py \
  --artifact /path/to/example.runtime.json
```

输出默认写入
`backend/.deer-flow/soc-runtime-validation/tenant-policy-shadow/`。该目录是本地验证产物，
不会提交 Git；每个 `*.tenant-policy.json` 保留完整策略决策，`summary.json` 给出验收结论。

## PingAn EDR 安全路径快速策略 / Fast Policy

`validate_pingan_edr_safe_path_fast_policy.py` 固定选择 10 条真实 EDR 告警，通过生产
`SocAnalysisService`、隔离 SQLite、Tenant Policy observer 和 Effective Decision observer
验证安全路径快速忽略。它使用 deterministic stub，验证的是路径策略集成和留痕，不声明
LLM 研判准确率。

```bash
backend/.venv/bin/python \
  validation/compact_zeus/policy/validate_pingan_edr_safe_path_fast_policy.py
```

固定 cohort 包含 4 条应直接忽略的精确 `safe_paths`，以及 6 条必须 fail closed 的
`other_paths`、hash mismatch、未知路径和“安全+未知”混合路径。验收同时要求：

- 原始 payload 不变，Runtime verdict/confidence 不变；
- 命中样本保留 `Base -> Memory -> Tenant Policy -> Effective`，只清除 review 并形成
  `ignored` disposition；
- 未命中或混合样本没有快速忽略；
- action authorization/execution 均为 0。

当前 212 条真实 corpus 没有命中已推导路径族的新 EDR 路径，因此报告会明确记录
`real_path_family_coverage=not_present_in_selected_real_corpus`；路径族直接忽略、精确
`other_paths` 优先和 hash mismatch 拒绝由
`backend/tests/test_soc_pingan_software_path_policy.py` 的受控组件测试覆盖，不伪造真实命中。
每次运行创建新的 Git-ignored 私有目录
`backend/.deer-flow/soc-validation/edr-safe-path-ten/<UTC>/`，其中
`acceptance.json` 是主审阅入口，`runtime-batch/` 和 `soc-validation.sqlite` 保存完整证据。
