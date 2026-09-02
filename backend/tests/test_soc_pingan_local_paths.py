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
    values = module.resolved_paths(resolved, environment="dev")

    assert resolved == repo_root
    assert values["SOC_REPO_ROOT"] == str(repo_root)
    assert values["DEER_FLOW_CONFIG_PATH"] == str(repo_root / "config.pingan-dev.local")
    assert values["SOC_DATABASE_URL"] == ("sqlite+pysqlite:///" + str(repo_root / "backend/.deer-flow/data/soc_agent_dev.db"))
    assert values["SOC_SQLITE_PATH"] == str(repo_root / "backend/.deer-flow/data/soc_agent_dev.db")
    assert values["SOC_DEV_SQLITE_PATH"] == values["SOC_SQLITE_PATH"]
    assert values["SOC_RUNTIME_ENVIRONMENT"] == "dev"
    assert "/Users/zhangjianming627" not in "\n".join(values.values())


def test_pingan_local_paths_isolates_stg_soc_database() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "backend" / "scripts" / "soc_pingan_local_paths.py"
    spec = importlib.util.spec_from_file_location("soc_pingan_local_paths_stg", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    values = module.resolved_paths(repo_root, environment="stg")

    assert values["SOC_RUNTIME_ENVIRONMENT"] == "stg"
    assert values["SOC_SQLITE_PATH"].endswith("/soc_agent_stg.db")
    assert values["SOC_DATABASE_URL"].endswith("/soc_agent_stg.db")
    assert "SOC_DEV_SQLITE_PATH" not in values
