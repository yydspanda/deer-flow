# Python 基础知识：DeerFlow 源码中遇到的高级特性

> 学习 DeerFlow 源码过程中遇到的不熟悉的 Python 概念，按需查阅。

---

## 目录

1. [ContextVar — 请求级变量](#1-contextvar--请求级变量)
2. [Token — 回滚凭证](#2-token--回滚凭证)
3. [Final — 只赋值一次](#3-final--只赋值一次)
4. [Protocol — 结构化类型（鸭子类型 + 类型注解）](#4-protocol--结构化类型鸭子类型--类型注解)
5. [runtime_checkable — 运行时类型检查](#5-runtime_checkable--运行时类型检查)
6. [哨兵值（Sentinel Value）](#6-哨兵值sentinel-value)
7. [单例模式（Singleton）](#7-单例模式singleton)

---

## 1. ContextVar — 按协程隔离的"全局变量"

### 问题：全局变量在并发时会串

```python
current_user = "Alice"  # 全局变量

def handle(name):
    global current_user
    current_user = name
    print(f"{name} 看到 current_user = {current_user}")

# 两个线程同时跑
import threading
threading.Thread(target=handle, args=("Alice",)).start()
threading.Thread(target=handle, args=("Bob",)).start()

# 可能输出：
# Alice 看到 current_user = Bob    ← Alice 被覆盖了！
```

### threading.local 能解决线程并发，但解决不了协程并发

`threading.local()` 按**线程**分配独立存储，多线程里互不干扰：

```python
import threading

local = threading.local()

def handle(name):
    local.user = name         # 写到自己线程的 local 里
    print(f"[{threading.current_thread().name}] {name} 看到 {local.user}")

t1 = threading.Thread(target=handle, args=("Alice",))
t2 = threading.Thread(target=handle, args=("Bob",))
t1.start()  # Thread-1 开始跑 handle("Alice")
t2.start()  # Thread-2 开始跑 handle("Bob")

# 输出：
# [Thread-1] Alice 看到 Alice    ← Thread-1 的 local.user = Alice
# [Thread-2] Bob 看到 Bob        ← Thread-2 的 local.user = Bob
# 互不干扰 ✅
```

但在 asyncio 里就坏了 — 多个协程跑在**同一个线程**里，共享同一份 local：

```python
import asyncio
import threading

local = threading.local()

async def handle(name):
    # 两个协程都跑在 MainThread 里
    local.user = name          # 写到 MainThread 的 local

    await asyncio.sleep(0)     # 让出 CPU，事件循环切到另一个协程
    # ← 另一个协程也往同一个 MainThread 的 local 里写了值

    print(f"[{threading.current_thread().name}] {name} 读到 {local.user}")
    # ← 读到的是"最后一个写入的"，不一定是自己写的

async def main():
    await asyncio.gather(handle("Alice"), handle("Bob"))

asyncio.run(main())
```

执行过程（都在 MainThread 里，同一份 local）：

```
[Alice 协程] local.user = "Alice"     ← 写入
[Alice 协程] await sleep(0)           ← 让出 CPU
[Bob 协程]   local.user = "Bob"       ← 覆盖了！同一个 local
[Bob 协程]   await sleep(0)           ← 让出 CPU
[Alice 协程] print(local.user)         → "Bob"！不是 "Alice" 了 ❌
[Bob 协程]   print(local.user)         → "Bob"
```

**根本原因**：`threading.local` 按线程隔离，但 asyncio 多个协程跑在同一个线程里 → 共享同一份 local → 互相覆盖。

### ContextVar：按协程隔离（同一线程也不串）

```python
import asyncio
import threading
from contextvars import ContextVar

user_var: ContextVar[str] = ContextVar("user_var", default="未知")

async def handle(name):
    user_var.set(name)         # 只改当前协程的小本子

    await asyncio.sleep(0)     # 让出 CPU

    print(f"[{threading.current_thread().name}] {name} 读到 {user_var.get()}")
    # ← 读到的是自己协程的值

async def main():
    await asyncio.gather(handle("Alice"), handle("Bob"))

asyncio.run(main())

# 输出：
# [MainThread] Alice 读到 Alice    ✅
# [MainThread] Bob 看到 Bob        ✅
# 都在 MainThread 里，但 ContextVar 按协程隔离，互不干扰
```

**对比**：
```
threading.local：按【线程】隔离
  不同线程 → 各自的 local        ✅ 线程间安全
  同一线程的多个协程 → 共享一份 local   ❌ 协程间串

ContextVar：按【协程】隔离
  不同线程 → 各自的 context
  同一线程的多个协程 → 各自的小本子     ✅ 协程间也安全
```

### 心智模型：每人手里的小本子

- 每个协程有自己的小本子
- `set()` 在自己本子上写
- `get()` 从自己本子上读
- 别人看不到你的本子

### 完整语法（就 4 个 API）

```python
from contextvars import ContextVar

# ① 定义：名字 + 默认值
my_var: ContextVar[str] = ContextVar("my_var", default="没人")

# ② 写
my_var.set("hello")

# ③ 读
val = my_var.get()     # → "hello"，没有则返回 default

# ④ 恢复（token 是 set() 的返回值）
token = my_var.set("new_value")
my_var.reset(token)    # 恢复到 set 之前的值
```

### 跨线程传播：copy_context()

ContextVar 在**同一线程的协程间**自动隔离，但**跨线程不传播**。

先看 ❌ 版本，每一行都加注释说明"谁在哪个线程"：

```python
import threading
from contextvars import ContextVar

my_var: ContextVar[str] = ContextVar("my_var", default="没人")

# ---- 以下代码在 MainThread（主线程）里跑 ----
my_var.set("Alice")
# 主线程的小本子 = {"my_var": "Alice"}

def wrong():
    # ← 这个函数会在 Thread-1（新线程）里跑
    print(my_var.get())  # → "没人"
    # 新线程有自己的小本子（全新的，空的）
    # 新线程的小本子 = {"my_var": "没人"}（default）
    # 所以读到 "没人"

# threading.Thread(target=wrong) — 创建线程对象，"新线程里跑 wrong 函数"
# .start() — 启动线程，wrong() 开始在新线程里执行
# 此时有两个线程同时在跑：
#   MainThread → 继续 main() 后面的代码
#   Thread-1   → 在跑 wrong()
threading.Thread(target=wrong).start()
# threading.Thread 默认不会把主线程的小本子复制给新线程
# 新线程拿到的是空白的小本子
```

再看 ✅ 版本：

```python
from contextvars import copy_context

# ---- 以下代码在 MainThread（主线程）里跑 ----
my_var.set("Alice")
# 主线程的小本子 = {"my_var": "Alice"}

ctx = copy_context()
# ctx 是什么？→ 主线程小本子的【复印件】
# ctx = {"my_var": "Alice"}  ← 拍了一张快照

def right():
    # ← 这个函数会在 Thread-1（新线程）里跑
    print(my_var.get())  # → "Alice"
    # 为什么能读到？因为下面用的不是 threading.Thread(target=right)
    # 而是 threading.Thread(target=ctx.run, args=[right])
    # ctx.run(right) 的意思是：用 ctx 这份复印件当小本子来跑 right

# 注意 target 的区别：
# ❌ threading.Thread(target=wrong)           → 新线程用空白小本子跑 wrong
# ✅ threading.Thread(target=ctx.run, args=[right]) → 新线程用复印件小本子跑 right
threading.Thread(target=ctx.run, args=[right]).start()
```

加上 `threading.current_thread().name` 可以亲眼验证：

```python
import threading
from contextvars import ContextVar, copy_context

my_var: ContextVar[str] = ContextVar("my_var", default="没人")

# ---- ❌ 版本 ----
my_var.set("Alice")

def wrong():
    print(f"[{threading.current_thread().name}] get → {my_var.get()}")

threading.Thread(target=wrong).start()
# 输出：[Thread-1] get → 没人

# ---- ✅ 版本 ----
ctx = copy_context()

def right():
    print(f"[{threading.current_thread().name}] get → {my_var.get()}")

threading.Thread(target=ctx.run, args=[right]).start()
# 输出：[Thread-1] get → Alice
```

### 对比表

| | 全局变量 | threading.local | ContextVar |
|---|---|---|---|
| 线程间隔离 | ❌ 共享 | ✅ 按线程 | ✅ 按 context |
| 协程间隔离 | ❌ 共享 | ❌ 同线程共享 | ✅ 按协程 |
| 跨线程传播 | ✅ 天然共享 | ❌ 线程独立 | ✅ copy_context() |
| Python 版本 | 任意 | 任意 | 3.7+ |

### 典型使用场景

```python
# 场景 1：Web 框架请求级状态（FastAPI / aiohttp）
current_user: ContextVar[str | None] = ContextVar("current_user", default=None)

async def auth_middleware(request):
    current_user.set(extract_user(request))  # 请求进来时设
    await handle(request)                     # 后续任意层都能 get
    # 请求结束，context 自动销毁，不需要手动清理

# 场景 2：请求级配置覆盖（测试场景）
config_var: ContextVar[dict] = ContextVar("config", default={})

def push_config(test_config):
    config_var.set(test_config)   # 测试期间覆盖

def pop_config():
    config_var.set({})            # 恢复默认

# 场景 3：子线程继承父线程的 ContextVar
def run_sub_task():
    ctx = copy_context()          # 复印
    thread = threading.Thread(target=ctx.run, args=[task_fn])
    thread.start()                # 子线程能读到 trace_id、user_id 等
```

### 在 DeerFlow 里的使用

- `tools/builtins/tool_search.py` — `_registry_var` 隔离每个请求的工具注册表
- `config/app_config.py` — `_current_app_config` 请求级配置覆盖 + push/pop 栈
- `agents/memory/queue.py` — 入队时捕获 user_id（因为定时器回调在另一个线程，ContextVar 丢失）

---

## 2. Token — ContextVar 的"撤销凭证"

### 先搞清楚：set() 会返回一个东西

```python
from contextvars import ContextVar
my_var = ContextVar("my_var", default="没人")

# set() 不只是"设值"，它还会返回一个 Token 对象
token = my_var.set("Alice")   # ← token 是什么？
print(type(token))             # → <class 'Token'>
print(my_var.get())            # → "Alice"
```

**Token 就是"设值前的快照"** — 它记住了 `set("Alice")` 之前，`my_var` 是什么值。

### Token 能干嘛？用 reset() 撤销 set()

```python
my_var = ContextVar("my_var", default="没人")

token = my_var.set("Alice")
print(my_var.get())            # → "Alice"

my_var.reset(token)            # 撤销！恢复到 set 之前
print(my_var.get())            # → "没人"（default）
```

**`reset(token)` = 撤销这一次 set()，恢复到 set 之前的值。**

### 为什么不直接 set(default) 恢复？

因为"之前是什么"不一定是 default。看这个场景：

```python
my_var = ContextVar("my_var", default="没人")

# 第一层：设了 admin
token1 = my_var.set("admin")   # "没人" → "admin"
print(my_var.get())             # → "admin"

# 第二层：临时切到 alice
token2 = my_var.set("alice")   # "admin" → "alice"
print(my_var.get())             # → "alice"

# 第二层结束：要恢复成什么？
# ❌ set("没人") → 错了！应该是 admin
# ✅ reset(token2) → 恢复成 "admin"（因为 token2 记住了 set 之前是 "admin"）
my_var.reset(token2)
print(my_var.get())             # → "admin"    ✅

# 第一层结束
my_var.reset(token1)
print(my_var.get())             # → "没人"     ✅
```

**关键区别**：
- `set("没人")` → 你得自己记住"之前是什么"，嵌套多层就乱了
- `reset(token)` → token 帮你记住了，不管嵌套多深都能精确恢复

### 实际用法：try/finally 模式

```python
async def handle_request(request):
    token = user_var.set(extract_user(request))
    try:
        await process(request)        # 任意深度的调用都能 get() 到用户
    finally:
        user_var.reset(token)         # 请求结束，恢复干净
```

### 对比

```python
# 方式 1：用 reset(token) — 精确，支持嵌套
token = my_var.set("Alice")
try:
    do_something()
finally:
    my_var.reset(token)       # 恢复到 set 之前的值（不一定是 default）

# 方式 2：用 set(default) — 简单，但不支持嵌套
my_var.set("Alice")
try:
    do_something()
finally:
    my_var.set("没人")        # 总是恢复成 default，嵌套时出错
```

### 在 DeerFlow 里的使用

`config/app_config.py` 用 Token 实现 push/pop 栈：
- `push_current_app_config(config)` — `set()` 新配置，保存旧值到栈
- `pop_current_app_config()` — 从栈里取旧值，`set()` 回去

等价于多层 Token 嵌套，但用栈管理更清晰。

---

## 3. Final — 只赋值一次

### 是什么

类型注解工具，告诉类型检查器"这个变量赋值后不应该再改"。

### 注意：运行时不强制

```python
from typing import Final

DEFAULT_USER_ID: Final[str] = "default"
DEFAULT_USER_ID = "alice"  # 类型检查器会警告，但运行时不报错
```

类比：Java 的 `final`、JS 的 `const`。

### Final 锁的是变量，不是对象

```python
_current_user: Final = ContextVar(...)

_current_user = "other"       # ❌ Final 不允许重新赋值变量
_current_user.set("alice")    # ✅ 调用对象的方法，不是重新赋值
_current_user.get()           # ✅ 同上
_current_user.reset(token)    # ✅ 同上
```

`Final` 锁的是"_current_user 这个变量名永远指向同一个 ContextVar 对象"。ContextVar 对象内部的值可以随便 set/reset。

类比 JS：

```javascript
const arr = [1, 2, 3]
arr = [4, 5, 6]    // ❌ const 不允许重新赋值
arr.push(4)         // ✅ 修改对象内容
```

---

## 4. Protocol — 结构化类型（鸭子类型 + 类型注解）

### 是什么

只声明"我需要哪些属性/方法"，不要求继承。

### 传统做法 vs Protocol

```python
# 传统做法：必须继承 Animal
class Animal:
    def speak(self) -> str: ...

class Dog(Animal):
    def speak(self) -> str:
        return "汪"

class Robot:  # 有 speak() 但不想继承 Animal
    def speak(self) -> str:
        return "嘀嘀"

def make_sound(animal: Animal):
    print(animal.speak())

make_sound(Dog())    # ✅
make_sound(Robot())  # ❌ 类型检查器报错（没有继承 Animal）
```

```python
# Protocol 做法：只要有 speak() 方法就行
from typing import Protocol

class CanSpeak(Protocol):
    def speak(self) -> str: ...

def make_sound(thing: CanSpeak):
    print(thing.speak())

make_sound(Dog())    # ✅ 有 speak()
make_sound(Robot())  # ✅ 也有 speak()！
make_sound(42)       # ❌ int 没有 speak()
```

### "鸭子类型的有类型版本"

> "如果它走起来像鸭子、叫起来像鸭子，那它就是鸭子"——不需要验 DNA（继承），只要行为对就行。

### 在 DeerFlow 里的用法

```python
# 框架层（deerflow.runtime）
class CurrentUser(Protocol):
    id: str    # 只要求有 .id 属性

# 应用层（app.gateway.auth）
class User(BaseModel):
    id: UUID   # User 有 .id 属性 → 自动满足 Protocol

# User 不需要继承 CurrentUser，只要"长得像"就行
set_current_user(user)  # User 对象传入，满足 CurrentUser Protocol ✅
```

**为什么这样做？** 框架层不能 import 应用层（违反分层原则）。Protocol 让框架层只声明需求，不依赖具体实现。

---

## 5. runtime_checkable — 运行时类型检查

### 是什么

默认 Protocol 不支持 `isinstance()`。加 `@runtime_checkable` 后可以：

```python
from typing import Protocol, runtime_checkable

# 不加
class CanSpeak(Protocol):
    def speak(self) -> str: ...

isinstance(dog, CanSpeak)  # ❌ 运行时报错

# 加了
@runtime_checkable
class CanSpeak(Protocol):
    def speak(self) -> str: ...

isinstance(dog, CanSpeak)  # ✅ 运行时检查 dog 有没有 speak() 方法
```

### 在 DeerFlow 里的用法

```python
@runtime_checkable
class CurrentUser(Protocol):
    id: str

# 测试里验证"只要有 .id 就行"
user = SimpleNamespace(id="alice")
assert isinstance(user, CurrentUser)  # ✅ 有 .id 属性

obj = object()
assert not isinstance(obj, CurrentUser)  # ❌ 没有 .id 属性
```

---

## 6. 哨兵值（Sentinel Value）

### 是什么

一个**独一无二**的标记值，用来区分"没传参数"和"传了某个正常值"。

### 问题场景

```python
def greet(name=None):
    if name is None:
        print("你好，陌生人")
    else:
        print(f"你好，{name}")

greet()       # 你好，陌生人
greet("Alice")  # 你好，Alice
greet(None)     # 你好，陌生人  ← 有人真的想叫 "None"，但分不清了
```

`None` 同时表示"没传"和"传了 None"，有歧义。

### 解决方案

用一个独一无二的对象当默认值：

```python
_NO_NAME = object()  # 独一无二，不会和任何值相等

def greet(name=_NO_NAME):
    if name is _NO_NAME:
        print("你好，陌生人")
    else:
        print(f"你好，{name}")

greet()       # 你好，陌生人  （name 是哨兵）
greet("Alice")  # 你好，Alice
greet(None)     # 你好，None   （name 不是哨兵，正常处理）
```

### 为什么 object() 有效

```python
a = object()
b = object()
a is b   # False —— 每次 object() 创建的都是不同的对象
a is a   # True  —— 只有和自己比较才相等
```

所以哨兵永远不会等于任何字符串、None、数字——只等于它自己。

### 为什么用自定义类而不是 object()

```python
AUTO = object()
print(AUTO)  # <object at 0x7f3b2c1d0>  ← 调试时看不懂

# 自定义类
class _AutoSentinel:
    def __repr__(self):
        return "<AUTO>"

AUTO = _AutoSentinel()
print(AUTO)  # <AUTO>  ← 一眼看出
```

好处：
1. 打印友好（`<AUTO>`）
2. 支持 isinstance 判断
3. 支持类型注解（`user_id: str | None | _AutoSentinel`）

### Python 标准库里的哨兵

- `Ellipsis`（`...`）— NumPy 切片里表示"全部"
- `NotImplemented` — 运算符重载里表示"我不处理这个类型"

### 在 DeerFlow 里的用法

persistence 层区分三种 user_id：

```python
def get_threads(user_id=AUTO):
    if isinstance(user_id, _AutoSentinel):
        # AUTO → "没传参数"，从 ContextVar 自动解析
    elif user_id is None:
        # None → "故意跳过隔离"
    else:
        # 字符串 → "用这个具体值"
```

---

## 7. 单例模式（Singleton）

### 是什么

一个类全局只允许存在一个实例。不管调用多少次 `MyClass()`，返回的都是同一个对象。

### 为什么哨兵必须是单例

哨兵的判断靠 `is`（身份比较）：

```python
AUTO = _AutoSentinel()   # 全局唯一的哨兵对象

if user_id is AUTO:       # 用 is 判断"是不是那个哨兵"
    ...
```

如果不是单例，每次 `_AutoSentinel()` 创建新对象，`is` 比较就会失效：

```python
a = _AutoSentinel()  # 对象 A
b = _AutoSentinel()  # 对象 B（如果不是单例，A 和 B 不同）
a is b   # False → 哨兵判断失效
```

### 实现方式

```python
class _AutoSentinel:
    _instance = None

    def __new__(cls):          # __new__ 比 __init__ 更早调用，负责"创建对象"
        if cls._instance is None:
            cls._instance = super().__new__(cls)  # 第一次：创建，存起来
        return cls._instance   # 之后：直接返回之前的

a = _AutoSentinel()
b = _AutoSentinel()
a is b   # True —— 永远是同一个对象
```

### 单例的其他实现方式（DeerFlow 没用，但你应该知道）

```python
# 方式 1：模块级变量（最简单）
AUTO = _AutoSentinel()  # 模块只加载一次，所以 AUTO 只创建一次

# 方式 2：装饰器（不推荐，复杂）
def singleton(cls):
    _instance = {}
    def wrapper(*args, **kwargs):
        if cls not in _instance:
            _instance[cls] = cls(*args, **kwargs)
        return _instance[cls]
    return wrapper
```

DeerFlow 的 `_AutoSentinel` 同时用了方式 1（模块级变量 `AUTO = _AutoSentinel()`）和 `__new__` 单例——双保险。
