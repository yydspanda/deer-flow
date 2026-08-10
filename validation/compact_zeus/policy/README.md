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
