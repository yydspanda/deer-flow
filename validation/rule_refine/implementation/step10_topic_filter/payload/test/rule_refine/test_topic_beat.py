"""Only the new beat registration block runs; no Celery/broker/application imports."""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "config/celery_config.py").read_text(encoding="utf-8")
MARKER = "# Rule refinement: reuse the existing LOCAL periodic worker and beat."
REGISTRATION = SOURCE[SOURCE.index(MARKER) :]
MODULE = "my_workflows.zeus.rule_refine.topic_settings"


class BeatRegistrationTests(unittest.TestCase):
    def registration(self, profile, enabled):
        calls = []
        fake = types.ModuleType(MODULE)

        def topic_enabled(root, environ):
            calls.append((root, dict(environ)))
            return enabled

        fake.topic_enabled = topic_enabled
        scope = {
            "rc": types.SimpleNamespace(ENV=profile),
            "beat_schedule": {"existing_job": {"task": "synthetic-existing"}},
            "__file__": str(ROOT / "config/celery_config.py"),
        }
        with (
            patch.dict(sys.modules, {MODULE: fake}),
            patch.dict(
                os.environ, {"env_profile": profile, "ENV_PROFILE": profile}, clear=True
            ),
        ):
            exec(compile(REGISTRATION, "<topic-beat-registration>", "exec"), scope)
        self.assertIn("existing_job", scope["beat_schedule"])
        return scope["beat_schedule"], calls

    def test_local_keeps_original_tick_without_duplicate_topic_task(self):
        schedule, calls = self.registration("LOCAL", False)
        self.assertEqual(calls, [])
        self.assertEqual(set(schedule), {"existing_job", "rule_refine_pending"})
        self.assertEqual(
            schedule["rule_refine_pending"]["task"], "tasks.task.rule_refine_tick"
        )

    def test_prd_disabled_does_not_register_monitor_tick(self):
        schedule, calls = self.registration("PRD", False)
        self.assertEqual(set(schedule), {"existing_job"})
        self.assertEqual(calls[0][0], ROOT)
        self.assertEqual(len(calls), 1)

    def test_prd_enabled_reuses_existing_periodic_queue_and_task(self):
        schedule, calls = self.registration("PRD", True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            schedule["rule_refine_pending"],
            {
                "task": "tasks.task.rule_refine_tick",
                "schedule": 30.0,
                "options": {"queue": "periodic_queue", "expires": 25},
            },
        )

    def test_stg_is_unchanged_even_when_topic_setting_is_enabled(self):
        schedule, calls = self.registration("STG", True)
        self.assertEqual(set(schedule), {"existing_job"})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
