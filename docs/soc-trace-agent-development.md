# TraceAgent 新同事上手与共同开发说明

**你可以独立开发、启动和测试 TraceAgent，不需要启动 SOC Runtime、网页或 ZEUS。** 两个服务放在同一个仓库，使用同一份 Python 依赖配置；日常开发各自启动，需要双方联调时再一起运行。

本轮已经提供接口、启动命令、SOC 调用客户端和离线测试。**真实调查引擎尚未迁入**，所以刚拿到代码时，能够跑测试和启动接口，但不能完成真实调查。你的主要任务是把原 Trace 的调查、日志查询、任务管理和报告代码接入这个架子。

业务和 HTTP 格式以[三方交互流程与接口约定](../validation/trace_agent/ZEUS-AI研判-TraceAgent三方交互流程与接口约定.md)为准。本页说明如何在当前仓库继续开发，不另建一套接口约定。

第一次接手，先按下面第 1～5 步操作。后面的“开发参考”用于迁入代码时查阅。

## 第 1 步：拿到个人开发副本和 Python 环境

请先向项目负责人取得包含本次整合的代码，以及内网依赖镜像配置。使用自己的开发目录；多人共用的是代码和依赖版本，每台机器自行安装环境。

本文所有命令都从**仓库根目录**执行，也就是同时包含 `Makefile`、`backend/`、`scripts/` 的目录。先用下面命令确认位置：

```bash
pwd
ls Makefile backend/pyproject.toml backend/uv.lock
```

出现“文件不存在”时，先 `cd` 到自己的 `deer-flow` 项目目录。不要在原来独立的 Trace 项目或 `backend/` 目录内执行根目录的 `make` 命令。

**如果项目环境已经装好**，先验证：

```bash
backend/.venv/bin/python -m trace_agent --help
```

看到 `check`、`serve` 两个子命令，就可以进入第 2 步。缺少 `.venv` 或提示 `No module named trace_agent`，需要先安装/更新共享环境。

**内网 Apple Silicon Mac 首次安装**：沿用项目的内网安装入口。`python3` 必须是 Python 3.12 或更高版本，先检查：

```bash
python3 --version
```

安装入口会先检查 Python、uv、Node 22+、项目指定的 pnpm、nginx 和内网镜像配置，缺少时按提示补齐，再执行：

```bash
python3 scripts/soc_pingan_macos_host_dev.py install
backend/.venv/bin/python -m trace_agent --help
```

这个安装入口也安装前端依赖，但**不会启动 SOC Runtime 或 Trace**。它会重建 `backend/.venv`，仅在自己的开发副本安装，不要对正在提供服务的共用部署目录执行。镜像配置使用 `backend/samples/pingan_dev/uv-index.env.example` 及本机 pnpm 配置，由负责人提供，不自行切换公网源。这里不需要执行 Host 的 `check`/`start` 子命令，它们还检查 SOC 运行资料；Trace 独立开发使用下面的 `soc-trace-*` 命令即可。

其他已获准访问锁文件依赖源的开发机，可以在根目录执行：

```bash
(cd backend && uv sync --locked)
backend/.venv/bin/python -m trace_agent --help
```

已有部署环境如安装了额外依赖，沿用该环境的安装流程；普通 `uv sync` 不负责保留未选择的 extras。

## 第 2 步：先运行离线测试，不启动任何业务服务

```bash
make soc-trace-test
```

看到最后一行 `... passed` 且没有 `FAILED`，说明当前接口和衔接测试通过。测试会使用合成告警、模拟引擎和进程内 HTTP 请求，不需要启动 SOC Runtime，不连接真实模型、日志平台或 ZEUS。本轮交付时该命令为 102 项通过；以后增加用例后数量会变化。

三个命令的区别如下，它们不是必须连续执行的三个步骤：

| 命令 | 什么时候用 | 实际作用 |
| --- | --- | --- |
| `make soc-trace-test` | 修改代码后 | 运行离线自动测试，结束后回到终端 |
| `make soc-trace-check` | 核对启动配置时 | 检查密钥、端口、引擎入口的配置，结束后回到终端 |
| `make soc-trace-serve` | 需要手工调用 Trace 接口时 | 持续运行 Trace HTTP 服务，按 `Ctrl+C` 停止 |

## 第 3 步：单独启动接口，认识“尚未接入引擎”的状态

在根目录打开**终端 A**，执行：

```bash
export TRACE_AGENT_API_KEY='trace-local-smoke-only'
export TRACE_AGENT_PORT=8300
unset TRACE_AGENT_ENGINE_FACTORY
make soc-trace-check
```

这里固定的 key 仅用于本机合成数据练习；正式联调使用私有配置中的专用 key。现在尚未接入引擎，预期看到 `not_ready`，命令以非零退出，`make` 可能显示 `Error 2`。**这一步是预期结果，不是安装失败。**

接着在终端 A 执行：

```bash
make soc-trace-serve
```

看到服务监听 `127.0.0.1:8300` 后，保持这个终端运行。该命令只启动 Trace，不会启动 SOC Runtime、网页、Redis 或原 Trace 的 worker。

再打开**终端 B**，检查接口：

```bash
curl --silent --show-error http://127.0.0.1:8300/health
```

未接引擎时预期返回 `status: degraded`、`detail: not_ready`。这表示 HTTP 接口已启动，真实调查能力尚未提供。需要停止时，回到终端 A 按 `Ctrl+C`。

## 第 4 步：用一个合成告警调用接口

这一步仍然不需要 SOC Runtime。保持第 3 步终端 A 的接口运行，在**终端 B**执行以下命令。临时文件只含人工数据：

```bash
export TRACE_AGENT_API_KEY='trace-local-smoke-only'
trace_demo_dir="$(mktemp -d)"
cat > "$trace_demo_dir/alert.json" <<'JSON'
{
  "alertId": "TRACE-DEMO-ALERT-001",
  "topic": "synthetic_trace_demo",
  "event_content": "合成告警，仅验证接口，不是真实安全事件"
}
JSON

curl --silent --show-error --include \
  -H "X-API-Key: $TRACE_AGENT_API_KEY" \
  -H 'X-Request-Id: trace-demo-create-001' \
  -F 'task_id=TRACE-DEMO-TASK-001' \
  -F "file=@$trace_demo_dir/alert.json;type=application/json" \
  --form-string 'evidence_gaps=仅使用模拟日志核对这条合成事件，不连接真实平台。' \
  http://127.0.0.1:8300/traces
```

此时预期是 **HTTP 503 / `TRACE_ENGINE_UNAVAILABLE`**，因为默认引擎没有接任务的能力。接口不会假装已创建任务。这个最小告警只用于检查传参格式；真实引擎接入后的业务测试需要使用符合其输入要求的合成完整告警和模拟工具。`evidence_gaps` 只是问题文本，不会替你配置或隔离工具。

等第 5 步实现引擎并配置好模拟模型、模拟日志源之后，才能验证受理和查询完整流程。创建成功通常返回 HTTP 202，复用已有任务可返回 200，响应包含 `taskId`、`status` 和 `X-Trace-Revision`。随后用同一个任务 ID 查询：

```bash
curl --silent --show-error --include \
  -H "X-API-Key: $TRACE_AGENT_API_KEY" \
  http://127.0.0.1:8300/traces/TRACE-DEMO-TASK-001
```

任务 ID 是本次调查的编号；告警 ID 是原始告警的编号，两者分别传递，不能让模型编造或互相替换。`PENDING`/`RUNNING` 只表示已受理/调查中，`SUCCESS` 才有完整报告；未接引擎时查询仍返回 503。

## 第 5 步：迁入真实调查代码

先按这个顺序打开文件，不需要从整个 SOC 项目开始读：

1. [`engine.py`](../backend/packages/trace-agent/src/trace_agent/engine.py)：你需要实现的五个方法。
2. [`contracts.py`](../backend/packages/trace-agent/src/trace_agent/contracts.py)：这些方法接收什么、返回什么。
3. [`service.py`](../backend/packages/trace-agent/src/trace_agent/service.py)：已经写好的 HTTP 接口怎样调用你的方法。
4. [`test_trace_agent_service.py`](../backend/tests/test_trace_agent_service.py)：如何用模拟引擎测试接口；测试里的替身不用于实际部署。

建议在 `backend/packages/trace-agent/src/trace_agent/` 下增加自己的 `investigation.py`、`implementation.py` 等模块，把旧包顶层的 `app.*` 导入改为 `trace_agent.*`。调查实现不要导入 `soc_agent` 或 Gateway 的 `app`，这样单独启动时就不依赖 SOC。

| 你要实现的方法 | 通俗解释 | 返回内容 |
| --- | --- | --- |
| `async create(request)` | 保存任务，检查重复，安排后台调查，尽快返回受理结果 | `TraceReply[TraceCreateResponse]` |
| `async get(task_id)` | 读取保存的状态和报告，不重新调查 | `TraceReply[TraceTaskResponse]` |
| `async resume(task_id, operation)` | 按约定恢复失败任务 | `TraceReply[TraceCreateResponse]` |
| `async cancel(task_id, operation)` | 取消任务，阻止旧 worker 再覆盖结果 | `TraceReply[TraceCreateResponse]` |
| `async health()` | 检查 API 所需的引擎、worker、存储是否可用 | `TraceHealth` |

`TraceReply` 就是“响应内容 + 保存的版本号”；新建任务时 `created=True`。版本号要从存储读取，不能在接口层随手填一个固定值。详细状态和重复请求约定见下方“开发参考：接口和业务边界”。

还需要一个普通的、无参数的函数 `create_engine()`，返回实现上述方法的引擎对象。例如，你完成 `InvestigationEngine` 后，`implementation.py` 可以采用以下入口形状（这两个实现模块当前尚不存在）：

```python
from .investigation import InvestigationEngine


def create_engine():
    return InvestigationEngine()
```

模型地址、日志工具、数据目录等配置由你的引擎读取本机私有配置；这段入口示意不会自动提供它们。真实 worker 的启动、退出和资源释放也要一并实现，当前 HTTP 外壳没有自动托管任意后台进程的能力。

实现完成后，停止终端 A 中的服务，在该终端设置入口并重新启动：

```bash
export TRACE_AGENT_ENGINE_FACTORY='trace_agent.implementation:create_engine'
make soc-trace-check
make soc-trace-serve
```

`check` 返回 `configured` 只代表配置填齐，不会导入并运行工厂。`serve` 才加载这个函数；`/health` 必须根据实际就绪情况返回 `ok`。第一次验证先使用你配置好的模拟模型和日志源，随后再在授权环境测试真实调查能力。

日常改完代码运行 `make soc-trace-test`。只想跑 Trace 自身用例时，可以使用：

```bash
(cd backend && uv run --no-sync python -m pytest tests/test_trace_agent_*.py -q)
```

## 交接验收：做到什么程度交给 SOC 联调

- 在不启动 SOC Runtime 的情况下，Trace 可以独立启动、创建任务、查询结果、恢复和取消任务。
- 用合成告警和模拟工具跑通完整调查流程，补充你自己的引擎测试，已有接口测试保持通过。
- 重发同一任务不会重复调查；重启服务后仍能查询；取消/恢复后旧 worker 不能覆盖新状态。
- 在交接说明中写清新增的配置名称、数据目录、worker 启停方法及真实工具依赖，不提交实际密钥、运行库或告警原文。
- 把本轮代码和共享锁文件一起提交。SOC 开发方随后接入实际触发、后台跟踪和 ZEUS 回填，再约双方联调。

## 常见问题

| 现象 | 原因和处理 |
| --- | --- |
| `make: No rule to make target 'soc-trace-test'` | 核对当前目录是否为仓库根目录，并确认代码含 `scripts/soc-trace.mk` |
| `No module named trace_agent` | 共享环境尚未安装本地 Trace 包，返回第 1 步；不要随意安装公网同名包 |
| `check` 返回 `not_ready` / `Error 2` | 尚未配置 key 或真实引擎入口；未完成第 5 步时正常 |
| `serve` 提示缺少 `TRACE_AGENT_API_KEY` | 在启动它的同一终端执行 `export`；示例 `.env.example` 不会自动加载 |
| 端口 `8300` 被占用 | 停止自己之前的 Trace 进程，或改 `TRACE_AGENT_PORT`，同时修改测试请求地址 |
| HTTP 401 | 请求的 `X-API-Key` 与服务启动时配置的不一致 |
| HTTP 422 | 告警文件、任务 ID 或请求头不符合格式，核对第 4 步和共享模型 |
| HTTP 503 / `degraded` | 未接入真实引擎，或引擎报告暂不可用；不表示 SOC Runtime 必须启动 |
| `configured` 后仍无法启动 | 工厂模块/函数可能不存在，或引擎初始化失败；`check` 只检查配置形式 |

## 开发参考：在哪里开发

| 位置 | 职责 | 开发方 |
| --- | --- | --- |
| `backend/packages/trace-agent/src/trace_agent/` | 共享接口模型、Trace HTTP 入口、调查引擎接口；后续放入真实调查实现 | Trace 同事 |
| `backend/soc_agent/integrations/trace_agent/` | SOC 调用客户端、从已有研判结果准备交接材料 | SOC 开发方 |
| `backend/tests/test_trace_agent_*.py` | Trace 接口、鉴权、启动和引擎契约测试 | Trace 同事与 SOC 开发方 |
| `backend/tests/test_soc_trace_*.py` | SOC 客户端、身份核对及交接材料测试 | SOC 开发方 |

Trace 作为独立后台进程，经 HTTP 接入 SOC。Gateway 不导入或启动 Trace 的调查循环，Trace 也不替换 DeerFlow 的 Lead Agent、Memory 或任务运行框架。本轮无需修改通用 DeerFlow core。

原 `validation/trace_agent/TraceAgent-20261103.zip` 用于核对旧实现，不是运行时导入路径。迁入时把旧代码的顶层 `app` 包改到 `trace_agent` 下，避免与当前 Gateway 的 `app` 包重名。

## 开发参考：共用 uv 和新增依赖

统一依赖入口是 `backend/pyproject.toml`，统一锁文件是 `backend/uv.lock`。Trace 是其中一个 workspace 包，发布名为 `soc-trace-agent`，Python 导入名为 `trace_agent`。

按第 1 步在自己的机器安装，生成本机的 `backend/.venv`。你和同事使用相同版本的依赖，但不互相复制 `.venv`，也不把 `.venv` 提交到仓库。

内网 Mac 继续使用现有 Host 安装入口：它从同一锁文件导出带哈希的依赖，通过批准的镜像安装，再把 workspace 包安装到同一 `.venv`。该安装清单已包含 `trace-agent`，无需另建环境或自行切换公网源。

本轮基础包只使用现有兼容的接口依赖。**原 Trace 依赖尚未全部合并**：原包固定 `openai==3.5.0`，当前 LangChain OpenAI 依赖要求 `<3`；原包固定 `cryptography==49.0.0`，当前项目要求 `>=50.0.0`。原包对 Python、uv 的精确版本要求也需一起调整。

迁入真实引擎时，先适配当前共同依赖。确实缺少依赖时，在 `backend/packages/trace-agent/pyproject.toml` 声明，在批准的依赖源环境下更新 `backend/uv.lock`，安装后执行测试，一起提交这两个文件。不要直接复制旧项目的锁文件或整套依赖覆盖当前配置。如果确实需要共同升级，附上 SOC 与 Trace 的兼容测试一起评审。

## 开发参考：配置与以后统一启停

可参考包内的 [`.env.example`](../backend/packages/trace-agent/.env.example) 注入配置；
该文件不自动加载，密钥默认留空，两个开关默认关闭。

三个命令的用途和操作示例见第 2～3 步。独立开发只需 Trace 服务端配置；下方 SOC 客户端及 Host 开关在以后集成时使用。

| 配置 | 用途 |
| --- | --- |
| `TRACE_AGENT_API_KEY` | Trace 接口鉴权密钥，从本机私有环境注入，不写入示例或 Git |
| `TRACE_AGENT_PORT` | Trace API 端口，默认 `8300`；默认只绑定 `127.0.0.1` |
| `TRACE_AGENT_ENGINE_FACTORY` | 真实引擎工厂，例如待同事实现的 `trace_agent.implementation:create_engine` |
| `SOC_TRACE_ENABLED` | Mac Host 统一启动流程的 Trace 开关，默认关闭；不代表启用自动调查 |

`make soc-trace-check` 只检查配置和工厂声明，不运行引擎、不验证 worker。没有工厂时返回 `not_ready` 并以非零退出；配置完整时的 `configured` 也不等于真实调查已可用。

配置密钥后，`make soc-trace-serve` 可启动开发用接口外壳。没有真实工厂时，健康状态为 `degraded`，任务调用返回不可用。真实工厂是**由部署者配置的、同步无参函数**，返回 `TraceEngine` 实例；HTTP 请求不能指定工厂或导入模块。

Mac Host 的统一启动、停止和状态流程已预留可选 Trace 进程。显式开启后须通过真实健康检查；未就绪会启动失败并清理本次启动的进程。不要为了通过检查让尚未实现的引擎返回 `ok`。

Docker 本轮只包含共享 workspace 包，不新增 Trace 运行服务；待真实引擎和 worker 生命周期就绪后再接入 Docker 的统一启停。单独运行上述开发命令不会启用 SOC 自动派发。

SOC 客户端单独读取下列配置。调用方显式执行 `TraceClientSettings.from_env(operator_env)`，导入模块不会自动读取环境或发送请求。

| 客户端配置 | 用途 |
| --- | --- |
| `SOC_TRACE_AGENT_ENABLED` | 允许显式调用客户端，默认 `false`；不是自动调查开关 |
| `SOC_TRACE_AGENT_BASE_URL` | Trace 地址，例如本机 `http://127.0.0.1:8300`，不含凭证、查询参数或 fragment |
| `SOC_TRACE_AGENT_API_KEY` | 对应 Trace 服务的密钥，与服务端私有配置保持一致 |
| `SOC_TRACE_AGENT_TIMEOUT_SECONDS` | 单次 HTTP 超时，默认 `30` 秒，不是整项调查时限 |
| `SOC_TRACE_AGENT_MAX_RESPONSE_BYTES` | 单次响应最大字节数，默认 `4194304`（4 MiB） |

`SOC_TRACE_ENABLED` 控制是否启动进程，`SOC_TRACE_AGENT_ENABLED` 控制是否允许客户端发送请求；两者都不会自动创建调查。部署时分别显式配置，不能把其中一个当作另一个。

## 开发参考：接口和业务边界

Trace 实现 `TraceEngine`，由 HTTP 入口调用。同事接入真实引擎时保留共享接口模型，调查算法、日志工具与报告生成放在引擎实现内。

| 接口 | 作用 |
| --- | --- |
| `POST /traces` | 接收上游 `task_id`、原始告警文件、可选 `evidence_gaps` |
| `GET /traces/{task_id}` | 查询当前状态和固定结构的结果 |
| `POST /traces/{task_id}/resume` | 显式恢复失败任务 |
| `POST /traces/{task_id}/cancel` | 请求取消 |
| `GET /health` | 返回真实就绪状态 |

调用前配置独立的 Trace API key。创建、查询和操作都先鉴权；重复请求也不能绕过鉴权。`/health` 可不带密钥，只返回就绪信息，不返回任务、日志或凭证。任务响应须携带文档约定的 `X-Trace-Revision`；恢复与取消使用稳定的 `X-Request-Id` 和 `X-Expected-Trace-Revision`。版本、重复请求复用和恢复状态需要真实引擎持久保存，基础 HTTP 外壳不会代替它实现可靠任务队列。

三方公共 `taskId` 与告警 ID、租户和环境绑定。SOC 的内部 `run_id` 是分析记录编号，不由客户端擅自替换公共任务编号；Trace 从保存的输入返回身份，不让模型生成身份。

客户端使用 `async with TraceClient(settings) as client` 管理连接；创建时明确传入 `task_id`、`expected_alert_id`、告警、问题和稳定的 `request_id`。创建／恢复／取消的受理响应可能只有任务 ID 和状态，因此返回的 `alert_identity_verified=False` 不能当作报告已核验；随后 `get(task_id, expected_alert_id=...)` 取得完整结果，才核对任务和告警两种身份。客户端不自动重试或轮询。

SOC 交接使用**当前有效研判结果**和已有后续建议。已经被审核经验等解决的旧问题不能重新变成调查要求。材料保留事实、初判观点及建议来源；要提交的问题由调用方明确选择，不把所有 `manual_checks` 自动转换成调查问题，也不靠“确认”“授权”等关键词判定是否能交给 Trace。

例如，“查询同账号前后登录记录”可以交给已具备相应日志能力的 Trace；“请业务负责人确认操作意图”可能仍需运营处理。有没有相关工具、权限和日志，需要接入真实引擎后验证。本轮不修改初判提示词，也不增加一轮专门寻找缺口的模型调用。

按三方文档第 6、12 节，后续调查来源是新行为、抽样、明确的调查需求；分类和调用选择独立。“待核实”不表示必须调查，“已转交”也不表示必须停止调查。初判和转交不等待 Trace；调查结果追加保存，不覆盖人工确认或重放转交、封堵等处置。

## 开发参考：双方负责什么

1. **迁入引擎并适配共同依赖。** 在 `trace_agent` 内实现 `TraceEngine`，接入原来的日志查询、调查和报告逻辑；配置、资源文件和导入路径不能依赖旧工作目录。
2. **实现任务持久化。** 同任务、同输入复用；输入不同拒绝覆盖。状态与版本来自同一快照；成功任务不因重发重跑，恢复和取消不能被旧 worker 的结果覆盖。
3. **完善对外结果。** 使用规定的六种状态和四类分类；非成功状态保留告警身份，结果字段为 `null`；成功报告附上简短事实依据。自动恢复耗尽时返回 `FAILED`，不能永久停在可恢复状态。
4. **接好启动与健康检查。** 真实 API、worker 和必要资源就绪后才报告可用。工厂返回一个引擎实例，不等于 HTTP 外壳已经管理其 worker；后台进程的启动、退出和资源释放也需完成。调查并发、工具预算、数据目录及恢复设置由 Trace 明确配置，模型或日志源故障不得阻断 SOC 初判。
5. **补上真实引擎测试。** 先用合成告警和替身模型、日志工具验证完整生命周期，再在批准的环境验证真实日志能力。密钥、告警原文、运行数据库和报告不提交到 Git。

Trace 数据与 SOC 数据分别管理。原实现若继续使用 SQLite WAL，运行库必须满足项目的 SQLite 版本检查，数据库放在受支持的本地盘或块存储，不直接放 NFS/SMB 共享 NAS。Trace 的内部经验不能直接写成已审核的 SOC Memory。

SOC 开发方后续负责实际的触发策略、任务登记与后台跟踪、页面结果展示，以及分阶段 ZEUS 回填。Trace 不持有 ZEUS callback 凭证，也不独立执行初判里的处置动作。

## 开发参考：自动化验证范围

从仓库根执行：

```bash
make soc-trace-test
```

离线测试使用合成材料和进程内 HTTP 测试服务，不连接真实模型、日志平台、Redis 或 ZEUS，也不把模拟报告写入真实研判记录。测试验证接口格式、身份核对、失败处理、鉴权和调用边界；它不证明原 Trace 引擎已迁入，或生产调查已可用。

默认引擎明确返回未就绪：健康状态为 `degraded`，任务调用返回不可用，而不是生成假报告。自动调用保持关闭。本轮无需数据库迁移、生产发布或重启当前 SOC 服务；后续真实联调另行启用。
