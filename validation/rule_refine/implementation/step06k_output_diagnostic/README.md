# 6K：模型输出校验诊断

6J内网计数与真实模型传输通过：4次提交、3条唯一告警、1个信号，重发未新增；SDK请求1次，finish_reason=stop，6.247秒，tokens=967+279=1246。后续analysis_failed/invalid_model_output，交付blocked，信号未确认；3个测试键清理成功。这不是完整串联验收通过。

原analysis.py将JSON解析、Pydantic结构、样本引用、topic及条件支持异常合并为同一错误，既不保存具体校验异常也不保存模型正文。旧摘要无法证明究竟哪项失败；不应推断已是Markdown围栏或其他具体原因。

CHECK-OUTPUT.sh是本次操作的完整可复制命令，复用用户已保存的synthetic-chain-check-r46kqixu中3个工具文件，核对其摘要并复制到新私有目录。仅对该进程的分析函数创建内存副本：唯一替换最终异常分支，追加安全诊断；保持原提示词模板及严格校验。新测试的窗口时间会变化，新回答可能不复现旧错误，不能称为旧回复的离线重放。

原LIVE确认、单次SDK、共享并发控制、测试键清理均沿用。没有修改内网业务源码、运行配置或原结果目录；不部署或重启。副本将本次助手正文（最多16KiB，不含推理、SDK原响应或请求头）保存为0600的assistant-content.txt，后续可离线复核。output-check.json仅输出分类、受限错误字段路径和固定本地校验原因，不含input/context/完整异常或模型原文。

本地10项测试通过，另用原信号处理/留档/交付/消费模块串联验证：有围栏的人工回复仍被拒绝，诊断保存在原bundle中且测试清理正常；外部模型/Redis/数据库调用0。内网诊断续测待回传，未修复未知原因或声称实际业务效果通过。

维护：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06k_output_diagnostic/test_diagnostics.py
backend/.venv/bin/python validation/rule_refine/implementation/step06k_output_diagnostic/build_command.py
bash -n validation/rule_refine/implementation/step06k_output_diagnostic/CHECK-OUTPUT.sh
```

不要直接再次执行原6J命令碰运气。新诊断测试失败后不自动重发；保留整个目录，回传末尾校验详情。
