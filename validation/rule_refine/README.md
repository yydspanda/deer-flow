# 规则配置检视与告警突增优化

本目录保存规则配置检视资料，以及在内网 `sec_know_model` 项目逐步实现告警突增过滤建议的方案。

设计、接口、实施步骤和验收记录统一见 [规则告警突增过滤建议实施方案](规则告警突增过滤建议_实施方案.md)。

## 当前实施入口

当前已完成 [6W统一修复安装](implementation/step06w_unified_limits/README.md) 和 [6X历史规则试跑](implementation/step06x_historical_review/README.md)。内网后台恢复轮询；历史试跑的模型请求、输出格式与配置核对通过，模型返回证据不足，未形成过滤条件。真实突增后的完整自动建议链路和ZEUS接口联调仍待验证。

当前待改进的是取样与容量策略：128KiB输入、最多20条告警和每告警3条日志均为工具设置，未按实际模型token容量分批覆盖全部选定样本。本次提交保留现状及问题记录，没有切换模型或实现自动分批。`DeepSeek_V32`来自规则分析配置，源码包将其路由至平安EAGW；不能单凭别名确认后台实际模型版本。

以下早期步骤保留为交付版本和后续安装器的摘要依赖，不应按列表重新执行一遍，也不要批量格式化已交付的固定版本文件。

已完成步骤：[第 1 步 A 说明与复制命令](implementation/step01_contracts/README.md)。CSV 内网目标位置为 `$HOME/sec-rule-refine-data/rules.csv`。

已完成步骤：[第 2 步 A CSV 查询说明与复制命令](implementation/step02_csv_source/README.md)。

已完成步骤：[第 2 步 B 本地模拟接收说明与复制命令](implementation/step02b_local_receipt/README.md)。

已完成核对：[第 3 步入口核对命令](implementation/step03_ingress_check/README.md)。

已完成步骤：[第 3 步 A 提取规则观察与复制命令](implementation/step03a_observations/README.md)。

已完成步骤：[第 3 步 B Redis 计数与 EVAL 兼容修复](implementation/step03b_counter/README.md)。

已完成步骤：[第 3 步 C 串联检查](implementation/step03c_chain_check/README.md)。

已通过串联检查：[第 3 步 D 查询接口、批量读取与复制命令](implementation/step03d_preparation/README.md)。

已通过入口隔离检查：[第 3 步 E 默认关闭的入口钩子与复制命令](implementation/step03e_ingress_hook/README.md)。

已完成 API 启动：[第 3 步 F 临时启用](implementation/step03f_enable_monitor/README.md)。

已完成普通启动配置：[第 3 步 G 保存配置与普通启动](implementation/step03g_persistent_settings/README.md)。

协作方式：一次推进一步，用户在内网执行并贴精简结果，助手检查后给下一步。每次继续先读取方案末尾的“当前协作位置”，根据已验证结果更新。

## 资料

- [规则配置 CSV](202610221741结果集.csv)：配置查询的本地替代来源，不包含告警流。
- [过滤条件格式示例](输出优化建议.txt)：`topic + A/B/C + expression`。
- [既有运营修改建议](audit-20261026-729b0892/operations-report/运营修改建议.json)：问题、原因、修改片段与前提。
- [静态检视报告目录](audit-20261026-729b0892/operations-report/)：此前完成的规则配置检视报告与附件。

实现位置是内网原项目；本目录用于方案、可复制安装脚本和离线验证代码，不作为 DeerFlow 生产模块的运行时依赖。上面的CSV、格式资料及审计目录是本地私有依赖，未随Git提交；原项目源码ZIP同样独立提供。含原始告警、配置值及运行结果的材料按 `validation/AGENTS.md` 管理。安装/诊断脚本可能调用内网服务，应按各步骤说明执行，不能直接把全部shell脚本作为测试运行。
