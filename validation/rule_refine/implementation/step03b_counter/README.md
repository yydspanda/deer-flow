# 第三步 B：Redis 去重、窗口计数与触发信号

第 3 步 A 已在内网通过。用户表示项目已有 Redis 配置，本步复用 `util.redis_client.get_redis()`；无需贴连接地址或密码。

## 内网验证已通过

用户已执行修复：19 项测试全部通过（42.179 秒），人工样例 submitted=4、unique_count=3、new_signal_count=1、duplicate_added=false、model_calls=0。无需再次执行修复或重跑本步测试。下一步是 [第 3 步 C：串联检查与调用开销](../step03c_chain_check/README.md)。

## 内网反馈与修复

内网已连通 Redis，但旧版 14 项测试有 12 项因 `unknown command EVAL` 失败，另两项通过不代表计数功能可用。尤其旧错误类型测试只断言 ResponseError，误把命令不可用当成预期的键类型错误；修复已收紧断言。

源码包中的 `util/model_semaphore.py:98-118` 和 `:120-137` 已用 WATCH/MULTI/EXEC + 有限竞争重试规避 Lua；`model/db/crud/workflow_task.py:62-75` 用相同方式按 token 条件删除。当前计数实现沿用此模式，不要求修改 Redis 配置，不尝试 EVALSHA。

**已执行旧版的用户：完整复制 [FIX-NO-EVAL.sh](FIX-NO-EVAL.sh)。** 只更新本步两个文件，核对旧版/新版 SHA-256 后原子替换，保留 `.before-no-eval` 副本；遇到其他改动停止且不覆盖。修复命令支持重复执行。无需重跑此前步骤。

## 首次安装

整段复制 [APPLY-STEP03B.sh](APPLY-STEP03B.sh) 到内网 Mac 终端执行。新增 `my_workflows/zeus/rule_refine/counter.py` 和 `test/rule_refine/test_counter.py`，不修改原告警入口。代码先检查再写入，拒绝覆盖已有不同内容。

测试连接真实 Redis，使用系统随机生成的独立测试命名空间，只清理自己使用的精确键，不调用 FLUSHDB、不扫描或删除业务键。测试只使用人工告警，不调用模型；结束后人工样例数据会清理。

预期 `Ran 19 tests`、`OK`，最后：

```json
{
  "mode": "demo",
  "submitted": 4,
  "unique_count": 3,
  "new_signal_count": 1,
  "reason": "high_volume",
  "duplicate_added": false,
  "model_calls": 0
}
```

请贴回测试摘要和 JSON。如果连接失败，贴错误类型即可，不贴密码。重复验证：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/test_counter.py
.venv/bin/python test/rule_refine/test_counter.py --demo
```

开发测试也支持 `RULE_REFINE_TEST_REDIS_URL` 指定隔离 Redis；内网直接使用项目配置即可。这个环境变量只用于测试，不是生产配置替代方案。

## 行为及配置

`RedisRuleCounter(client, ...)` 复用已有客户端；`observe(observation)` 消费第 3 步 A 的规则观察，返回是否新增、是否过旧、当前和前窗数量、基线状态、倍数及已保存的信号。

| 参数 | 模块默认值（尚未接入线上） | 本步人工测试 |
|---|---:|---:|
| window_seconds | 3600 | 10 |
| minimum_count | 1000 | 3 |
| growth_ratio | 3 | 2 |
| cooldown_seconds | 3600 | 10 |
| dedup_seconds | 86400 | 60 |

当前窗口 `(t-W,t]`，前窗口 `(t-2W,t-W]`。数量达到 minimum_count 后：连续观察满 2W 且前窗非零时，需要达到 growth_ratio，信号原因 growth；基线不足或前窗为零时记 high_volume，不输出虚假的无限增长倍数。

同一来源、企业、topic、规则下，告警 ID 在保留期内只计一次，首次时间不刷新。会话、任务、配置版本变化不导致新增。保留期 D 至少覆盖 2W 加冷却；超过 D 后重新推送旧 ID 可能重新计入，这是有限存储的明确边界。`added=false` 也可能是过旧输入，需结合 `too_old` 判断。

live 使用 Redis TIME 作为计数观察时间；demo/replay 使用传入的带时区 received_at，可复现窗口边界。模拟/回放时间水位只前进，乱序记录保留其原时间；不把过旧记录搬入当前窗口。

demo/replay 默认自动生成独立 namespace；真实服务配置稳定 namespace，无需运营手填编号。live 必须有显式企业范围，真实来源仍需在接入前确认。

## 信号保存与后续派发

WATCH 监视 events/state/pending 三键，读取并计算后通过 MULTI/EXEC 一起提交去重、计数和 pending 信号；响应丢失后重试同一告警可找回信号。三类键共用一个 Redis hash tag：events 为保留告警 ID 及首次时间的 ZSET，state 为时间水位/覆盖/冷却信息，pending 为一份待处理信号。

同规则已有 pending 时，新的告警仍累计，但不覆盖或新建信号；返回的 pending 是触发当时的快照，不是实时计数。信号含系统 ID、规则范围、窗口、阈值、触发原因和最多 20 个窗口内告警 ID。这只是初始样本引用，后续代表场景选样不能只依赖最近 20 个。

- `pending(observation)`：读取已保存信号，即使当前没有新告警也可查询。
- `acknowledge(observation, signal_id)`：仅匹配当前 ID 才清除；错误或过期确认不会删除别的信号。确认后仍受冷却期约束。
- 后续任务派发必须以信号 ID 幂等，并在下游持久接收后确认；不能只依赖 triggered 布尔值，也不能在模型执行前无依据地删除信号。本步尚未实现后台轮询/派发。

events/state 有空闲 TTL，pending 无 TTL，保留到显式确认。未来派发器负责轮询与故障可见性，避免遗留 pending 长期阻止新信号。跨应用进程可恢复，不等于承诺 Redis 重启后持久化；后者取决于服务端配置。Redis 事务不回滚任意运行时错误，因此在提交前检查键类型、输入和 JSON 编码；命令能力仍以内网测试为准。只把明确的 WATCH 数据竞争做有限重试（默认 transaction_retries=32），超限抛 CounterBusyError；连接异常（包括 redis-py 包装的 WatchError）与其他异常直接传播，不伪装成功。参考 [Redis 事务说明](https://redis.io/docs/latest/develop/using-commands/transactions/)。

配置参数、方法、v1 键名及数据格式保持兼容；不新增数据库或迁移旧数据。事务实现每条观察有多次 Redis 往返，接入真实入口前需要验证实际延迟和并发。

## 验证与边界

本地临时 Redis 7.4.7 禁用 EVAL/EVALSHA 后，原版准确复现 12 errors；修复后 19 项测试通过：去重/首次时间/跨实例恢复、窗口与乱序、完整/零基线、冷却、pending 及条件确认、并发跨阈值、保留期、失败路径、错误键类型、写入成功但响应丢失、live 不受远未来输入时间影响，以及无 Lua 调用、真实 WATCH 竞争、确认期间信号被替换保护、竞争重试耗尽、断线包装的 WatchError 不误重试。测试后验证临时数据库无残留测试键，并删除临时容器，未写共享 Redis。

本地客户端 redis-py 7.4.0；用户内网为 5.0.5，服务端版本未知且已证实 EVAL 不可用，修复后已通过内网 19 项测试；服务端具体版本仍未知。首次安装、旧版升级、重复升级均在隔离测试通过，已有不同内容拒绝覆盖；Python 3.11 语法和 Ruff（120 列）通过。当前未接入真实请求、模型、ZEUS 或自动规则修改。
