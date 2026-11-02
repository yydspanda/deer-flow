# 第 6 步 B：从突增信号到分析保存和模拟提交

6A 已在内网通过。本步新增 `signal_processing.py` 和隔离测试，复用已有材料组装、分析、建议检查和保存接口。执行完整 `APPLY-STEP06B.sh`，无需重启服务。人工演示不调用真实模型、数据库或 Redis。

## 流程与接口

`SignalProcessor(directory, *, load_rules, load_task_rows, analyze, sink, source_kind).process(signal)`：

1. 按模式、存储命名空间、来源应用、企业、topic、规则编号和信号编号自动定位处理目录；完整信号与材料来源另存。同一身份输入改变时报冲突。
2. 同一主机对同一信号使用非阻塞文件锁，忙碌返回 busy，不并行生成同一信号的建议。
3. 调用配置工厂和 `load_task_rows(signal)`，交给 4A 组装最多 100 条任务中的引用样本；调用方仍须约束实际查询范围。配置和任务读取故障返回 materials_failed，可重试。
4. 在调用注入的 `analyze(materials)` 前保存材料与开始记录。分析器可接 4C 的受限模型适配器，本次测试使用人工回复。传给回调的是副本。
5. 先在信号目录保存完整分析包，再建立 6A 的内容索引，随后执行 5B 检查和本地模拟提交。若索引或交付中断，重试从完整分析包恢复，不重新查询任务或生成分析。
6. 每次交付都重新读取配置；回执丢失后仍提交原建议 ID。本地接收端负责识别重复。

信号和内部目录编号由程序处理，不需要用户输入。每次 process 最多调用一次分析器；模型请求上限和超时仍由注入的适配器负责。`model_calls_this_attempt` 表示本次处理的分析结果报告的模型调用数；复用已有分析时为 0，分析是否执行完不能确认时为 null。

## 中断与结果

- 有开始记录、没有完整分析包：analysis_unconfirmed，不能自动认定模型没调用过并再次生成。该状态也可能发生在开始记录写好、模型尚未调用时，是保守处理。显式生成重试在后续后台操作中补齐。
- 正常返回的 analysis_failed 也保存，不隐式重新生成；交付检查返回 blocked。
- 分析结果已保存、索引建立失败：抛出写入异常；再次 process 自动从原分析恢复。
- 交付异常或回执无效：delivery_unconfirmed；重试继续同一建议。
- 没有修改建议或材料不足：not_applicable，保留分析结果，不向接收端提交。
- 已存文件损坏或输入冲突：明确报错，不删除文件或当成首次运行。

`jobs/<自动摘要>/input.json` 绑定完整输入；`analysis_started.json` 保存分析前材料；`analysis.json` 保存完整分析；稳定的 process.lock 不删除、不替换。`artifacts/` 沿用 6A 的分析索引和各次交付记录。新目录 0700、文件 0600。只承诺本地 Mac/Linux 文件系统上同主机进程互斥；不作为跨主机队列、网络文件系统锁或整机断电事务保证。

## 本次验证边界与下一步

本步串联的是可被后台任务调用的流程入口。当前仍未把在线监测信号派发到此入口，未确认删除 Redis 信号，未读取真实告警表，未进行真实建议效果验证。后续接入任务读取与既有 Celery 派发、信号确认和运营报告。

与直接在后台任务里重新实现相同步骤相比，独立流程入口可让人工测试和后台调用共用恢复逻辑。当前本地文件存储沿用 mock 留档；正式多主机执行前须用既有数据库/任务机制落实唯一性与恢复，不能仅依赖此文件锁。

## 本地验证

2026-10-28：安装器首次完整安装后 13 项测试通过（0.499 秒），包含并发线程及独立进程对同一锁的验证、分析前后写入中断、索引失败恢复、复制其他信号材料的拒绝、当前配置变化和回执丢失。人工示例 received，analysis_calls=1、task_load_calls=1，analysis_reused_on_retry/ retry_duplicate/ recommendation_id_preserved 均为 true。Python 3.11 语法、Ruff 格式与检查、Bash 语法通过；重复安装成功，依赖或新增文件版本冲突时停止且未修改其他 Python 文件。内网 Mac 执行待回传，真实服务与模型未调用。

## 内网回传

用户回传 13 项测试通过（0.189 秒），示例 received，首次模拟回执丢失；analysis_calls=1、task_load_calls=1，重试复用、接收去重、建议 ID 保留均为 true。真实模型/数据库/Redis 调用数为 0，real_ingress_connected=false。留档目录 `/Users/zhangjianming627/sec-rule-refine-data/signal-chain-demo-3noww6qs`。继续 [6C 本地任务材料读取](../step06c_task_source/README.md)。
