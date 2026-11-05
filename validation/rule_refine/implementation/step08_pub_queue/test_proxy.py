"""Offline transformations of private source; never import the model adapter."""

import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from install import reviewed_transform

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "pub_proxy_transforms", HERE / "proxy_transforms.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ZIP = (
    None
    if os.environ.get("SEC_KNOW_SOURCE_ROOT")
    else HERE.parents[2] / "sec_know_model.zip"
)


def patched(path, source):
    if path in {
        "llm_service/openai_completion.py",
        "llm_service/litellm_proxy_config.prd.yaml",
    }:
        return reviewed_transform(path, source.encode("utf-8"))[0].decode("utf-8")
    return module.transforms(path, source)


def original(name):
    if os.environ.get("SEC_KNOW_SOURCE_ROOT"):
        return (Path(os.environ["SEC_KNOW_SOURCE_ROOT"]) / name).read_text(
            encoding="utf-8"
        )
    with zipfile.ZipFile(ZIP) as archive:
        return archive.read(name).decode("utf-8")


def model_dict(source, name):
    for node in ast.parse(source).body:
        target = getattr(node, "target", None)
        targets = getattr(node, "targets", [target])
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return {
                ast.literal_eval(key): ast.dump(value, include_attributes=False)
                for key, value in zip(node.value.keys, node.value.values)
            }
    raise AssertionError("dictionary not found")


class ProxyTests(unittest.TestCase):
    def test_python_adapter_removes_only_retired_prd_entries(self):
        name = "llm_service/openai_completion.py"
        before = original(name)
        after = patched(name, before)
        old = model_dict(before, "PRD_MODEL_CONFIGS")
        expected = {
            key: value for key, value in old.items() if key not in module.RETIRED
        }
        self.assertTrue(
            model_dict(after, "PRD_MODEL_CONFIGS") == expected,
            "other PRD registrations must stay unchanged",
        )
        self.assertTrue(
            model_dict(after, "STG_MODEL_CONFIGS")
            == model_dict(before, "STG_MODEL_CONFIGS"),
            "STG registrations must stay unchanged",
        )
        self.assertEqual(after.count("async def "), before.count("async def "))

    def test_yaml_preserves_remaining_models_and_settings_exactly(self):
        for name in module.YAML_PATHS:
            before = original(name)
            after = patched(name, before)
            before_blocks = module.yaml_model_blocks(before)
            after_blocks = module.yaml_model_blocks(after)
            self.assertEqual(
                set(after_blocks), set(before_blocks) - set(module.RETIRED)
            )
            for key in after_blocks:
                self.assertTrue(
                    after_blocks[key][2] == before_blocks[key][2],
                    "remaining model block changed",
                )
            self.assertTrue(
                before[before.index("litellm_settings:") :]
                == after[after.index("litellm_settings:") :],
                "proxy settings changed",
            )

    def test_preserve_release_disable_and_other_startup_commands(self):
        name = "run.sh"
        raw = original(name)
        guard = "\n# release guard\nexport RULE_REFINE_MONITOR_ENABLED=0\n"
        before = raw.replace("#!/bin/bash\n", "#!/bin/bash\n" + guard, 1)
        after = module.transforms(name, before)
        self.assertIn(guard, after)
        self.assertIn("python validation/verify_stg_distribution.py", after)
        self.assertIn("cleanup_priority9_tasks.py -e stg --delete", after)
        self.assertIn("cleanup_priority9_tasks.py -e prd --delete", after)
        self.assertIn("supervisord -n -c /etc/supervisor/supervisord.conf", after)
        self.assertIn("python run.py", after)
        marker = 'else\n  export CELERY_LOG_PATH="/mnt/nas_prd/glm/nas/log"\n'
        self.assertTrue(
            before.split(marker)[0] == after.split(marker)[0],
            "STG branch and pre-launch release guard must be byte-for-byte unchanged",
        )
        copy = (
            "\n  # PRD 使用独立 Pub worker 配置；STG 继续使用原配置。\n"
            "  cp config/celery_task.prd.pub.conf /etc/supervisor/conf.d/celery_task.conf || exit 1\n"
        )
        self.assertEqual(after.count(copy), 1)
        self.assertTrue(
            after.replace(copy, "", 1) == before,
            "Only PRD Supervisor selection may be inserted",
        )
        self.assertLess(after.index(copy), after.index("cleanup_priority9_tasks.py -e prd"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.sh"
            path.write_text(after, encoding="utf-8")
            subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)

    def test_launcher_rejects_unknown_branch_or_repeat_instead_of_touching_stg(self):
        before = original("run.sh")
        for source in (
            before.replace('if [ "$env_profile" = "stg" ]; then', "if false; then"),
            before.replace('else\n  export CELERY_LOG_PATH=', 'else\n  export CUSTOM_LOG_PATH='),
            module.transforms("run.sh", before),
        ):
            with self.subTest():
                with self.assertRaises(ValueError):
                    module.transforms("run.sh", source)

    def test_unknown_path_rejected(self):
        with self.assertRaises(ValueError):
            module.transforms("config/private.py", "secret")


if __name__ == "__main__":
    unittest.main(verbosity=2)
