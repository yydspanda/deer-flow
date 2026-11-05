# 单条小规则的批量回执兼容

用户在 Step10E 实测中完成两轮模型调用及一次 ZEUS STG POST，客户端日志返回
`code=200, rule_id=[153]`。本地却记录 `submission_unknown`。
原因是批量接口的 `data` 原样进入客户端 `rule_id`，而 topic 回执仅接受标量 ID。

本补丁只修改 `topic_delivery.py` 的响应处理，并增加一个测试文件。
每次 topic 交付确实只提交一条规则，因此成功响应中的单元素 ID 列表可以归一为其唯一标量。
仍要求 `success is True`、整数 `code=200` 及原有合法 ID 类型。标量响应继续兼容。
空、多元素、嵌套列表、布尔值或其他无效 ID 保持未知，不取第一个元素、不重发。
落盘继续使用原有标量格式，不变更提示词、规则条件、数据库、队列或监控开关。

## 内网操作

打开 [INSTALL-TOPIC-RECEIPT-FIX.sh](INSTALL-TOPIC-RECEIPT-FIX.sh)，复制全部内容到内网终端。
先检查源码并执行离线回归，通过后备份更新2个文件。不调用模型、Redis、业务库或ZEUS，不启动服务。
新手工进程直接读取修复；已运行后台进程需后续按原发布流程重启才能加载，安装不会代为重启。
本命令仅安装，不附带重新生成或提交。不要为了验证这次回执而重跑原案例。

当前旧记录已丢弃 `[153]`，只保存 `code=200, rule_id=null`，不能从它自动还原服务端 ID。
153 最初来自用户回传的成功日志；用户随后确认“stg能看到这个153，没问题”，本次STG创建已核实。
该确认是外部核验，不是从旧NAS回执恢复的ID；启用状态未报告。旧 unknown 回执及 job 文件保持原样，
继续阻止同一精确payload/公司/目标scope的重复POST。不能删除旧回执再发一次，也不将新补丁安装
描述为已恢复旧job成功状态。未新增核对/启用/删除服务端规则的接口调用。

若后续显式启用监控，使用本目录更新源码摘要的 [ENABLE-TOPIC-FILTER.sh](ENABLE-TOPIC-FILTER.sh)；
此次安装不会执行启用命令。旧交付命令、旧结果与旧安装包保持冻结。

## 离线检查

442项回归通过，包含新增7项单元素数组、标量兼容、异常数组、不重发、旧unknown不变及实际两阶段流程测试。
实际安装Shell首次/重复安装、目标摘要和未修改文件检查通过；全部使用临时项目及人工响应，外部调用为0。
人工两阶段流程确认 `[153]` 生成 `received / zeus_rule_id=153`，不代表补丁已在内网安装。
本次真实STG创建由用户页面核验确认，与补丁的离线验证分开记录；不需要重跑模型或重新提交153。

```bash
backend/.venv/bin/python -B validation/rule_refine/implementation/step10f_topic_receipt_array/offline_preflight.py
```
