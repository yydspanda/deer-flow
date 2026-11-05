"""Prepare the legacy release on the Mac; never import or launch the application."""

import json
import re
import subprocess
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

MODULE = "my_workflows/zeus/rule_refine/"
PRD_CONFIG = "config/rule_refine.topic.prd.json"
CATALOG = "config/rule_refine.rules.prd.snapshot"
LOCAL_CONFIGS = (
    "config/rule_refine.topic.json",
    "config/rule_refine.local.json",
)
START = "# BEGIN RULE_REFINE_RELEASE_DISABLED"
END = "# END RULE_REFINE_RELEASE_DISABLED"
NEW_BLOCK = """# BEGIN RULE_REFINE_TOPIC_PRODUCTION
# Enable topic suggestions only when the existing release runs as PRD.
case "${env_profile:-${ENV_PROFILE:-}}" in
  prd|PRD) export RULE_REFINE_MONITOR_ENABLED=1 ;;
  *) export RULE_REFINE_MONITOR_ENABLED=0 ;;
esac
# END RULE_REFINE_TOPIC_PRODUCTION"""


def read_object(root, name, installer):
    found = installer._read(root, name)
    if found is None:
        return {}, None
    try:
        if len(found[0]) > 16384:
            raise ValueError()
        value = json.loads(found[0], object_pairs_hook=installer._unique)
        if not isinstance(value, dict):
            raise ValueError()
    except Exception:
        raise installer.InstallError(
            "invalid_release_configuration", relative_file=name
        ) from None
    return value, found


def launcher(raw, installer):
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise installer.InstallError("launcher_encoding_invalid") from None
    if source.count(NEW_BLOCK) == 1:
        before, after = source.split(NEW_BLOCK)
        result = source
    else:
        pattern = re.compile(
            re.escape(START)
            + r"\n[^\n]*\nexport RULE_REFINE_MONITOR_ENABLED=[01]\n"
            + re.escape(END)
        )
        matches = list(pattern.finditer(source))
        if len(matches) != 1:
            raise installer.InstallError(
                "review_run_sh_monitor_block", relative_file="run.sh"
            )
        match = matches[0]
        before, after = source[: match.start()], source[match.end() :]
        result = before + NEW_BLOCK + after
    if any(
        "RULE_REFINE_MONITOR_ENABLED" in line and not line.lstrip().startswith("#")
        for line in (before + after).splitlines()
    ):
        raise installer.InstallError(
            "multiple_launcher_monitor_settings", relative_file="run.sh"
        )
    checked = subprocess.run(
        ["bash", "-n"],
        input=result.encode(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    if checked.returncode:
        raise installer.InstallError("launcher_syntax_invalid", relative_file="run.sh")
    return result.encode("utf-8")


@contextmanager
def modules(sources, root):
    """Only reviewed stdlib-only modules, isolated from the app's import tree."""
    package = "_topic_release_preparation"
    names = []
    parent = types.ModuleType(package)
    parent.__path__ = []
    sys.modules[package] = parent
    names.append(package)
    result = {}
    try:
        for name in (
            "surge_settings",
            "topic_settings",
            "local_settings",
            "rule_config_source",
            "csv_config_source",
        ):
            full = package + "." + name
            module = types.ModuleType(full)
            module.__package__ = package
            module.__file__ = str(root / MODULE / (name + ".py"))
            sys.modules[full] = module
            names.append(full)
            exec(
                compile(
                    sources[MODULE + name + ".py"], "<reviewed-release-module>", "exec"
                ),
                module.__dict__,
            )
            result[name] = module
        yield result
    finally:
        for name in reversed(names):
            sys.modules.pop(name, None)


def catalog_source(root, configs, env, installer):
    explicit = env.get("SEC_TOPIC_CSV_PATH")
    if explicit:
        values = [explicit]
    elif configs[PRD_CONFIG].get("csv_path"):
        # Repeated preparation uses the already packaged production snapshot.
        values = [configs[PRD_CONFIG]["csv_path"]]
    else:
        values = [
            configs[name].get("csv_path")
            for name in LOCAL_CONFIGS
            if configs[name].get("csv_path")
        ]
        if not values:
            values = [str(Path.home() / "sec-rule-refine-data/rules.csv")]
    candidates = []
    for value in values:
        if not isinstance(value, str):
            raise installer.InstallError("invalid_rule_catalog_path")
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        try:
            size = path.stat().st_size
            if size > installer.MAX_FILE_BYTES:
                raise installer.InstallError("rule_catalog_too_large")
            raw = path.read_bytes()
        except OSError:
            raise installer.InstallError(
                "configured_rule_catalog_not_readable"
            ) from None
        candidates.append((path, raw))
    if len({installer.digest(raw) for _, raw in candidates}) != 1:
        raise installer.InstallError(
            "configured_rule_catalogs_differ_use_SEC_TOPIC_CSV_PATH"
        )
    return candidates[0]


def prepare(root, backup_root, env, bundle, reconstruct, installer):
    root, backup_root = Path(root).absolute(), Path(backup_root).absolute()
    # Rule configuration snapshots can exceed the source-file limit; they never
    # contain task history, and are validated by the installed catalog parser.
    installer.MAX_FILE_BYTES = 64 * 1024 * 1024
    with installer._lock(root):
        packet = reconstruct(bundle, root, installer)
        staged = installer.validate_packet(packet)
        originals = {name: installer._read(root, name) for name in packet["files"]}
        for name, entry in packet["files"].items():
            found = originals[name]
            current = installer.digest(found[0]) if found is not None else None
            if current not in {entry["old_sha256"], entry["new_sha256"]}:
                raise installer.InstallError(
                    "release_source_changed", relative_file=name
                )
        checked = {}
        for name, digest in packet["dependencies"].items():
            found = installer._read(root, name)
            if found is None or installer.digest(found[0]) != digest:
                raise installer.InstallError(
                    "dependency_version_mismatch", relative_file=name
                )
            checked[name] = found
            staged[name] = found[0]
        configs = {}
        for name in (*LOCAL_CONFIGS, PRD_CONFIG):
            configs[name], found = read_object(root, name, installer)
            checked[name] = found
        old_run = installer._read(root, "run.sh")
        if old_run is None:
            raise installer.InstallError(
                "production_launcher_missing", relative_file="run.sh"
            )
        run_after = launcher(old_run[0], installer)
        csv_path, csv_raw = catalog_source(root, configs, env, installer)
        with tempfile.TemporaryDirectory(prefix="topic-release-verify-") as directory:
            stage = Path(directory)
            installer._save(stage / CATALOG, csv_raw)
            for name in LOCAL_CONFIGS:
                if checked[name] is not None:
                    installer._save(stage / name, checked[name][0])
            with modules(staged, stage) as loaded:
                try:
                    catalog = loaded["csv_config_source"].CsvRuleConfigSource(
                        stage / CATALOG
                    )
                    identities = [
                        (r["company_code"], r["topic"], r["rule_code"])
                        for r in catalog.list_rules()
                    ]
                    if len(identities) != len(set(identities)):
                        raise installer.InstallError("duplicate_rule_catalog_identity")
                except installer.InstallError:
                    raise
                except Exception:
                    raise installer.InstallError(
                        "not_a_valid_rule_configuration_catalog"
                    ) from None
                settings = loaded["topic_settings"]
                value = {**settings.DEFAULTS, **configs[PRD_CONFIG]}
                value.update(enabled=True, csv_path=CATALOG)
                if not value["namespace"]:
                    value["namespace"] = "topic-v1:prd"
                config_raw = installer._json(value) + b"\n"
                installer._save(stage / PRD_CONFIG, config_raw)
                try:
                    effective = settings.load_settings(stage, {"env_profile": "prd"})
                    configured_env = {
                        "env_profile": "prd",
                        "RULE_REFINE_MONITOR_ENABLED": "1",
                    }
                    monitor = loaded["local_settings"].configure_rule_monitor(
                        stage, configured_env
                    )
                    if (
                        effective is None
                        or not monitor["enabled"]
                        or configured_env["RULE_REFINE_CSV_PATH"]
                        != str(stage / CATALOG)
                        or configured_env["RULE_REFINE_NAMESPACE"] != value["namespace"]
                    ):
                        raise ValueError()
                    if (
                        settings.load_settings(stage, {"env_profile": "STG"})
                        is not None
                    ):
                        raise ValueError()
                except Exception:
                    raise installer.InstallError(
                        "production_settings_check_failed"
                    ) from None

        targets = {
            name: item["content"].encode("utf-8")
            for name, item in packet["files"].items()
        }
        targets.update({PRD_CONFIG: config_raw, CATALOG: csv_raw, "run.sh": run_after})
        for name in (PRD_CONFIG, CATALOG, "run.sh"):
            originals[name] = installer._read(root, name)
        if not installer._same(
            originals[PRD_CONFIG], checked[PRD_CONFIG]
        ) or not installer._same(originals["run.sh"], old_run):
            raise installer.InstallError("release_source_changed")
        tests_passed = installer.run_checks(staged, packet["tests"])
        for name, found in {**originals, **checked}.items():
            if not installer._same(installer._read(root, name), found):
                raise installer.InstallError(
                    "release_source_changed_during_tests", relative_file=name
                )
        if csv_path.read_bytes() != csv_raw:
            raise installer.InstallError("rule_catalog_changed_during_tests")
        changed = [
            name
            for name in targets
            if originals[name] is None or originals[name][0] != targets[name]
        ]
        result = {
            "check": "rule_refine_production_release_prepare",
            "status": "prepared" if changed else "already_prepared",
            "tests_passed": tests_passed,
            "files_changed": len(changed),
            "changed_files": changed,
            "target_profile": "PRD",
            "configuration_file": PRD_CONFIG,
            "rule_catalog_file": CATALOG,
            "rule_catalog_count": catalog.count,
            "rule_catalog_bytes": len(csv_raw),
            "rule_catalog_sha256": installer.digest(csv_raw),
            "rule_catalog_source": str(csv_path),
            "rule_catalog_online_freshness_verified": False,
            "namespace": value["namespace"],
            "production_monitor_enabled_on_normal_start": True,
            "local_configuration_unchanged": True,
            "stg_unchanged": True,
            "queue_configuration_unchanged": True,
            "result_storage": "existing_PRD_root_config_DATA_DIR/rule_refine/topic_filters",
            "deployment_required": True,
            "running_production_verified": False,
            "application_code_updated": MODULE + "topic_settings.py" in changed,
            "services_started": False,
            "automatic_restart": False,
            "model_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "zeus_posts": 0,
        }
        if not changed:
            return result
        if backup_root.is_symlink():
            raise installer.InstallError("symlink_not_allowed")
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = Path(
            tempfile.mkdtemp(prefix="topic-production-prepare-", dir=backup_root)
        )
        files = {
            name: {"new_sha256": installer.digest(targets[name])} for name in changed
        }
        for name in changed:
            if originals[name] is not None:
                installer._save(backup / "before" / name, originals[name][0])
        installer._save(
            backup / "manifest.json",
            installer._json(
                {
                    "files": [
                        {
                            "path": name,
                            "before_sha256": installer.digest(originals[name][0])
                            if originals[name]
                            else None,
                            **files[name],
                        }
                        for name in changed
                    ]
                }
            ),
        )
        written = []
        try:
            for name in changed:
                before = originals[name]
                installer._replace(
                    root,
                    name,
                    targets[name],
                    before,
                    before[1] if before else 0o644,
                    lambda identity, name=name: written.append((name, identity)),
                )
            for name in changed:
                if installer._read(root, name)[0] != targets[name]:
                    raise installer.InstallError("release_write_verification_failed")
            result["backup_dir"] = str(backup)
            installer._save(backup / "result.json", installer._json(result))
        except Exception:
            rollback = installer._rollback(root, written, originals, files)
            installer._save(backup / "rollback.json", installer._json(rollback))
            raise installer.InstallError(
                "release_prepare_failed_rollback_incomplete"
                if rollback["retained_concurrent_or_unavailable_files"]
                else "release_prepare_failed_rolled_back"
            ) from None
        return result
