# 今晚整项目发布：关闭规则监测

此补丁用于用户的 legacy `sec_know_model` 生产发布。只修改该项目的 `run.sh`，在第一行后追加：

```bash
export RULE_REFINE_MONITOR_ENABLED=0
```

**操作：**在内网Mac的发布源码目录执行 [DISABLE-FOR-RELEASE.sh](DISABLE-FOR-RELEASE.sh) 全文，默认项目路径为 `$HOME/sec_know_model`。出现 `installed` 或 `already_installed` 后，将修改后的 `run.sh` 纳入本次发布。脚本不会替用户提交Git、打包、部署或重启，也不会执行原 `run.sh`。非默认目录可修改复制命令中的 `release_project`。

原文件备份为同目录 `run.sh.before-rule-refine-release-disable`（权限0600）；不要提交或打包该备份。只接受已审阅的原版启动脚本SHA及精确补丁版本；未知修改、软链接、冲突备份均拒绝，不覆盖其他业务改动。目标文件原权限和其余内容保持不变，重复执行不重复插入。`unreviewed_run_sh` 表示需要审阅内网实际版本，不能用旧文件覆盖整份启动脚本。

## 为什么改这里

已上传源码的 `deploy_desc_pks/Dockerfile` 使用 `/bin/bash run.sh`。该脚本启动API和Supervisor；已提供的 `config/celery_task.conf` 启动worker/beat，未覆写此变量。顶部export可由这些子进程继承：

- API的配置加载优先读取明确的0，不会被 `rule_refine.local.json` 的 `enabled:true` 打开；入口监测直接返回，不读取CSV或连接Redis。
- worker中的规则后台执行同样检查监测开关；正确PRD/STG配置另有环境隔离。
- `local_run.sh` 独立启动本地测试，不调用 `run.sh`，其文件、启用JSON和历史结果均不修改。

此补丁对通过 `run.sh` 启动的所有环境关闭监测，不是只对PRD生效；本地若也使用 `run.sh`，该次启动也会关闭。保留既有 `env_profile`，不改变业务服务、队列、端口、模型或鉴权配置。

整项目发布应完整保留 `my_workflows/zeus/rule_refine/`，因为已交付的 `tasks/task.py` 仍会导入该模块。禁用执行不等于可删除依赖。不要只关闭 `surge.enabled`，那会退回旧分析流程而不是关闭入口监测。

## 验证与边界

补丁检查和回归只使用临时文件、Bash语法解析及人工子进程，不启动原应用、模型、Redis、数据库或任务清理脚本。既有源码审核已完成50项配置/入口/监测/后台隔离回归；它们不是生产整机启动验收。

本补丁18项离线回归通过，覆盖原文/权限保留、0600备份、重复安装、未知源码和链接拒绝、冲突备份、原子替换失败、关闭值的子进程继承，以及完整复制命令在临时HOME中的成功和拒绝路径。内网安装结果已回传，见下节。

发布后API日志 `rule_refine_monitor_config` 应为 `enabled:false`、`source:environment`。外部平台若覆盖容器启动命令，或实际Supervisor配置覆写变量，需把同一变量设置到实际启动环境；已上传ZIP不能证明这些外部配置或运行中进程。补丁不会立即改变已经运行的服务。

本地验证：

```bash
python3 validation/rule_refine/implementation/step07g_release_disable/test_patch.py
python3 validation/rule_refine/implementation/step07g_release_disable/build_command.py
bash -n validation/rule_refine/implementation/step07g_release_disable/DISABLE-FOR-RELEASE.sh
```

源码基线 `run.sh` SHA-256：`125020a4f1bc5934d35bcdea1ed9648fbc78d87244c92519abe915f393d8a8d7`。脚本内只保存该hash，不内嵌原项目源码。

## 内网安装结果已回传

2026-11-04用户回传：`status=installed`、`files_changed=1`、`installed_state_verified=true`，修改文件为 `~/sec_know_model/run.sh`。安装前摘要与上述基线一致；安装后SHA-256为 `b0ab3d6753a10fa994d72139072db98ac309b232df963ef394dacf68f7c792fd`，已与本地交付补丁的预期字节摘要核对一致。

备份为同目录 `run.sh.before-rule-refine-release-disable`；只修改启动脚本，`local_run.sh`及配置文件未改。服务未启动或重启，模型/Redis/数据库调用均为0。此回执确认内网发布源码已安装关闭开关，不代表代码已提交、镜像已发布或生产进程已经关闭；无需重复安装。随本次代码发布修改后的 `run.sh`，备份不提交或打包，发布后按上面的启动日志核验。

2026-11-05归档前再次通过18项离线回归，使用与上述原始摘要匹配的本地私有旧包作为测试夹具；
更新后的生产ZIP不是这个补丁的原始基线，不能为使测试通过而放宽摘要校验。
