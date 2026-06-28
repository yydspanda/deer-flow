# task-queue-selection.md

| 问题 | 最佳参考位置 | 要点 |
|---|---|---|
| SOC Agent 是否需要 Celery | `/home/yydspei/projects/system-prompts-and-models-of-ai-tools/tools/queue/LLM_QUEUE_GUIDE.md` | 日 1 万告警量级不需要 Celery；主要瓶颈是 LLM/API 调用，不是队列框架调度。 |
| Phase 1 队列怎么做 | `/home/yydspei/projects/system-prompts-and-models-of-ai-tools/tools/queue/LLM_QUEUE_GUIDE.md` | 进程内 `PriorityQueue` + `Semaphore` 足够做 CLI/少量并发和 API 限流；不保证持久化。 |
| Phase 4 daemon 队列怎么做 | `/home/yydspei/projects/system-prompts-and-models-of-ai-tools/tools/queue/LLM_QUEUE_GUIDE.md` | 已有 PostgreSQL 时优先 PgQueuer / PG-backed queue，利用 PG 事务、`LISTEN/NOTIFY`、`SKIP LOCKED`，避免额外 Redis/RabbitMQ。 |
| 本项目落点 | `.notes/ai_soc/soc-agent-solution.md` | 抽象 `TaskQueue` 接口；Phase 1 使用进程内实现，Phase 4 替换为 PostgreSQL-backed queue。 |
