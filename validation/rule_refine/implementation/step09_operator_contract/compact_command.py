"""Build a pasteable delta command with the identical reviewed final payload."""

import base64
import copy
import difflib
import hashlib
import importlib.util
import inspect
import json
import lzma
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
INLINE_OMISSION = "test/rule_refine/test_legacy_completed_operator.py"


def inline_packet(packet):
    """Omit only the large synthetic frozen-history test from pasted transport.

    The full installer retains this test, and it has already passed locally.
    Every runtime source and all remaining tests retain identical target hashes.
    """
    result = copy.deepcopy(packet)
    omitted = result["files"].pop(INLINE_OMISSION)
    if (
        omitted["old_sha256"] is not None
        or INLINE_OMISSION in result["test_files"]
        or INLINE_OMISSION in result["dependencies"]
    ):
        raise ValueError("unexpected_inline_fixture_omission")
    result["tests"].remove(INLINE_OMISSION)
    result["delivery_profile"] = "inline_without_frozen_history_fixture"
    result["local_and_full_package_only_tests"] = [INLINE_OMISSION]
    return result


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _apply_delta(raw, edits, installer):
    lines = raw.decode("utf-8").splitlines(keepends=True)
    cursor, parts = 0, []
    if not isinstance(edits, list):
        raise installer.InstallError("invalid_compact_delta")
    for edit in edits:
        if (
            not isinstance(edit, list)
            or len(edit) != 3
            or type(edit[0]) is not int
            or type(edit[1]) is not int
            or not isinstance(edit[2], str)
            or not cursor <= edit[0] <= edit[1] <= len(lines)
        ):
            raise installer.InstallError("invalid_compact_delta")
        start, end, replacement = edit
        parts.extend(lines[cursor:start])
        parts.append(replacement)
        cursor = end
    parts.extend(lines[cursor:])
    return "".join(parts).encode("utf-8")


def reconstruct(bundle, root, installer):
    """Read/hash-check local baseline bytes; reconstruct only in memory."""
    root = Path(root).absolute()
    packet = copy.deepcopy(bundle["packet"])
    packet["files"], packet["test_files"] = {}, {}
    for relative, entry in bundle["files"].items():
        found = installer._read(root, relative)
        raw = found[0] if found is not None else None
        current = _digest(raw) if raw is not None else None
        if current == entry["new_sha256"]:
            after = raw
        elif current == entry["old_sha256"]:
            if current is None:
                after = entry["content"].encode("utf-8")
            else:
                after = _apply_delta(raw, entry["edits"], installer)
        else:
            raise installer.InstallError(
                "unknown_project_file_version",
                relative_file=relative,
                actual_sha256=current,
            )
        if _digest(after) != entry["new_sha256"]:
            raise installer.InstallError(
                "compact_result_hash_mismatch", relative_file=relative
            )
        packet["files"][relative] = {
            "old_sha256": entry["old_sha256"],
            "new_sha256": entry["new_sha256"],
            "content": after.decode("utf-8"),
        }
    for relative, expected in bundle["test_files"].items():
        found = installer._read(root, relative)
        if found is None or _digest(found[0]) != expected:
            raise installer.InstallError(
                "compact_test_version_mismatch", relative_file=relative
            )
        packet["test_files"][relative] = found[0].decode("utf-8")
    for relative, expected in packet["dependencies"].items():
        found = installer._read(root, relative)
        if found is None or _digest(found[0]) != expected:
            raise installer.InstallError(
                "dependency_version_mismatch", relative_file=relative
            )
    installer.validate_packet(packet)
    return packet


def make_bundle(packet, baseline, installer_source):
    metadata = {
        k: copy.deepcopy(v)
        for k, v in packet.items()
        if k not in {"files", "test_files"}
    }
    files = {}
    for relative, entry in packet["files"].items():
        before = baseline.get(relative)
        after = entry["content"].encode("utf-8")
        if (_digest(before) if before is not None else None) != entry["old_sha256"]:
            raise ValueError("compact_baseline_mismatch")
        if _digest(after) != entry["new_sha256"]:
            raise ValueError("compact_target_mismatch")
        delta = {key: entry[key] for key in ("old_sha256", "new_sha256")}
        if before is None:
            delta["content"] = entry["content"]
        else:
            old_lines = before.decode("utf-8").splitlines(keepends=True)
            new_lines = entry["content"].splitlines(keepends=True)
            delta["edits"] = [
                [i1, i2, "".join(new_lines[j1:j2])]
                for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(
                    None, old_lines, new_lines, autojunk=False
                ).get_opcodes()
                if kind != "equal"
            ]
        files[relative] = delta
    return {
        "packet": metadata,
        "files": files,
        "test_files": {
            name: _digest(text.encode("utf-8"))
            for name, text in packet["test_files"].items()
        },
        "installer": installer_source,
        "reconstructor": "import copy, hashlib\nfrom pathlib import Path\n\n"
        + "\n\n".join(
            inspect.getsource(fn) for fn in (_digest, _apply_delta, reconstruct)
        ),
    }


def packtext(bundle):
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = "\n".join(
        textwrap.wrap(
            base64.b64encode(lzma.compress(raw, preset=6)).decode("ascii"), 100
        )
    )
    return (
        "bash <<'SOC_OPERATORS'\nset -euo pipefail\numask 077\n"
        'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"\n'
        ".venv/bin/python -B - <<'PY_OPERATORS'\n"
        "import base64,hashlib,json,lzma,os,types\nfrom pathlib import Path\n"
        "d=lzma.LZMADecompressor(memlimit=67108864)\n"
        'raw=d.decompress(base64.b64decode("""'
        + encoded
        + '"""),max_length='
        + str(len(raw) + 1)
        + ")\n"
        f'if len(raw)!={len(raw)} or not d.eof or d.unused_data or hashlib.sha256(raw).hexdigest()!="{_digest(raw)}":\n'
        '    raise SystemExit("命令载荷不完整，未修改项目。")\n'
        'b=json.loads(raw);i=types.ModuleType("operator_installer");exec(compile(b["installer"],"<installer>","exec"),i.__dict__)\n'
        'n={};exec(compile(b["reconstructor"],"<reconstruct>","exec"),n)\n'
        'print("先校验源码并执行离线回归，通过后备份更新；不调用模型、不启动服务。",flush=True)\n'
        "try:\n"
        '    p=n["reconstruct"](b,Path.cwd(),i)\n'
        '    result=i.apply(Path.cwd(),Path(os.environ.get("SEC_RULE_REFINE_DATA_ROOT",str(Path.home()/"sec-rule-refine-data"))),p)\n'
        "except Exception as e:\n"
        '    result={"check":"rule_refine_operator_contract_install","status":"failed","error_type":type(e).__name__,"automatic_restart":False}\n'
        "    if isinstance(e,i.InstallError): result.update(error_code=str(e),file=e.relative_file,actual_sha256=e.actual_sha256)\n"
        "    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(1)\n"
        'result["delivery_profile"]=p.get("delivery_profile","full")\n'
        'result["local_and_full_package_only_tests"]=p.get("local_and_full_package_only_tests",[])\n'
        "print(json.dumps(result,ensure_ascii=False,indent=2))\n"
        "PY_OPERATORS\nSOC_OPERATORS\n"
    )


def main():
    spec = importlib.util.spec_from_file_location(
        "operator_compact_builder", HERE / "build_command.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    packet = inline_packet(builder.contents())
    bundle = make_bundle(packet, builder.baseline(), builder.installer_source())
    command = packtext(bundle)
    target = HERE / "PASTE-OPERATORS.sh"
    target.write_text(command, encoding="utf-8")
    manifest = {
        "delivery_profile": packet["delivery_profile"],
        "local_and_full_package_only_tests": packet[
            "local_and_full_package_only_tests"
        ],
        "files": {
            name: {key: entry[key] for key in ("old_sha256", "new_sha256")}
            for name, entry in packet["files"].items()
        },
        "regression_test_files": packet["tests"],
        "command_sha256": _digest(command.encode("utf-8")),
    }
    (HERE / "compact-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "path": str(target),
                "bytes": len(command.encode("utf-8")),
                "sha256": _digest(command.encode("utf-8")),
            }
        )
    )


if __name__ == "__main__":
    main()
