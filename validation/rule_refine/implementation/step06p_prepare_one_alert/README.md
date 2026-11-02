# 6P：准备一条真实历史告警请求

6O内网盘点已完成。小样本实际有214行，检查前200行，196条告警带可用规则和日志。按这次反馈，选择样本最多的`ptp-nids / RPAADM_002627`（50条）作为入口联调候选。本步骤只准备请求，尚未提交或运行。

打开`PREPARE-ONE-ALERT.sh`，复制全部内容到内网终端执行。脚本自带所需的只读工具，不依赖之前的临时验证目录，不安装或修改应用代码。

它会：

1. 检查已确认的23MB样本及911条CSV快照摘要，只读取小样本的前200行，不读另外三份大文件。
2. 在目标规则中按原始行顺序选第一条符合条件的请求，不按历史模型结论挑选。核对内外告警ID与表格ID，排除同ID但正文冲突的副本；不改ID、日志、`executeType`或已有请求包装。仅当输入是直接告警数据时补充接口要求的外层包装。
3. 对原接口`WorkFlowTaskInput`做格式校验，并使用已安装版本一致的纯函数预览规则归属。校验和预览使用副本，保存的是原数据，不保存Pydantic转换后的对象。未运行worker完整`AlertRoot`解析。
4. 静态比较原配置及回调代码，输出已审阅版本是否一致。已审阅的LOCAL分支选择STG配置，原任务成功/失败钩子都会调用Zeus；这不证明运行进程的环境及远端实际地址，输出明确标记未验证。
5. 新建`~/sec-rule-refine-data/ingress-one-ready-*`私有目录，保存`request.json`、`observation-preview.json`、`selection.json`。目录权限0700，文件0600。终端只打印摘要，不打印告警ID、原始日志或凭证。

预期状态：`prepared_not_submitted`。把终端最后一段JSON回传即可，原始请求留在内网。

本步没有HTTP请求、业务数据库/Redis访问、Celery派发或模型调用；也不修改运行配置和计数阈值。归属观察只是预览，没有进入实时计数。单条请求后续即使成功执行，也只证明入口衔接，不能证明达到突增阈值或建议效果。历史50条样本数量不代表当前小时告警量。

后续提交前再依据已准备的具体请求和回调检查，确定实际API/worker环境及联调范围。保留原ID的历史告警进入原研判后可能回调原系统，不直接执行会改ID的`inject_priority9_tasks.py`。

## 本地验证

```bash
backend/.venv/bin/python -B validation/rule_refine/implementation/step06p_prepare_one_alert/test_prepare.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step06p_prepare_one_alert/build_command.py
bash -n validation/rule_refine/implementation/step06p_prepare_one_alert/PREPARE-ONE-ALERT.sh
```

11项测试通过：原字段和输入不变、缺失包装生成、三处身份检查、同ID冲突排除、numpy整数列兼容、CSV多匹配/重复JSON键/超预算拒绝、前200行及重复去重、与原ZIP请求模型和回调摘要一致、输出权限与敏感信息隔离、样本变化提前停止、工具校验/非LOCAL停止、完整内嵌命令使用人工PKL成功。测试未连接内网或调用模型。内网准备已回传，见下节。

## 内网回传：2026-10-29

`prepared_not_submitted`：原样本第14行，目标规则有50条合格唯一告警，本次只保存1条135592字节的完整请求。无身份冲突，原ID/正文不变、原包装保留，API格式校验通过，归属预览1个规则。请求SHA256为`283adc1f05877538b0f33658c25e92d90bfb462f4710fe424506cd229fd50572`；保存于`~/sec-rule-refine-data/ingress-one-ready-367kxcn7`。

三份回调/鉴权/配置文件均与审阅源码一致；磁盘LOCAL分支选择STG、成功/失败均有Zeus回调。实际worker环境和完整worker数据解析未验证。业务提交、模型、Redis、数据库调用均0，应用未修改。下一步：[6Q一条入口联调](../step06q_submit_one_alert/README.md)。
