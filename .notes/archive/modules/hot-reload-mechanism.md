# DeerFlow 热重载机制

> 改了 `config.yaml` 不用重启服务，下次请求自动生效。
> 没有 watchdog、没有 inotify、没有后台线程，就是一个文件修改时间戳检查。

## 一句话原理

**每次有人读配置时，顺便看一眼文件的 mtime（修改时间戳），变了就重新加载。** 懒检查，零额外开销。

## 完整数据流（以 TitleConfig 为例）

```
用户改了 config.yaml
  title:
    max_words: 6 → 3

                    ┌─────────────────────────────────────────────┐
                    │ 下一次 HTTP 请求进来                         │
                    └──────────────┬──────────────────────────────┘
                                   ▼
              TitleMiddleware._get_title_config()
                    │
                    │ 优先用构造时传入的 app_config.title（直接内存读取，0 开销）
                    │ 如果没有 → 走全局单例 get_title_config()
                    ▼
              get_title_config()           ← title_config.py:43
                    │
                    │ 返回全局变量 _title_config
                    │ 这个全局变量是什么时候更新的？↓
                    ▼
              另一条路径（任何地方调 get_app_config() 时触发）：

              get_app_config()             ← app_config.py:362
                    │
                    ├─ ① 读 config.yaml 的 mtime（文件修改时间戳）
                    │     current_mtime = config_path.stat().st_mtime
                    │
                    ├─ ② mtime 和上次一样？→ 直接返回缓存的 _app_config（最快路径）
                    │
                    └─ ③ mtime 变了！→ 重新加载
                          │
                          ▼
                    AppConfig.from_file("config.yaml")     ← app_config.py:146
                          │
                          ├─ YAML 解析 → dict
                          ├─ Pydantic 验证（max_words=3, max_chars=60, ...）
                          │     如果验证失败 → 报错，旧配置不变（安全）
                          └─ _apply_singleton_configs()    ← app_config.py:190
                                │
                                ├─ load_title_config_from_dict({"max_words": 3, ...})
                                │     └─ _title_config = TitleConfig(max_words=3)  ✅ 更新了！
                                ├─ load_summarization_config_from_dict(...)
                                ├─ load_memory_config_from_dict(...)
                                ├─ load_guardrails_config_from_dict(...)
                                ├─ load_checkpointer_config_from_dict(...)
                                ├─ load_stream_bridge_config_from_dict(...)
                                ├─ load_subagents_config_from_dict(...)
                                ├─ load_tool_search_config_from_dict(...)
                                └─ load_acp_config_from_dict(...)
```

## 涉及的子配置（全部通过同一机制热重载）

每个子配置都是同一个模式：全局变量 + `get_xxx_config()` + `load_xxx_config_from_dict()`。

| 子配置 | 配置文件 | 全局变量 | 热重载函数 |
|--------|---------|---------|-----------|
| TitleConfig | `title_config.py` | `_title_config` | `load_title_config_from_dict()` |
| SummarizationConfig | `summarization_config.py` | `_summarization_config` | `load_summarization_config_from_dict()` |
| MemoryConfig | `memory_config.py` | `_memory_config` | `load_memory_config_from_dict()` |
| GuardrailsConfig | `guardrails_config.py` | `_guardrails_config` | `load_guardrails_config_from_dict()` |
| CheckpointerConfig | `checkpointer_config.py` | `_checkpointer_config` | `load_checkpointer_config_from_dict()` |
| StreamBridgeConfig | `stream_bridge_config.py` | `_stream_bridge_config` | `load_stream_bridge_config_from_dict()` |
| SubagentsConfig | `subagents_config.py` | `_subagents_config` | `load_subagents_config_from_dict()` |
| ToolSearchConfig | `tool_search_config.py` | `_tool_search_config` | `load_tool_search_config_from_dict()` |
| AgentsApiConfig | `agents_api_config.py` | `_agents_api_config` | `load_agents_api_config_from_dict()` |
| ACP Config | `acp_config.py` | `_acp_agents_config` | `load_acp_config_from_dict()` |
| ExtensionsConfig | `extensions_config.py` | `_extensions_config` | `reload_extensions_config()` |

## 核心代码（3 个函数 + 4 个全局变量）

### 1. 惰性检查 + 自动重载

```python
# app_config.py:334-391

_app_config: AppConfig | None = None          # 缓存的配置实例
_app_config_path: Path | None = None           # 上次加载的文件路径
_app_config_mtime: float | None = None         # 上次加载时文件的 mtime
_app_config_is_custom = False                   # 是否是测试注入的自定义配置

def get_app_config() -> AppConfig:
    global _app_config, _app_config_path, _app_config_mtime

    # ① 如果有 ContextVar 注入（测试场景），直接返回
    runtime_override = _current_app_config.get()
    if runtime_override is not None:
        return runtime_override

    # ② 如果是手动注入的自定义配置，不重新加载
    if _app_config is not None and _app_config_is_custom:
        return _app_config

    # ③ 读当前文件的 mtime
    resolved_path = AppConfig.resolve_config_path()
    current_mtime = _get_config_mtime(resolved_path)

    # ④ 核心：mtime 变了 → 重新加载
    should_reload = (
        _app_config is None                         # 首次
        or _app_config_path != resolved_path        # 路径变了
        or _app_config_mtime != current_mtime       # 文件被改了
    )
    if should_reload:
        _load_and_cache_app_config(str(resolved_path))
    return _app_config
```

### 2. 子配置同步（重新加载时，把新值写到所有子配置的全局变量）

```python
# app_config.py:190-204

def _apply_singleton_configs(cls, config, acp_agents):
    load_title_config_from_dict(config.title.model_dump())
    load_summarization_config_from_dict(config.summarization.model_dump())
    load_memory_config_from_dict(config.memory.model_dump())
    # ... 10 个子配置全部更新
```

### 3. 子配置的全局单例模式（每个子配置都是同一套）

```python
# title_config.py（所有子配置都是这个模式）

_title_config: TitleConfig = TitleConfig()          # 全局单例，默认值

def get_title_config() -> TitleConfig:              # 读
    return _title_config

def set_title_config(config: TitleConfig) -> None:  # 写（测试用）
    global _title_config
    _title_config = config

def load_title_config_from_dict(config_dict: dict): # 从 AppConfig 同步（热重载用）
    global _title_config
    _title_config = TitleConfig(**config_dict)       # Pydantic 验证 + 覆盖旧值
```

## 为什么不用文件监听（watchdog / inotify）？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **DeerFlow 的做法（mtime 惰性检查）** | 零依赖、零线程、零复杂度 | 如果没人调 `get_app_config()`，配置不会更新 |
| **文件监听（watchdog）** | 配置改了立即生效 | 需要后台线程、额外依赖、平台兼容性问题 |
| **信号量（SIGHUP）** | 按需触发 | 需要 nginx/systemd 集成、Windows 不支持 |

DeerFlow 选 mtime 方案的原因：
- **每次 HTTP 请求都会调 `get_app_config()`**（创建 Agent 时），所以配置最多延迟一个请求
- 不需要额外线程和依赖库
- 不需要处理文件监听的边缘情况（文件移动、权限变化、NFS 等）

## Pydantic 的作用：验证防火墙

```python
# 用户在 config.yaml 里写错了
title:
  max_words: -1       # 无效值

# 加载时 Pydantic 直接报错，旧配置保持不变
# ValidationError: Input should be greater than or equal to 1
```

如果用 dict，`max_words: -1` 不会报错，运行时生成 0 个词的标题，行为诡异还不好排查。

## 如果你要在自己的项目里实现

```python
# 最小实现（3 个函数 + 3 个变量）

_config = None
_config_path = None
_config_mtime = None

def get_config():
    global _config, _config_path, _config_mtime

    path = Path("config.yaml")
    mtime = path.stat().st_mtime if path.exists() else None

    if _config is None or _config_mtime != mtime:
        with open(path) as f:
            data = yaml.safe_load(f)
        _config = MyConfig(**data)   # Pydantic 验证
        _config_path = path
        _config_mtime = mtime

    return _config
```

这就是 DeerFlow 热重载的全部。没有黑魔法，就是一个 mtime 检查。
