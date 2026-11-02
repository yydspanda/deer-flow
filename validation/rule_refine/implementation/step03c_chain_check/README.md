# 第三步 C：串联接收解析与计数，测量调用开销

前置：用户内网第 3 步 B 修复后 19 项测试通过（42.179 秒），人工样例 unique_count=3、new_signal_count=1、duplicate_added=false、model_calls=0。测试总时长包含异常与竞争场景，不能除以测试数当作单条告警耗时。

## 内网结果已通过

用户确认 status=passed、submitted=15、unique_count=12、new_signal_count=1、duplicate_added_count=0、cleanup_ok=true。计数均值/中位数/最大值为 364.74/350.92/522.34ms；解析加计数为 365.37/351.49/522.91ms。新增开销主要集中于 Redis 往返，下一步先优化读取并核对接线方式，不把此样例当生产吞吐量验收。

正式规则配置与企业归属应由 ZEUS API 返回；CSV 是接口未就绪阶段的 mock。用户本次澄清再次确认这一原定边界。

## 执行

完整复制 [CHECK-STEP03C.sh](CHECK-STEP03C.sh) 到内网 Mac。无需传文件，不修改应用代码，复用已安装 CSV 查询、ingress、counter 以及原项目 get_redis。

- CSV 读取一次，选取可唯一匹配的一条配置，只使用身份信息构造人工请求。
- 12 条不同人工告警 + 3 次重发，每次包含重复 hitLog，串联解析后同一次只计一条规则观察。
- 自动随机 namespace，顺序执行 15 次 observe。人工阈值 5；不运行模型、Celery 或真实接口。
- 最后精确删除本次使用的三个 Redis 键；若清理失败，返回失败键便于后续清理，不宣称已清理。

预期最终 JSON：status=passed、submitted=15、unique_count=12、new_signal_count=1、duplicate_added_count=0、cleanup_ok=true。当前 CSV 有 911 条、5 个企业配置归属，ambiguous_topic_rule_pairs=0。

贴回完整的最后 JSON，尤其 count_call_ms 和 parse_and_count_ms。它们是 15 次顺序人工调用的均值/中位数/最大值；不含首次 CSV 加载、Redis PING、清理和打印，不据此宣称生产吞吐量或 P95。demo 使用指定时间，live 还会读取 Redis TIME；真实接入多规则、并发竞争、网络状况会影响开销。

## 已确认的实时企业归属方案

用户明确选择：按 topic + ruleCode 查询 CSV，取唯一配置的 company_code 作为规则统计归属；查不到或有歧义时跳过并记录原因。

这是配置归属解析，不是从请求/资产推断身份授权。保留 company_scope_source=csv_unique_match；不伪造 explicit，不用 app_code 或资产公司替代。当前代码的 live 仍要求 explicit，**此命令只使用原本允许的 demo 路径**；下一步接入口时需显式实现并测试用户选定的 CSV 策略。

## 本地验证

实际 CSV 与已交付的三个模块，在禁用 EVAL/EVALSHA 的临时隔离 Redis 上串联检查通过；12 条、1 信号、重复不增量，测试键为 0，临时容器已移除。Python 3.11 语法与 bash -n 通过。内网本步已通过，真实入口性能尚待验证。
