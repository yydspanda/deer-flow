# 第7A步：修复LIVE验证的终端确认

内网第7步已安装成功：147项测试、18个文件更新；DEV已补42条人工记录，实际召回当前6条和最近20条历史均通过。随后LIVE工具在预览后报 `UnsupportedOperation`，尚未显示确认输入，也未进入 `execute()`；此次SDK请求为0。

后续内网已确认修复安装成功（18项测试、2文件更新、备份 `surge-terminal-fix-upn8thww`），LIVE提示正常。新验证目录 `surge-dev-history-live-_uyz_4fk` 两阶段均执行，结果 `received/proposal`、`ai_review_completed=true`，模型调用2次。无需再次执行修复脚本或重复请求，接下来读取已保存报告。

原因是验证工具使用 `open('/dev/tty', 'r+')`。CPython的缓冲读写模式需要可定位文件，而终端不支持seek。已用真实PTY复现；原有 `mock_open` 测试未覆盖这一行为。

打开 [FIX-AND-CONTINUE.sh](FIX-AND-CONTINUE.sh)，复制全文到内网终端：

1. 核对原验证工具摘要，先运行18项离线回归（13项原预检、5项真实PTY测试）。
2. 备份并更新 `test/rule_refine/check_surge_dev_live.py`，新增终端回归测试。改为独立的只读、只写TTY句柄；模型参数、原材料、确认内容及调用次数不变。
3. 自动继续同一DEV验证，重新展示当前6条、历史20条。确认提示出现后输入 `LIVE`，最多2次SDK请求；代理内部可能重试。

不需要重新安装后台或重新补数。本次修复不要求重启服务；原第7步的后台代码若尚未重启加载，仍按第7步说明重启一次。模型代理需按原方式运行。

预览的 `generation_selection.current_count=3` 是首轮使用6条当前材料中的一组，另外一组供第二轮复核；历史20条参与首轮。它不是数据丢失或上下文预算不足。第二轮还会带上草案引用的原文，实际数量以输出为准。

修复脚本保留未知本地编辑，重复安装不重写；备份目录前缀 `surge-terminal-fix-`。每次运行脚本并再次输入LIVE会新建验证任务，后续诊断应使用已保存结果，避免重复调用。

第7步已交付payload和安装脚本保持原字节，不重新生成覆盖版本。修复版本单独存于本目录。

验证：真实PTY测试先对旧代码复现失败，再对新代码通过；`test_patch.py`验证安装、重复执行及未知源码保护。原13项预检与新增5项终端测试通过，2项修复交付测试通过，均未调用模型/Redis或读写业务数据库。

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step07a_terminal_confirmation_fix/test_patch.py
backend/.venv/bin/python validation/rule_refine/implementation/step07a_terminal_confirmation_fix/build_command.py
bash -n validation/rule_refine/implementation/step07a_terminal_confirmation_fix/FIX-AND-CONTINUE.sh
```
