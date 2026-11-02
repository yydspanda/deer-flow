# 第三步 D：统一规则查询接口与 Redis 批量读取

第 3 步 C 内网已通过：15 次接收、12 条去重告警、1 个信号、重复新增 0、清理成功。计数调用均值 364.74ms、中位数 350.92ms、最大值 522.34ms；解析加计数均值 365.37ms。该样例不证明峰值吞吐量。

## 执行

完整复制 [APPLY-STEP03D.sh](APPLY-STEP03D.sh) 到内网 Mac。无需传脚本文件；只更新已交付的 rule_refine 模块及其测试，增加公共查询契约和人工串联检查脚本。所有原文件先验证 SHA-256，保存 `.before-step03d` 小文件副本后原子替换；其他内容不覆盖。重复执行可安全跳过相同源码并重新验证。

命令自动运行：CSV 10 项测试、解析 16 项测试、Redis 22 项测试，以及 15 次人工串联检查。预期三个 OK，最后 status=passed、unique_count=12、new_signal_count=1、duplicate_added_count=0、cleanup_ok=true。

请贴回三组测试摘要及最后完整 JSON。内网耗时是否改善以实际结果为准，不填预估提速百分比。单独复测串联：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/check_chain.py
```

可用 `--csv /完整路径/rules.csv` 指定其他配置快照。仍只用随机隔离的人工告警，不接收真实告警、不启动模型或 Celery。

## 配置来源边界

用户已明确：正式规则配置和所属企业应由 ZEUS 的规则配置 API 返回；API 未提供阶段 CSV 是 mock 查询实现。实时接收模式与配置提供器是两个维度。

`rule_config_source.py` 定义 `RuleConfigSource.get_rule(rule_code, *, company_code=None, topic=None)` 和共同异常。CSV 实现遵循此契约；未来 ZEUS HTTP 适配器映射响应后实现同一方法，本步不编造真实 URL、不发 HTTP 请求、不声明 ZEUS 已联调。

`ingress.py` 只依赖公共契约，不导入 CSV 类或文件路径。唯一匹配取配置 company_code，标记 `company_scope_source=rule_config_unique_match`；实际来源继续保存在 `rule_source.kind`（当前 csv_snapshot）。显式企业范围仍可由可信调用上下文提供；不使用请求同名字段、资产企业或 app_code 猜测公司。

- 零条或多条匹配：明确记录 rule_not_found/rule_ambiguous，跳过相应命中。
- 返回身份不符、缺企业/版本/来源：明确报错，不能混入其他规则或公司。
- 提供器故障：传播故障，不伪装成无匹配。
- 配置来源变化不单独改变 occurrence_key；模式、来源系统、企业、topic、规则、告警身份仍参与去重。
- CSV 旧异常导入名及独立 CLI 保持兼容；已安装 CSV 测试的无包 loader 同时升级为隔离包 loader。

## Redis 优化及边界

仍使用原项目的 WATCH/MULTI/EXEC，不依赖 EVAL。原 WATCH 连接持续监视三个键；通过同一个 Redis 客户端借用另一连接，将状态读取和窗口计数分别批量获取，再回到原连接提交事务。常规路径由逐项等待改为约四次往返；创建信号时另取少量样本。

不能在原 WATCH pipeline 上执行中间读取批次，否则 execute 会重置监视。两个连接必须指向同一主数据源，不配置读副本。读取失败直接失败；读取期间有竞争写入会使原 EXEC 失败并重读。无条件释放、普通 GET/SET 替代、Lua 均未引入。

单次计数最多同时使用两条池连接；接入口时需要限制并发。当前 get_redis 最大连接数为 200。未改变键名、状态格式、窗口边界、冷却、去重或信号确认语义。兼容旧 csv_unique_match 观察，新增提供器无关来源标记。

## 验证

隔离 Redis 7.4.7 禁用 EVAL/EVALSHA，CSV 10 + 解析16 + Redis22 项测试及真实 CSV 人工串联通过；升级、重复执行、版本不匹配保护和备份逐字节校验通过。测试后隔离 Redis 无残留键，临时容器删除。Ruff、Python 3.11 语法和 bash -n 通过。本地 redis-py 7.4.0；内网 5.0.5 实测仍待用户运行。

当前没有修改 `/workflow/task`、Celery 配置、模型任务或原业务流程。此步通过后才继续真实入口接线与启用。draft 目录保留上一轮的未交付中间稿，当前交付以 payload 和 APPLY-STEP03D.sh 为准，不执行 draft。

## 内网第 3 步 D 回传

串联 JSON 已通过：15 次接收、12 条去重告警、1 个信号、重复新增 0、清理成功。计数 mean/median/max 为 154.12/140.51/318.13ms；解析加计数为 154.72/141.04/320.01ms。规则来源仍为 csv_snapshot，企业归属为 rule_config_unique_match，无模型调用，真实入口未连接。均值比上一步降低约 57.7%；不代表峰值吞吐。用户只贴了最终 JSON，单元测试摘要未另贴。下一步见 [默认关闭的入口钩子](../step03e_ingress_hook/README.md)。
