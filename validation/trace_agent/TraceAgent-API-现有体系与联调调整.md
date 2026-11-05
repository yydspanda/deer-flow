# TraceAgent API 体系与联调调整

# 第一部分：现有 API 体系（当前交付版）

> 本部分仅保留当前交付包中的告警任务接入、管理、过程与结果查询，以及健康检查，共 **9 个操作、7 个路径**。内容按当前源码整理，示例均为演示值。第二部分说明联调调整内容。

## 1. 服务与约定

| 项 | 值 |
|---|---|
| 服务名称 | TraceAgent API |
| 示例 Base URL | `http://127.0.0.1:8300` |
| OpenAPI | `/openapi.json` |
| 响应格式 | 除删除接口外，均为 JSON |

约定：

- 路径中的 `{task_id}` 为创建任务返回的 UUID，不是原始告警 ID。
- 任务列表按 `pageToken` / `nextPageToken` 分页，空串表示末页。
- 主任务状态：`created`（已创建）、`running`（执行中）、`resumable`（可续跑）、`completed`（已完成）、`cancelled`（已取消）。
- `userPrompt` 用于传入 Gap，**每个非空行一项**，不要添加开场白、总标题或手工编号。
- 响应字段按实际名称使用；任务模型主要为 camelCase，事件和产物中的部分字段为 snake_case。

### 错误格式

```json
{
  "code": "TRACE_NOT_FOUND",
  "status": 404,
  "message": "任务不存在：...",
  "details": {}
}
```

| HTTP | 常见原因 |
|---|---|
| 400 | 上传内容、JSON、大小或提示词不合法 |
| 404 | 任务不存在、报告未产出 |
| 409 | 当前状态不允许取消、续跑或删除 |
| 422 | 参数缺失、类型或范围错误 |
| 500 | 任务目录删除失败等 |

## 2. 接口清单

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/traces` | 创建单个告警任务 |
| GET | `/traces` | 查询任务列表 |
| GET | `/traces/{task_id}` | 查询任务详情 |
| POST | `/traces/{task_id}/cancel` | 请求取消任务 |
| POST | `/traces/{task_id}/resume` | 请求续跑任务 |
| DELETE | `/traces/{task_id}` | 删除任务 |
| GET | `/traces/{task_id}/events` | 查询事件时间线 |
| GET | `/traces/{task_id}/report` | 获取最终研判报告 |
| GET | `/health` | 健康检查 |

---

## 3. 任务管理

### 3.1 POST /traces — 创建任务

**请求：** `multipart/form-data`；成功返回 **202**。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | 文件 | 是 | UTF-8 告警 JSON，顶层为对象；默认不超过 10 MiB |
| `userPrompt` | 字符串 | 否 | Gap 清单，每个非空行一项，最多 32,000 字符 |

```bash
curl -X POST "$BASE_URL/traces" \
  -F 'file=@alert.json;type=application/json' \
  -F 'userPrompt=缺少该次登录是否获得授权的证据'
```

**输出：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `taskId` | 字符串 | 新建任务 UUID |
| `status` | 字符串 | `created` |

```json
{
  "taskId": "11111111-1111-4111-8111-111111111111",
  "status": "created"
}
```

返回 202 只表示任务已创建并登记派发，不表示研判已完成。

### 3.2 GET /traces — 查询任务列表

| 查询参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `status` | 字符串 | 不筛选 | 按任务状态筛选 |
| `pageToken` | 字符串 | 首页 | 上一页的 `nextPageToken` |
| `limit` | 整型 | 20 | 1～100 |
| `createdSince` | 字符串 | 不筛选 | 创建日期下界，格式 `YYYY-MM-DD` |

```bash
curl "$BASE_URL/traces?status=created&limit=20"
```

**输出：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `traces` | 对象数组 | 任务摘要列表 |
| `nextPageToken` | 字符串 | 下一页游标，空串表示末页 |

```json
{
  "traces": [{
    "taskId": "11111111-1111-4111-8111-111111111111",
    "status": "created",
    "fingerprint": "aaaa...",
    "createdAt": "2026-11-03T15:30:00+00:00",
    "startedAt": null,
    "finishedAt": null,
    "completeness": null,
    "riskLevel": null,
    "confidence": null,
    "forcedAccept": null,
    "verdict": null,
    "totalInputTokens": null,
    "totalOutputTokens": null,
    "numTurns": null,
    "execDurationS": null,
    "toolCallCount": 0,
    "reflectionStatus": null,
    "reflectionReason": null
  }],
  "nextPageToken": ""
}
```

任务摘要共 18 个字段，主要类型如下：

| 类型 | 字段 |
|---|---|
| 字符串 | `taskId`、`status`、`fingerprint` |
| 字符串/null | `reflectionStatus`、`reflectionReason`、`createdAt`、`startedAt`、`finishedAt`、`riskLevel`、`verdict` |
| 数值/null | `completeness`、`confidence`、`execDurationS` |
| 整型/null | `totalInputTokens`、`totalOutputTokens`、`numTurns` |
| 布尔/null | `forcedAccept` |
| 整型 | `toolCallCount` |

### 3.3 GET /traces/{task_id} — 查询任务详情

无查询参数和请求体。

```bash
curl "$BASE_URL/traces/$TASK_ID"
```

返回任务摘要的 18 个字段，另加以下 12 个字段，共 **30 个顶层字段**。

| 字段 | 类型 | 说明 |
|---|---|---|
| `dispatch` | 对象数组 | 主任务和复盘派发记录 |
| `alertBytes` | 整型 | 入账告警字节数 |
| `promptLen` | 整型 | 提示词长度 |
| `finishNote` | 字符串 | 结束或中断说明 |
| `userPrompt` | 字符串/null | 最多展示 200 字符 |
| `phase` | 字符串/null | 当前处理阶段 |
| `entitySubmits`、`reportSubmits` | 整型/null | 实体、报告提交次数 |
| `entityCount` | 整型/null | 接受的实体数量 |
| `reportAcceptedAt` | 字符串/null | 报告验收时间 |
| `modelName` | 字符串/null | 模型名称 |
| `totalNumTurns` | 整型/null | 消息快照聚合轮数 |

**主要响应示例：**

```json
{
  "taskId": "11111111-1111-4111-8111-111111111111",
  "status": "created",
  "fingerprint": "aaaa...",
  "createdAt": "2026-11-03T15:30:00+00:00",
  "startedAt": null,
  "finishedAt": null,
  "completeness": null,
  "riskLevel": null,
  "confidence": null,
  "forcedAccept": null,
  "verdict": null,
  "totalInputTokens": 0,
  "totalOutputTokens": 0,
  "numTurns": 0,
  "execDurationS": null,
  "toolCallCount": 0,
  "reflectionStatus": null,
  "reflectionReason": null,
  "dispatch": [],
  "alertBytes": 256,
  "promptLen": 0,
  "finishNote": "",
  "userPrompt": null,
  "phase": "awaiting_entities",
  "entitySubmits": 0,
  "reportSubmits": 0,
  "entityCount": null,
  "reportAcceptedAt": null,
  "modelName": null,
  "totalNumTurns": 0
}
```

`dispatch[]` 包含 `kind`、`state`、`attempts`、`created_at`、`sent_at`、`claimed_at`、`next_attempt`、`last_error`；其中时间字段为 Unix 秒。

### 3.4 POST /traces/{task_id}/cancel — 请求取消

路径参数 `task_id` 必填。请求体可省略；如提供则为 JSON：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | 字符串 | 否 | 取消原因 |

```bash
curl -X POST "$BASE_URL/traces/$TASK_ID/cancel" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"人工接管调查"}'
```

```json
{
  "taskId": "11111111-1111-4111-8111-111111111111",
  "status": "cancelled"
}
```

返回 `cancelled` 表示账本状态已更新，不代表执行进程已经退出。

### 3.5 POST /traces/{task_id}/resume — 请求续跑

仅 `created`、`resumable` 状态允许；无请求体。

```bash
curl -X POST "$BASE_URL/traces/$TASK_ID/resume"
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `taskId` | 字符串 | 任务 ID |
| `status` | 字符串 | 请求时的任务状态 |
| `dispatchStatus` | 字符串 | 主任务派发状态 |

```json
{
  "taskId": "11111111-1111-4111-8111-111111111111",
  "status": "resumable",
  "dispatchStatus": "pending"
}
```

成功返回 **202**，只表示已登记续跑派发意图。

### 3.6 DELETE /traces/{task_id} — 删除任务

无查询参数和请求体。成功返回 **204**，无正文。

```bash
curl -X DELETE "$BASE_URL/traces/$TASK_ID"
```

删除任务目录、账本、事件和派发记录，不删除会话审计，不撤销知识。运行中、排队中或复盘进行中的任务可能被拒绝。取消请求不等于执行已经退出，删除前须确认执行已停止。

---

## 4. 过程与结果

### 4.1 GET /traces/{task_id}/events — 事件时间线

无查询参数和请求体。

```bash
curl "$BASE_URL/traces/$TASK_ID/events"
```

| 输出字段 | 类型 | 说明 |
|---|---|---|
| `events` | 对象数组 | 按入账序号排列的事件 |
| `events[].at` | 字符串 | 事件时间 |
| `events[].type` | 字符串 | 事件类型 |
| `events[].from_state` | 字符串/null | 原状态 |
| `events[].to_state` | 字符串/null | 新状态 |
| `events[].detail` | 字符串 | 事件说明 |
| `events[].level` | 字符串 | `info` / `warning` / `error` |

```json
{
  "events": [{
    "at": "2026-11-03T15:30:00+00:00",
    "type": "created",
    "from_state": "",
    "to_state": "created",
    "detail": "任务创建",
    "level": "info"
  }]
}
```

### 4.2 GET /traces/{task_id}/report — 最终研判报告

无查询参数和请求体。

```bash
curl "$BASE_URL/traces/$TASK_ID/report"
```

成功返回 **200**，共 **10 个顶层字段**。

| 字段 | 类型 | 说明 |
|---|---|---|
| `report` | 字符串 | Markdown 报告正文 |
| `verdict` | 字符串/null | 告警定性 |
| `riskLevel` | 字符串/null | 风险等级；误报时为 null |
| `completeness` | 数值/null | 报告完整度，通常为 0～1 |
| `confidence` | 数值/null | 实体可追溯程度，不是研判正确概率 |
| `forcedAccept` | 布尔/null | 是否强制收档 |
| `gapCoverage` | 对象/null | Gap 结构回应覆盖情况 |
| `sanitized` | 字符串数组 | 报告净化说明 |
| `submitsUsed` | 整型/null | 验收时累计提交次数 |
| `acceptedAt` | 字符串/null | 报告验收时间 |

```json
{
  "report": "# 溯源报告\n\n（完整 Markdown 正文）",
  "verdict": "误报",
  "riskLevel": null,
  "completeness": 0.96,
  "confidence": 1.0,
  "forcedAccept": false,
  "gapCoverage": {
    "mode": "structural",
    "required": 1,
    "covered": [1],
    "missing": [],
    "unexpected": [],
    "duplicates": [],
    "placeholderItems": [],
    "complete": true
  },
  "sanitized": [],
  "submitsUsed": 1,
  "acceptedAt": "2026-11-03T23:35:00+08:00"
}
```

`gapCoverage.complete=true` 只表示逐项结构回应齐全，不代表缺失证据已经补齐。报告未产出时返回 **404**：

```json
{
  "code": "HTTP_404",
  "status": 404,
  "message": "报告未产出（质检未通过或未提交）",
  "details": {}
}
```

---

## 5. 健康检查

### GET /health

| 查询参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `full` | 布尔 | false | 为 true 时增加 worker 检查 |

```bash
curl "$BASE_URL/health"
```

```json
{
  "database": "up",
  "broker": "up",
  "status": "ok"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `database` | 字符串 | `up` / `down` |
| `broker` | 字符串 | `up` / `down` |
| `worker` | 字符串 | 仅 `full=true` 时出现，`up` / `down` |
| `status` | 字符串 | `ok` / `degraded` |

探测失败时仍可能返回 HTTP **200**，应读取 `status` 判断，不能只看 HTTP 状态码。

---

# 第二部分：联调 API 调整

> 本部分说明与 SOC Runtime 融合时需要调整的接口参数、状态与返回结构。

## 1. 改造范围

保留现有接口路径，只调整任务创建参数、状态表达和结果返回结构。现有体系中的“任务详情、事件时间线、最终报告”不再作为三个独立联调接口，统一收敛为**研判结果查询**。`events` 不再以事件数组对外返回，而是投影为结果中的当前 `status`。

| 操作 | 方法与路径（保持不变） | 名称 | 说明 |
|---|---|---|---|
| 创建 | `POST /traces` | 创建研判任务 | 上游指定任务 ID，提交告警和 Gap |
| 查询 | `GET /traces/{task_id}` | 查询研判结果 | 返回任务当前状态及最终取证结果 |

字段约定：

- 创建表单的字段使用 snake_case：`task_id`、`evidence_gaps`。
- 返回体沿用给定示例：`taskId`、`alert_id`、`riskLevel`。
- `task_id` / `taskId` 表示同一次研判任务；`alert_id` 表示上游原始预警 ID，两者不能混用。

### 1.1 调整后接口总览

| 现有接口 | 调整后的归属 | 具体变化 |
|---|---|---|
| `POST /traces` | 保留，创建研判任务 | 路径不变；参数改为 `file + task_id + evidence_gaps` |
| `GET /traces` | 保留，任务列表 | 不变 |
| `GET /traces/{task_id}` | 保留路径，统一为查询研判结果 | 合并原任务详情、事件状态、最终报告；返回 `taskId + status + result` |
| `GET /traces/{task_id}/events` | 合并到 `GET /traces/{task_id}` | 不再单独提供；事件状态投影为 `status` |
| `GET /traces/{task_id}/report` | 合并到 `GET /traces/{task_id}` | 不再单独提供；报告及质量指标进入 `result` |
| `POST /traces/{task_id}/cancel` | 保留 | 不变 |
| `POST /traces/{task_id}/resume` | 保留 | 不变 |
| `DELETE /traces/{task_id}` | 保留 | 不变 |
| `GET /health` | 保留 | 不变 |

调整后共 **7 个操作、5 个路径**。其中只有创建参数和 `GET /traces/{task_id}` 的返回结构发生变化，其他接口不变。

## 2. 创建研判任务

### 2.1 请求

```http
POST /traces
Content-Type: multipart/form-data
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | 文件 | 是 | 上游原始告警 JSON |
| `task_id` | 字符串 | 是 | **上游分配并传入**；TraceAgent 不再自动生成 |
| `evidence_gaps` | 字符串 | 否 | 原 `userPrompt`；每个非空行一个 Gap |

```bash
curl -X POST "$BASE_URL/traces" \
  -F 'file=@alert.json;type=application/json' \
  -F 'task_id=example-task-id' \
  -F 'evidence_gaps=缺少该次登录是否获得授权的证据'
```

`evidence_gaps` 对应上游：

```text
analysis_run.analysis.evidence_gaps
```

将字符串数组按原顺序以换行连接后传入，不添加总标题、开场白或手工编号。Gap 的查证过程与结论体现在最终 `report` 中。

实现影响：

- 任务登记、目录、账本、派发、续跑及结果查询均使用上游 `task_id`。
- 当前 UUID 格式校验需调整为双方约定的上游 ID 校验。
- `alert_id` 从原始告警的约定字段读取并保存，供结果返回使用。

### 2.2 受理响应

创建接口保持异步受理，返回：

```json
{
  "taskId": "example-task-id",
  "status": "PENDING"
}
```

受理成功仅表示任务已登记，不表示调查完成。

## 3. 查询研判结果

### 3.1 统一返回结构

```http
GET /traces/{task_id}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `taskId` | 字符串 | 上游传入的任务 ID |
| `status` | 字符串 | 当前任务状态，由内部事件与状态投影而来 |
| `result` | 对象/null | 最终取证结果；任务未完成时为 null |
| `result.alert_id` | 字符串 | 上游原始预警 ID |
| `result.report` | 字符串 | 完整 Markdown 溯源报告 |
| `result.verdict` | 字符串 | 风险属实 / 正常行为 / 检测误报 / 待核实 |
| `result.riskLevel` | 字符串 | 风险等级；无等级时返回 `""` |
| `result.completeness` | 数值 | 报告完整度 |
| `result.confidence` | 数值 | 实体证据可追溯程度，不是研判正确概率 |

成功示例：

```json
{
  "taskId": "example-task-id",
  "status": "SUCCESS",
  "result": {
    "alert_id": "100002",
    "report": "# 溯源报告\n\n## 摘要\n- 定性：检测误报\n\n（其余报告内容省略）",
    "verdict": "检测误报",
    "riskLevel": "",
    "completeness": 0.96,
    "confidence": 1.0
  }
}
```

该结构取代当前分别暴露的任务详情、事件时间线和报告响应。原任务详情的运行指标、事件列表，以及 `forcedAccept`、`gapCoverage`、`sanitized`、`submitsUsed`、`acceptedAt` 等内部验收信息，不在精简联调结果中返回，可继续保留在内部用于排障。

### 3.2 events 到 status 的映射

`status` 是当前状态，不是历史事件列表。映射如下：

| 内部状态或事件 | 联调 `status` | 含义 |
|---|---|---|
| 已创建、等待派发 | `PENDING` | 已受理，尚未开始执行 |
| 已认领、正在调查取证 | `RUNNING` | 正在执行 |
| 可续跑中断（`resumable`） | `RETRYING` | 等待或正在续跑，不能直接视为最终失败 |
| 报告已验收、主任务完成 | `SUCCESS` | 已产生最终取证结果 |
| 用户取消 | `CANCELLED` | 已请求取消 |
| 不可恢复的执行失败 | `FAILED` | 无法形成正常结果 |

任务未完成时 `result=null`：

```json
{
  "taskId": "example-task-id",
  "status": "RUNNING",
  "result": null
}
```

`SUCCESS` 只表示研判任务成功，不表示转交、封堵、隔离等处置动作已经执行成功。
