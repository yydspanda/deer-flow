# 第 3 步 G：保存本地监测配置，恢复普通启动方式

用户已在内网终端完成第 3 步 F：预检 LOCAL、8090、911 条规则、Redis PING 成功，2026-10-27 17:48:12 API 启动完成（进程 7337）。尚无 `rule_refine_monitor` 接收日志，因此只确认启动和预检，不能确认实际告警登记。FastAPI 的 example 弃用提示没有阻止服务启动。

用户希望以后继续直接 `python run.py`，不必每次设置三个环境变量。本步新增本机配置文件与启动配置日志，不改计数算法或原告警入口。

## 操作

在运行 API 的终端按 Ctrl+C，等待退出，然后完整复制 [SAVE-LOCAL-SETTINGS.sh](SAVE-LOCAL-SETTINGS.sh)。脚本保存原 run.py 为 run.py.before-step03g，校验代码版本，新增配置加载模块和 10 项隔离测试；既有不同源码不覆盖，既有本地配置原样保留。

首次创建 `config/rule_refine.local.json`：

```json
{
  "enabled": true,
  "csv_path": "~/sec-rule-refine-data/rules.csv",
  "namespace": "sec-know-rule-monitor-mac-zhangjianming627-v1"
}
```

命名空间沿用已经启用的值，重启不会新建计数范围。配置与 run.py 旧版副本加入项目 .gitignore 的精确条目，保留原 .gitignore 内容；本机配置不用于共享源码提交。

完成后按原方式启动：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python run.py
```

若已经激活项目虚拟环境，等价于 `python run.py`。保留原有 env_profile 等环境配置。无需使用旧的临时启用启动脚本。

## 配置优先级与状态

- 显式 RULE_REFINE_* 环境变量优先于文件对应字段；无文件且无环境开关仍默认关闭。
- 配置在 run_api_server 工厂入口加载，覆盖直接 python 启动和工厂启动；不依赖当前工作目录。
- 相对 CSV 路径以项目根目录解析，支持 ~；不加载 CSV 内容、不连接 Redis、不调用模型。
- 只在完整校验成功后写入三个环境变量。配置损坏时明确关闭监测、输出 configuration_error，不让可选监测配置使整个 API 停止启动。
- 显式环境开关 0 可在配置损坏时紧急关闭；完整环境配置可覆盖不再使用的本地文件。
- 修改配置后重启 API 生效；运行中不重新加载，不改变当前监测缓存。
- `rule_refine_monitor_config` 是启动时的配置状态；`rule_refine_monitor` 是收到匹配的告警后的登记状态，两者不能互相代替。

配置文件是本机覆盖，与 env_profile 的 LOCAL/STG/PRD 不是同一概念；本步不切换 env_profile 或远端地址。

## 验证

本地 10 项配置测试通过，包括缺文件、文件开启、环境关闭覆盖、部分覆盖、非法布尔/空路径/命名空间、损坏/重复/未知 JSON 字段、失败不半写入、不同工作目录、稳定命名空间、不加载服务依赖。启动工厂隔离检查确认配置/日志异常不会阻止原应用工厂继续。

完整脚本首次安装、重复执行、保留原配置、保留原 .gitignore 内容及路由副本、拒绝覆盖未知源码均已验证。Python 3.11 语法和 Ruff 通过。未连接内网服务或发送实际告警。

内网已回传 10 项测试全部 OK（0.020 秒），配置摘要 status=configured、enabled=true、source=local_file、provider=csv_snapshot；namespace 沿用 sec-know-rule-monitor-mac-zhangjianming627-v1。2026-10-27 18:27:58 普通 run.py 启动已输出相同的 `rule_refine_monitor_config` 状态，API 在 8090 启动完成，配置持久生效已验证。实际接收监测仍等待正常告警日志；不为测试随意请求原 /workflow/task，以免派发模型研判。
