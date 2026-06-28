# SOC Agent Tooling Selection Reference

## Source

`/home/yydspei/projects/system-prompts-and-models-of-ai-tools/tools`

## Useful References

| Topic | Reference | SOC Agent Usage |
|---|---|---|
| LLM JSON parsing | `json_repair使用指南.md` | Repair malformed LLM JSON after strict parse fails; keep Pydantic/schema validation as the authority. |
| Model abstraction | `litellm/LITELLM_TUTORIAL.md` | Candidate `LLMClient` adapter for OpenAI-compatible providers, token counting, async calls, and parameter tolerance. |
| Structured extraction | `LangExtract/README.md` | Candidate extractor for unstructured alert descriptions, long logs, reports, and character-level grounding. |
| Long-document RAG | `pageindex/README.md` | Phase 5 document retriever for SOPs, PDFs, product manuals, and security reports. |
| Queue design | `queue/LLM_QUEUE_GUIDE.md` | Use in-process queue first; prefer PostgreSQL-backed queue before Celery for SOC Agent scale. |
| Python architecture patterns | `python-advance/python-advanced-concepts.md` | Use `ContextVar` for async context isolation and `Protocol` for replaceable interfaces. |
| Code quality | `code-quality/CODE_QUALITY_TUTORIAL.md` | Add ruff/pre-commit/CI checks early. |
| Local commands | `makefile/MAKEFILE_TUTORIAL.md` | Keep common SOC Agent commands behind `make` targets. |
| Git workflow | `git-operation/GIT_TUTORIAL.md` | Keep upstream sync and fork workflow as development practice. |

## Decision

Do not introduce every tool as a runtime dependency. For MVP, prioritize:

1. `json_repair` as a guarded fallback for LLM JSON output.
2. `Protocol` boundaries for queue, memory, LLM, and knowledge retrieval adapters.
3. Lightweight in-process queue first; PostgreSQL-backed queue when daemon persistence is needed.
4. Ruff/test/CI quality gates before business logic grows.

Defer LiteLLM, LangExtract, PageIndex, and GraphRAG until real data shows the exact integration pressure.
