# DB Memory to Wiki / OKF Projection

> 状态：Deferred / presentation and collaboration layer。SOC memory 继续以 PostgreSQL 中的受治理
> typed records 为唯一事实源；Wiki/OKF 只可能是后期展示、审阅和知识协作投影。

## 为什么保留

DB 适合保证 memory candidate、人工确认、retrieval activation、版本、有效期、审计和并发一致性，
但不一定是分析师阅读、复盘和跨团队维护知识的最佳界面。Wiki/OKF 可以在后期提供：

- 按场景、资产、检测来源和经验类型组织的可读知识视图。
- 经验来源、适用范围、有效期和 reviewer 的审阅页面。
- 面向运营复盘的链接、说明和变更历史。
- 在不暴露完整业务表的前提下进行知识浏览与协作。

## 为什么现在不做

- 当前优先验证 DB-first memory 生命周期和实际检索价值。
- 还没有稳定的 Wiki/OKF 目标格式、权限模型、owner 和真实协作工作流。
- 过早双写会制造两个事实源，并引入同步冲突、过期知识和错误回写风险。
- 它是展示/协作能力，不阻塞 SOC Runtime、Lead Agent 或真实 provider 接入。

## 固定一致性模型

```text
PostgreSQL governed memory records (source of truth)
  -> versioned export job
  -> read-oriented Wiki / OKF projection
  -> analyst edit or suggestion
  -> proposal / memory candidate
  -> governed review
  -> new DB version
  -> next projection
```

- DB -> Wiki/OKF 是带版本和 provenance 的单向投影。
- Wiki/OKF 页面不得直接修改 DB record，也不得直接启用 retrieval。
- Wiki/OKF 中的编辑只能形成 proposal 或 pending memory candidate，经现有治理服务确认后生成新版本。
- 删除或隐藏投影不等于删除 DB 审计历史。

## 未来投影内容

- memory id、type、title/summary 和适用 facets。
- source、evidence references、tenant scope、validity 和 review deadline。
- confirmed/retrieval status、record version 和 projection timestamp。
- superseded/deprecated lineage，以及内容 hash。

默认不投影原始告警、凭证、token、完整请求体或其他不必要的敏感字段。

## 非目标

- 不用 Wiki/OKF 替代 PostgreSQL、repository 或 memory governance service。
- 不让 Wiki 页面成为 Runtime Prompt 的无边界知识源。
- 不通过文件修改绕过 role、reason、expected version、validity 和 audit 约束。
- 不因为有可读页面就把 candidate 或过期 record 当成可检索 confirmed memory。

## 重新启动条件

- DB memory candidate/review/retrieval lifecycle 在真实使用中稳定。
- 分析师提出明确的浏览、复盘或协作需求，DB/API/Web 当前视图不足以满足。
- 选定 Wiki/OKF 目标、权限边界、同步 owner 和刷新策略。
- 完成敏感字段 projection policy，并在 `delivery-roadmap.md` 中重新排期。

## 验收标准

- 任一投影页面都能追溯到唯一 DB record/version/hash。
- 重复导出幂等，失败可重试，不产生第二事实源。
- Wiki/OKF 侧修改只生成 proposal/candidate，不能直接改变 retrieval 或 Runtime 行为。
- record 过期、废弃或新版本发布后，投影视图可确定性更新并保留审计 lineage。

