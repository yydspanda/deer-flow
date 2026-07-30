# Shared Validation Utilities

该目录只存放多个验证入口共同使用的底层工具，不承载 Adapter 或 Runtime 业务判断。

- `restricted_dataframe_pickle.py`：仅允许验证语料所需的受限 pandas/numpy pickle 类型。
- `compact_encoded_llm_context.py`：调用生产 encoded-context 策略做自检与样本投影；完整
  raw payload 不被改写，也不尝试解码长编码内容。

依赖方向只能从构建器指向 `shared/`。生产代码禁止导入 `validation.*`。

```bash
backend/.venv/bin/python validation/compact_zeus/shared/compact_encoded_llm_context.py --self-check
```
