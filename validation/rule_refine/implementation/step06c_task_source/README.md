# 第 6 步 C：本地任务材料读取

6B 内网通过。本步新增 LocalWorkflowTaskSource，读取当前 Mac LOCAL 环境的既有 SQLite 任务表，作为 6B 的 load_task_rows 回调；无需加载原应用、连接 Redis 或调用模型。未来远端数据库仍通过注入的任务读取适配器接入。本步不改动原项目 ORM、任务创建或后台队列。

## 执行与回传

完整粘贴 `APPLY-STEP06C.sh`。脚本校验前序文件，新增任务读取模块、测试和本地只读检查；对 materials.py 做三行兼容更新，并保留 `materials.py.before-step06c` 原文件。版本不符时停止，同版本可重复执行。无需重启服务。

顺序执行原有 15 项材料测试、新增 11 项读取测试、临时人工数据库演示、LOCAL 数据库只读检查。回传测试汇总和末尾两份 JSON。

人工数据库演示预期：3 条任务、2 个材料样本、1 条重复任务，cleanup_ok=true。真实本地检查只输出数量；此前用户确认任务表为空，如果仍显示 no_local_samples，只表示当前 LOCAL 数据库没有 zeus/alert_agent 任务，不能作为真实材料读取验收。非 LOCAL 环境显示 skipped_non_local，不连接远端；缺库显示 database_missing_not_created，不创建空库。check_failed 需定位，不能视为通过。

## 查询与证据约定

- `LocalWorkflowTaskSource(path).load_task_rows(signal)` 使用显式文件路径，以 mode=ro、query_only=ON、读事务读取；连接在正常及异常退出时关闭。连接等待 2 秒，SQLite 执行有 5 秒进度截止；这是 SQL 执行保护，不是整个 Python 函数的严格墙钟超时。
- 仅接受 live/replay、source_app=zeus，最多 20 个引用告警 ID。只校验查询所需信号字段；完整信号仍由 6B/4A 校验。空引用直接返回空列表，不代表已连接或验证数据库可用性。
- 参数化筛选 app_code=zeus、flow_id=alert_agent 和引用告警 ID；保留所有状态及所有匹配版本。按创建时间、任务 ID 倒序确定顺序，最多 100 行，发现第 101 行明确失败，不返回截断材料。任务时间不替代接收窗口，所以不按其筛掉窗口外任务。
- SQL 层用 BLOB 字节长度限制 alert_data 为 1 MiB、agent_response 为 16 KiB，再严格解码；总返回正文上限 8 MiB。错误 UTF-8、重复 JSON 键、不合法常量、过深或过大的结构均识别为不可用。
- 无效原告警保留任务行且 alert_data=None，由 4A 排除该告警，防止只挑有效副本隐藏同 ID 的异常版本。
- 历史研判是可选、未确认参考，允许原来的 JSON 字符串、对象等格式。读取失败或超限时设 None 和 agent_response_unavailable=true；4A 将 stored_response_omitted 写入材料 issues，仍保留可用原告警。
- 原表没有 company_code，任务查询不证明企业归属。规则与企业仍由配置唯一匹配和 4A 的 topic/rule/company 检查负责。

## 当前边界

读取器依赖标准库 sqlite3，复用现有文件，选择独立只读连接以避免原 session_scope 的提交行为。原应用无需导入，也不会触发环境配置中的远端凭据读取。

测试写入的仅为自动清理的临时人工数据库；最后的检查才访问当前 LOCAL 业务库，而且只读。诊断从最近 20 条任务中取告警引用，不伪造突增信号、不派发分析、不保存业务原文。实际后台派发、任务读取等待策略、信号确认、运营报告和 ZEUS 联调仍在后续步骤。

## 本地验证

2026-10-28：完整粘贴安装器在隔离目录首次安装通过，原材料 15 项测试通过（0.010 秒），新增读取 11 项通过（0.582 秒）；人工演示为 3 条任务、2 个样本、1 条重复、清理成功。重复安装及原副本摘要检查通过，材料文件/新增文件/前序依赖版本冲突均停止且未修改其他 Python 文件。只读检查在缺库、非 LOCAL、空表及临时人工已有数据四种情况下按预期返回，已有数据库字节保持不变。Python 3.11 语法、Ruff 和 Bash 语法通过；尚未访问内网真实数据库，等待用户回传。

## 内网反馈（2026-10-28）

用户反馈新增 11 项测试通过（0.137 秒），临时人工 SQLite 演示得到 3 条任务、2 个样本、1 条重复，cleanup_ok=true。LOCAL 数量检查仍为 no_local_samples，任务和引用计数均为 0。此次未另贴原 15 项材料测试汇总，不额外记为已确认。下一步交付 6D 待处理信号消费与确认。
