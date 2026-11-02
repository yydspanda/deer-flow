# 第 6 步 A：保存分析与模拟交付重试

5B 内网已通过。本步新增 saved_delivery.py 和独立测试，将已有分析保存后再交付到本地模拟接收端。无需重启服务，不调用模型、数据库或 Redis。

## 执行

在内网 Mac 新终端完整复制 [APPLY-STEP06A.sh](APPLY-STEP06A.sh)。脚本验证前序文件和新增内容摘要；不同已有版本停止，同版本可重复执行。

测试用临时目录并自动清理。随后人工示例在 `~/sec-rule-refine-data/saved-delivery-demo-随机串/` 新建独立目录：先模拟“接收端已保存但返回报错”，再从已保存分析重试，预期接收端识别 duplicate=true，建议 ID 保持一致。保留该目录可检查 records、attempts 和 receipts 中的 JSON；只需贴测试汇总和最后摘要。

## 接口与文件

- `LocalAnalysisStore(directory).save(materials, analysis)`：按完整规范化 JSON 的 SHA-256 保存，不改变建议 ID；同内容同记录，不同内容另存。返回 record_id、duplicate、saved_file。材料来源和模式必须对应；记录仅用于保存，是否可交付仍以 5B 为准。
- `store.load(record_id)`：只接受 64 位小写十六进制标识，校验文件大小、完整摘要、JSON 格式与版本。拒绝符号链接、损坏或改写记录。
- `deliver_saved(store, record_id, load_rules, sink)`：只读取已保存材料和分析，重新执行 5B，通过后用原建议 ID 提交，不包含模型生成回调。配置工厂仍须每次重新读取快照。

分析包保存在 `records/<record_id>.json`。每次调用交付函数新增 `attempts/<record_id>/<attempt_id>.request.json` 和 `.result.json`：request 在提交前保存检查结果，result 保存接收结果或错误类型。写入沿用临时文件、fsync、原子硬链接，不覆盖不同内容；新根目录及叶目录 0700、文件 0600。该实现用于当前 Mac/Linux 本地文件系统，不作为跨主机任务锁或整机断电事务保证。

本步内部记录标识由程序产生并传递，用户无需手动填写。未来任务层负责按突增信号选择已保存分析，并控制生成并发；保存两个不同分析不会自动判定它们属于同一次生成重试。

## 结果解释

| status | 含义 |
|---|---|
| received | 已取得并验证本地模拟接收回执；applied=false |
| blocked | 5B 检查未通过，未提交；check_reason 说明原因 |
| not_applicable | 没有可提交的新增排除建议，例如材料不足；保留原结果 |
| delivery_unconfirmed | 提交异常或回执无效，接收端可能已经保存；可用同一记录重试 |

只接受 mock 回执，核对建议 ID、内容摘要、received 状态、applied=false 和布尔 duplicate。回执错误不回显正文。结果写盘失败原样抛出，不能当作“未交付”；已保存的 request 和接收端记录保留，重试仍使用同一建议 ID。

每次尝试独立保存：新配置变化导致本次 blocked，并不否定之前已接收的历史。后台信号发现/派发/确认、生成重试和运营 HTML 报告在后续步骤接入。本步未修改在线入口或规则。

## 本地验证记录

2026-10-28：Python 3.11 语法检查、Ruff 检查与格式检查、安装器 Bash 语法检查通过。隔离目录首次完整安装后 12 项测试通过（0.208 秒），人工示例显示 received、retry_duplicate=true、recommendation_id_preserved=true。重复安装通过；前序依赖或新增文件版本不同时，安装器停止且所有 Python 文件保持原样。未调用模型、数据库或 Redis。内网 Mac 执行待用户反馈。

## 内网回传

用户回传 12 项测试通过（0.077 秒）。人工演示首次 delivery_unconfirmed，重试 received；saved_record_reused、retry_duplicate、recommendation_id_preserved 均为 true，automatic_apply=false，模型/数据库/Redis 调用均为 0。留档目录 `/Users/zhangjianming627/sec-rule-refine-data/saved-delivery-demo-m1e64sua`。继续 [6B 信号处理串联](../step06b_signal_processing/README.md)。
