# Mac 发布准备路径修复

用户执行第10G返回 `production_settings_check_failed`，发生在正式备份和写入之前。
已通过人工路径别名复现脚本缺陷：临时目录保留 `/var/...` 写法，监测配置读取器将其
解析为真实路径（macOS常见 `/private/var/...`），同一文件的字符串比较不相等。
用户日志未提供底层检查项；该复现解释了一个与Mac环境吻合的失败原因，不推测其它配置错误。

打开本目录 **`PREPARE-PRODUCTION.sh`**，复制全文到原内网 Mac 终端执行。
成功返回 `prepared` 或 `already_prepared` 后，按既有流程打包完整项目发布。
不需要执行旧 ENABLE，不需重跑模型或重新提交 ZEUS 153。

修复只将准备脚本的临时检查目录规范化为真实路径：

```python
stage = Path(directory).resolve()
```

所有生产配置检查、规则目录检查及失败回滚仍保留。准备的5个文件、运行时代码字节、
LOCAL/STG隔离、生产启用方式和随包规则清单均与第10G相同；旧交付文件保持冻结。
命令输出带 `preparation_revision=macos-canonical-temp-path-v1` 便于核对版本。
详情见[第10G说明](../step10g_production_prepare/README.md)。

离线验证命令：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step10h_macos_prepare_fix/test_macos_paths.py
backend/.venv/bin/python validation/rule_refine/implementation/step10h_macos_prepare_fix/offline_preflight.py
```

4项针对性测试覆盖旧命令确实失败且不写入、修复后通过及幂等、无效生产配置仍拒绝、
所有应用载荷保持一致。完整Shell在确有路径别名的TMPDIR下执行原454项回归，并再次执行检查幂等。
这是Linux上模拟Mac路径别名的离线验证，内网Mac实际执行及生产启动仍以用户后续回传为准。
无模型、Redis、业务库或ZEUS调用，不运行项目启动脚本。
