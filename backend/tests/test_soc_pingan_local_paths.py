from __future__ import annotations

import importlib.util
from pathlib import Path


def test_pingan_local_paths_resolve_from_script_location_not_user_home() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "backend" / "scripts" / "soc_pingan_local_paths.py"
    spec = importlib.util.spec_from_file_location("soc_pingan_local_paths", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    resolved = module.resolve_repo_root(script)
    values = module.resolved_paths(resolved)

    assert resolved == repo_root
    assert values["SOC_REPO_ROOT"] == str(repo_root)
    assert values["DEER_FLOW_CONFIG_PATH"] == str(repo_root / "config.pingan-dev.local")
    assert "/Users/zhangjianming627" not in "\n".join(values.values())
