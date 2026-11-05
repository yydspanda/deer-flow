"""Explicit, offline activation of installed topic filtering; never start services."""

import copy
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

CONFIG = "config/rule_refine.topic.json"
MODULE = "my_workflows/zeus/rule_refine/"
OVERRIDES = {
    "RULE_REFINE_MONITOR_ENABLED",
    "RULE_REFINE_CSV_PATH",
    "RULE_REFINE_NAMESPACE",
}


def _json_object(root, relative, installer):
    found = installer._read(root, relative)
    if found is None:
        return {}, None
    if len(found[0]) > 16384:
        raise installer.InstallError(
            "configuration_file_too_large", relative_file=relative
        )
    try:
        value = json.loads(found[0], object_pairs_hook=installer._unique)
    except Exception:
        raise installer.InstallError(
            "invalid_configuration_json", relative_file=relative
        ) from None
    if not isinstance(value, dict):
        raise installer.InstallError(
            "configuration_object_required", relative_file=relative
        )
    return value, found


def _settings_module(checked, installer):
    """Only two hash-verified, stdlib-only modules are evaluated, never root_config."""
    package_name = "_topic_activation_settings"
    package = types.ModuleType(package_name)
    package.__path__ = []
    names = [package_name]
    sys.modules[package_name] = package
    try:
        for name in ("surge_settings", "topic_settings"):
            full_name = package_name + "." + name
            module = types.ModuleType(full_name)
            module.__package__ = package_name
            sys.modules[full_name] = module
            names.append(full_name)
            raw = checked.get(MODULE + name + ".py")
            if raw is None:
                raise installer.InstallError("topic_installation_missing")
            exec(compile(raw[0], "<verified-topic-settings>", "exec"), module.__dict__)
        return module
    finally:
        for name in reversed(names):
            sys.modules.pop(name, None)


def configure(root, backup_root, environ, expected, installer):
    root, backup_root = Path(root).absolute(), Path(backup_root).absolute()
    env = dict(environ)
    with installer._lock(root):
        checked = {}
        for name, digest in expected.items():
            found = installer._read(root, name)
            if found is None or installer.digest(found[0]) != digest:
                raise installer.InstallError(
                    "topic_installation_version_mismatch", relative_file=name
                )
            checked[name] = found
        settings_module = _settings_module(checked, installer)
        profile = settings_module.profile(env)
        if profile not in {"LOCAL", "PRD"}:
            raise installer.InstallError("topic_enable_requires_LOCAL_or_PRD")
        unknown = sorted(
            key
            for key in env
            if key.startswith("RULE_REFINE_") and key not in OVERRIDES
        )
        if unknown:
            raise installer.InstallError(
                "unreviewed_rule_refine_environment_override:" + ",".join(unknown)
            )
        existing, config_before = _json_object(root, CONFIG, installer)
        if set(existing) - set(settings_module.DEFAULTS):
            raise installer.InstallError(
                "invalid_topic_configuration_fields", relative_file=CONFIG
            )
        value = copy.deepcopy(settings_module.DEFAULTS)
        if not existing and profile == "LOCAL":
            local, _ = _json_object(root, "config/rule_refine.local.json", installer)
            analysis, _ = _json_object(
                root, "config/rule_refine.analysis.local.json", installer
            )
            surge, _ = _json_object(
                root, "config/rule_refine.surge.local.json", installer
            )
            if "csv_path" in local:
                value["csv_path"] = local["csv_path"]
            if "model" in analysis:
                value["model"] = analysis["model"]
            for key in (
                "context_tokens",
                "input_fraction",
                "timeout_seconds",
                "operator_context",
            ):
                if key in surge:
                    value[key] = surge[key]
        value.update(existing)
        value["enabled"] = True
        requested_csv = env.get("SEC_TOPIC_CSV_PATH")
        if requested_csv is not None:
            value["csv_path"] = requested_csv
        elif profile == "PRD" and not existing.get("csv_path"):
            raise installer.InstallError("PRD_requires_SEC_TOPIC_CSV_PATH")
        if not isinstance(value.get("csv_path"), str) or not value["csv_path"].strip():
            raise installer.InstallError("existing_rule_csv_path_required")
        csv_path = Path(value["csv_path"]).expanduser()
        csv_path = csv_path if csv_path.is_absolute() else root / csv_path
        csv_path = csv_path.resolve()
        if not csv_path.is_file() or not os.access(csv_path, os.R_OK):
            raise installer.InstallError("rule_csv_not_readable")
        value["csv_path"] = str(csv_path)
        if not value.get("namespace"):
            value["namespace"] = "topic-v1:" + profile.lower()
        desired = {
            "RULE_REFINE_MONITOR_ENABLED": "1",
            "RULE_REFINE_CSV_PATH": value["csv_path"],
            "RULE_REFINE_NAMESPACE": value["namespace"],
        }
        requires_restart = []
        for key in sorted(OVERRIDES):
            if key not in env:
                continue
            actual = env[key]
            if key == "RULE_REFINE_CSV_PATH":
                path = Path(actual).expanduser()
                actual = str((path if path.is_absolute() else root / path).resolve())
            if actual == desired[key]:
                continue
            if (
                key == "RULE_REFINE_MONITOR_ENABLED"
                and actual == "0"
                and profile == "PRD"
            ):
                requires_restart.append(key)
                continue
            raise installer.InstallError("conflicting_environment_override:" + key)

        raw_config = installer._json(value) + b"\n"
        if len(raw_config) > 16384:
            raise installer.InstallError("topic_configuration_too_large")
        with tempfile.TemporaryDirectory(prefix="topic-enable-validation-") as tmp:
            installer._save(Path(tmp) / CONFIG, raw_config)
            parsed = settings_module.load_settings(Path(tmp), {"env_profile": profile})
            if parsed is None:
                raise installer.InstallError("topic_configuration_not_enabled")

        originals, targets = {CONFIG: config_before}, {CONFIG: raw_config}
        if profile == "PRD":
            run_before = installer._read(root, "run.sh")
            if run_before is None:
                raise installer.InstallError(
                    "production_launcher_missing", relative_file="run.sh"
                )
            try:
                source = run_before[0].decode("utf-8")
            except UnicodeDecodeError:
                raise installer.InstallError("launcher_encoding_invalid") from None
            references = [
                line
                for line in source.splitlines()
                if "RULE_REFINE_MONITOR_ENABLED" in line
                and not line.lstrip().startswith("#")
            ]
            marker = re.compile(
                r"^export RULE_REFINE_MONITOR_ENABLED=([01])$", re.MULTILINE
            )
            matches = list(marker.finditer(source))
            if len(matches) != 1 or len(references) != 1:
                raise installer.InstallError(
                    "review_run_sh_monitor_marker", relative_file="run.sh"
                )
            after = marker.sub("export RULE_REFINE_MONITOR_ENABLED=1", source)
            originals["run.sh"], targets["run.sh"] = run_before, after.encode("utf-8")
        for name, found in checked.items():
            if not installer._same(installer._read(root, name), found):
                raise installer.InstallError("topic_installation_changed_during_enable")
        changed = [
            name
            for name in (CONFIG, "run.sh")
            if name in targets
            and (originals[name] is None or originals[name][0] != targets[name])
        ]
        result = {
            "check": "rule_refine_topic_enable",
            "status": "already_enabled" if not changed else "enabled_for_next_start",
            "profile": profile,
            "configuration_file": str(root / CONFIG),
            "namespace": value["namespace"],
            "csv_path": value["csv_path"],
            "files_changed": len(changed),
            "launch_script_updated": "run.sh" in changed,
            "restart_required": True,
            "current_process_disable_requires_restart": bool(requires_restart),
            "services_started": False,
            "automatic_restart": False,
            "model_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "zeus_callback_calls": 0,
            "submission_after_restart": "reviewed_topic_proposals_to_ZEUS",
            "ZEUS_activation_managed_by_backend": True,
        }
        if not changed:
            return result
        if backup_root.is_symlink():
            raise installer.InstallError("symlink_not_allowed")
        backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="topic-enable-", dir=backup_root))
        for name in changed:
            if originals[name] is not None:
                installer._save(backup / "before" / name, originals[name][0])
        files = {
            name: {"new_sha256": installer.digest(targets[name])} for name in changed
        }
        installer._save(
            backup / "manifest.json",
            installer._json(
                {
                    "profile": profile,
                    "files": [
                        {
                            "path": name,
                            "before_sha256": installer.digest(originals[name][0])
                            if originals[name]
                            else None,
                            **files[name],
                        }
                        for name in changed
                    ],
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
                    before[1] if before else 0o600,
                    lambda identity, name=name: written.append((name, identity)),
                )
            result["backup_dir"] = str(backup)
            installer._save(backup / "result.json", installer._json(result))
        except Exception:
            restored = installer._rollback(root, written, originals, files)
            installer._save(backup / "rollback.json", installer._json(restored))
            raise installer.InstallError(
                "topic_enable_failed_rollback_incomplete"
                if restored["retained_concurrent_or_unavailable_files"]
                else "topic_enable_failed_rolled_back"
            ) from None
        return result
