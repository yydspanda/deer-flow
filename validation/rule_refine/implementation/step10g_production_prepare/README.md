# 在内网 Mac 准备生产发布

前提：已安装 Step10F 单元素批量回执修复。用户已报告执行了该步。

打开 `PREPARE-PRODUCTION.sh`，复制全文到内网 Mac 终端执行。默认项目为
`$HOME/sec_know_model`，沿用其 `.venv/bin/python`。成功显示 `prepared`，重复执行为
`already_prepared`。之后按既有流程打包完整项目、发布即可，不再执行旧 ENABLE 命令，
不需要进生产容器手工启用。正常发布启动加载新配置；准备脚本本身不启动服务。

## 准备内容

- 校验已安装 Step10F 源码，运行隔离离线回归；失败时不更新发布文件。
- 从现有 topic/local 配置发现规则配置清单，使用原 CSV 解析器校验。该清单描述
  rulecode、topic、公司和规则条件，不是 `20261102175803453.csv` 告警测试数据。
- 原字节复制到 `config/rule_refine.rules.prd.snapshot`；仍为 CSV 格式，使用 `.snapshot`
  避免原项目的 `*.csv` 忽略规则遗漏它。文件须随内部项目打包，不回传或提交公开仓库。
- 新增 `config/rule_refine.topic.prd.json`：相对路径指向随包清单，命名空间默认
  `topic-v1:prd`，Pub 模型和现有默认预算，无人工测试 operator_context。
- 仅给 `topic_settings.py` 增加 PRD 专用配置选择；未准备该文件的旧部署仍兼容。
  LOCAL 继续使用原 topic 配置，STG 路径保持原样。已有 PRD 设置再次准备时保留。
- 仅替换 `run.sh` 既有监测关闭标记块：明确 PRD 启用，其他环境关闭。其余启动、
  队列、清理命令逐字保留，不执行脚本。生产的 Pub 27 并发不修改。
- 写入前备份，写入失败回滚；相同内容重复执行不会重写文件。

准备脚本共更新/新增五个文件：上述运行模块、生产 JSON、清单、`run.sh` 和新增的
离线配置测试。现有 LOCAL/STG 配置、业务数据、旧结果及 ZEUS 153 回执不修改。
不调用模型、Redis、业务数据库或 ZEUS；临时 SQLite 仅用于原有离线测试。

生产结果仍按现有 PRD `root_config.DATA_DIR` 保存到 NAS 的
`rule_refine/topic_filters/` 下。Mac 不需要挂载生产 NAS。CSV 清单本身随代码发布。
脚本核验目录结构及内容，不声称该快照与当前线上规则中心已同步，也不证明线上
NAS/网关/模型连通或自动触发；这些由实际发布后的运行结果证明。

## 目录发现与重复准备

第一次依次读取已配置的 topic/local CSV 路径；两者均配置且内容不同时停止，避免
猜测哪个版本。两者均未设置时尝试 `~/sec-rule-refine-data/rules.csv`。这些路径不存在
时返回明确错误，不改源码。需要显式指定清单时可在复制命令前设置
`export SEC_TOPIC_CSV_PATH="/实际规则清单路径"`。

重复准备默认复用生产专用配置中的已打包快照，不静默换成 DEV 材料。后续换用更新的
规则清单时同样显式设置 `SEC_TOPIC_CSV_PATH`，准备后将该快照一起发版。

备份默认写入项目外 `~/sec-rule-refine-data/topic-production-prepare-*`，不随发布包携带。
项目及备份根分别支持既有 `SEC_KNOW_MODEL_ROOT` / `SEC_RULE_REFINE_DATA_ROOT`。

## 本地构建与验证

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step10g_production_prepare/test_prepare.py
backend/.venv/bin/python validation/rule_refine/implementation/step10g_production_prepare/offline_preflight.py
```

`offline_preflight.py` 在临时工程执行生成的完整粘贴命令及重复执行，覆盖全部既有回归和
12项 PRD 配置测试；`test_prepare.py` 单独覆盖10项失败、回滚、目录选择及环境隔离检查。
所有清单与数据库材料均为人工夹具，不执行真实启动脚本或服务请求。

此前 Step10—10F 交付文件保持冻结。新配置及回执兼容逻辑不修改 AI 提示词、两轮调用数、
人工审核职责、提交去重或旧 `submission_unknown` 记录。
