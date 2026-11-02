bash <<'SOC_FIX_COUNTER'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYFIX'
import hashlib
import os
import stat
import tempfile
from pathlib import Path

patches = [
    ('my_workflows/zeus/rule_refine/counter.py',
     '12da0167d8122a483bbf6ea7330862a3e074ff477f825cfef61b03098850184b',
     '1fbf0e5908b42a2c7753c2b0d4d51e61624b39213deb06c86d9a9280534cde06', [
        (0, 1, r'''"""Redis 规则计数与待处理信号；沿用原项目 WATCH 事务，不依赖 Lua。"""
'''),
        (5, 5, r'''import random
import time
'''),
        (8, 8, r'''from redis.exceptions import ResponseError, WatchError
'''),
        (9, 71, r''''''),
        (72, 79, r'''class CounterBusyError(RuntimeError):
    """有限次数的事务竞争后仍未提交；调用方需记录失败并稍后重试。"""
'''),
        (85, 85, r'''

def _decoded(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value
'''),
        (99, 99, r'''        transaction_retries=32,
'''),
        (113, 113, r'''        if type(transaction_retries) is not int or not 1 <= transaction_retries <= 100:
            raise ValueError("事务重试次数必须为 1～100 的整数")
'''),
        (117, 117, r'''        self.transaction_retries = transaction_retries
'''),
        (136, 136, r'''    @staticmethod
    def _backoff(attempt, error):
        # redis-py may wrap a lost connection while watching as WatchError.
        # Preserve that failure instead of treating it as ordinary contention.
        if str(error) != "Watched variable changed." or error.__cause__ is not None:
            raise error
        time.sleep(random.uniform(0.001, min(0.05, 0.005 * (attempt + 1))))

'''),
        (147, 148, r'''        ttl = retention + 2 * w + cooldown
        w, cooldown, retention = w * 1000, cooldown * 1000, retention * 1000
        template = {
'''),
        (154, 155, r'''            "window_seconds": self.settings[0],
'''),
        (157, 159, r'''            "cooldown_seconds": self.settings[3],
            "dedup_seconds": self.settings[4],
'''),
        (160, 178, r'''        for attempt in range(self.transaction_retries):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(*keys)
                    for key, kind in zip(keys, ("zset", "hash", "string")):
                        if _decoded(pipe.type(key)) not in ("none", kind):
                            raise ResponseError("rule counter key type mismatch")
                    observed_at = milliseconds
                    if self.mode == "live":
                        seconds, micros = pipe.time()
                        observed_at = seconds * 1000 + micros // 1000
                    state = {_decoded(k): int(v) for k, v in pipe.hgetall(keys[1]).items()}
                    raw = pipe.get(keys[2])
                    pending = self._signal(json.loads(raw)) if raw else None
                    now = max(observed_at, state.get("watermark", observed_at))
                    cutoff = now - retention
                    coverage = max(state.get("coverage", now), cutoff)
                    score = pipe.zscore(keys[0], alert_id)
                    added = observed_at > cutoff and (score is None or score <= cutoff)
                    current = pipe.zcount(keys[0], f"({now - w}", now)
                    previous = pipe.zcount(keys[0], f"({now - 2 * w}", now - w)
                    current += int(added and observed_at > now - w)
                    previous += int(added and now - 2 * w < observed_at <= now - w)
                    ready = now - coverage >= 2 * w
                    growth = current / previous if ready and previous else None
                    eligible = current >= minimum and (growth is None or growth >= ratio)
                    triggered = bool(
                        added
                        and observed_at > now - w
                        and eligible
                        and pending is None
                        and ("last_trigger" not in state or now - state["last_trigger"] >= cooldown)
                    )
                    updates = {"watermark": now, "coverage": coverage}
                    encoded = None
                    if triggered:
                        samples = [
                            (_decoded(identity), score)
                            for identity, score in pipe.zrevrangebyscore(
                                keys[0], now, f"({now - w}", start=0, num=20, withscores=True
                            )
                        ]
                        samples.append((alert_id, observed_at))
                        samples.sort(key=lambda pair: (pair[1], pair[0].encode("utf-8")), reverse=True)
                        pending = self._signal(
                            {
                                **template,
                                "reason": "growth" if growth is not None else "high_volume",
                                "current_count": current,
                                "previous_count": previous,
                                "baseline_ready": ready,
                                "growth_ratio": growth,
                                "window_start_ms": now - w,
                                "window_end_ms": now,
                                "sample_alert_ids": [identity for identity, _ in samples[:20]],
                            }
                        )
                        encoded = json.dumps(pending, ensure_ascii=False, allow_nan=False)
                        updates["last_trigger"] = now
                    pipe.multi()
                    pipe.zremrangebyscore(keys[0], "-inf", cutoff)
                    if added:
                        pipe.zadd(keys[0], {alert_id: observed_at}, nx=True)
                    pipe.hset(keys[1], mapping=updates)
                    if triggered:
                        pipe.set(keys[2], encoded)
                    pipe.expire(keys[0], ttl)
                    pipe.expire(keys[1], ttl)
                    pipe.execute()
                    return {
                        "added": added,
                        "too_old": observed_at <= cutoff,
                        "current_count": current,
                        "previous_count": previous,
                        "baseline_ready": ready,
                        "growth_ratio": growth,
                        "triggered": triggered,
                        "pending_signal": pending,
                    }
            except WatchError as error:
                self._backoff(attempt, error)
        raise CounterBusyError("计数事务竞争超限，未确认提交；请稍后重试同一告警")
'''),
        (184, 185, r'''        key = self._keys(observation)[2]
        signal_id = _text(signal_id)
        for attempt in range(self.transaction_retries):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if not raw or json.loads(raw)["signal_id"] != signal_id:
                        pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.delete(key)
                    return bool(pipe.execute()[0])
            except WatchError as error:
                self._backoff(attempt, error)
        raise CounterBusyError("信号确认事务竞争超限；请稍后重试")
'''),
    ]),
    ('test/rule_refine/test_counter.py',
     '7f81df0658bfa8130f587975c7b671cccf5a58efa4fe56ea534754af1171834e',
     'a420200201d835c28be68113cd6faaa5dc10ef6e9835d370f15dbccf2cbd59ac', [
        (15, 16, r'''from redis.client import Pipeline
from redis.exceptions import ConnectionError, ResponseError, WatchError
'''),
        (21, 22, r'''counter_module = importlib.import_module(package.__name__ + ".counter")
Counter = counter_module.RedisRuleCounter
CounterBusyError = counter_module.CounterBusyError
'''),
        (200, 201, r'''        execute = Pipeline.execute
        lost = False
'''),
        (202, 205, r'''        def lose_response(pipe, *args, **kwargs):
            nonlocal lost
            result = execute(pipe, *args, **kwargs)
            if not lost:
                lost = True
                raise ConnectionError("injected response loss after Redis execution")
            return result
'''),
        (206, 207, r'''        with patch.object(Pipeline, "execute", new=lose_response):
'''),
        (210, 210, r'''        self.assertIsNotNone(pending)
'''),
        (234, 234, r'''            {"transaction_retries": 0},
            {"transaction_retries": True},
'''),
        (241, 242, r'''        failing.pipeline.side_effect = ConnectionError("injected connection failure")
'''),
        (249, 250, r'''        with self.assertRaisesRegex(ResponseError, "^rule counter key type mismatch$"):
'''),
        (255, 255, r'''
    def test_observe_and_acknowledge_work_without_lua(self):
        error = ResponseError("unknown command EVAL")
        with (
            patch.object(Redis, "eval", side_effect=error),
            patch.object(Redis, "evalsha", side_effect=error),
            patch.object(Redis, "register_script", side_effect=error),
        ):
            signal = self.trigger()
            self.assertEqual(signal["current_count"], 3)
            self.assertTrue(self.counter.acknowledge(observation(), signal["signal_id"]))

    def test_real_watch_conflict_keeps_racing_count_and_existing_signal(self):
        self.send("a", 0)
        self.send("b", 1)
        execute = Pipeline.execute
        racing = []
        injected = False

        def race_before_execute(pipe, *args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                racing.append(self.counter.observe(observation("racer", 2)))
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=race_before_execute):
            result = self.send("c", 2)
        self.assertTrue(racing[0]["triggered"])
        self.assertTrue(result["added"])
        self.assertFalse(result["triggered"])
        self.assertEqual(result["current_count"], 4)
        self.assertEqual(result["pending_signal"], racing[0]["pending_signal"])
        self.assertEqual(self.redis.zcard(self.counter._keys(observation())[0]), 4)

    def test_acknowledge_watch_conflict_preserves_replacement(self):
        signal = self.trigger()
        replacement = {**signal, "signal_id": uuid4().hex}
        pending_key = self.counter._keys(observation())[2]
        execute = Pipeline.execute
        injected = False

        def replace_before_execute(pipe, *args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                self.redis.set(pending_key, json.dumps(replacement))
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=replace_before_execute):
            acknowledged = self.counter.acknowledge(observation(), signal["signal_id"])
        self.assertFalse(acknowledged)
        self.assertEqual(self.counter.pending(observation()), replacement)

    def test_repeated_watch_conflicts_fail_explicitly_without_partial_count(self):
        counter = self.make_counter(transaction_retries=2)
        keys = counter._keys(observation())
        self.keys.update(keys)
        execute = Pipeline.execute
        attempts = 0

        def always_race(pipe, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            self.redis.hset(keys[1], "test_collision", attempts)
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=always_race):
            with self.assertRaises(CounterBusyError):
                counter.observe(observation())
        self.assertEqual(attempts, 2)
        self.assertFalse(self.redis.exists(keys[0]))
        self.assertIsNone(counter.pending(observation()))

    def test_connection_wrapped_as_watch_error_is_not_retried(self):
        keys = self.counter._keys(observation())
        self.keys.update(keys)
        failure = WatchError("A ConnectionError occurred while watching one or more keys")
        with patch.object(Pipeline, "execute", side_effect=failure) as execute:
            with self.assertRaises(WatchError) as raised:
                self.counter.observe(observation())
        self.assertIs(raised.exception, failure)
        self.assertIsNone(failure.__cause__)
        self.assertEqual(execute.call_count, 1)
        for key in keys:
            self.assertFalse(self.redis.exists(key))
'''),
    ]),
]
prepared = []
for name, old_hash, new_hash, changes in patches:
    target = Path(name)
    if target.is_symlink() or not target.is_file():
        raise SystemExit("停止：文件缺失或为链接：" + name)
    original = target.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest == new_hash:
        print("已修复：" + name)
        continue
    if digest != old_hash:
        raise SystemExit("停止：文件与上一版不一致，未覆盖：" + name)
    lines = original.decode("utf-8").splitlines(keepends=True)
    for start, end, replacement in reversed(changes):
        lines[start:end] = [replacement]
    updated = "".join(lines).encode("utf-8")
    if hashlib.sha256(updated).hexdigest() != new_hash:
        raise SystemExit("停止：修复结果校验失败：" + name)
    compile(updated, name, "exec")
    backup = target.with_name(target.name + ".before-no-eval")
    if backup.is_symlink() or (backup.exists() and backup.read_bytes() != original):
        raise SystemExit("停止：旧版副本存在不同内容：" + str(backup))
    prepared.append((target, backup, original, updated))
for target, backup, original, updated in prepared:
    if not backup.exists():
        with backup.open("xb") as stream:
            stream.write(original)
    descriptor, temporary = tempfile.mkstemp(prefix=".counter-fix-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("已修复：" + str(target))
print("修复就绪：使用 WATCH/MULTI/EXEC，不再调用 EVAL。")
PYFIX
.venv/bin/python test/rule_refine/test_counter.py
.venv/bin/python test/rule_refine/test_counter.py --demo
SOC_FIX_COUNTER
