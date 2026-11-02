"""Read diagnostic tool metadata without importing it or loading application config."""

import ast
import hashlib
import json
from pathlib import Path


def normalized(node):
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[key, normalized(value)] for key, value in ast.iter_fields(node)
                                    if not (key == "type_params" and value == [])]]
    if isinstance(node, list):
        return [normalized(value) for value in node]
    return node


def check(folder):
    report = {"check": "diagnostic_tool_version", "read_only": True, "model_calls": 0}
    try:
        raw = (folder / "diagnostics.py").read_bytes()
        if len(raw) > 128 * 1024:
            raise ValueError("tool_too_large")
        report.update(bytes=len(raw), actual_sha256=hashlib.sha256(raw).hexdigest())
        report["matches_expected_file"] = report["actual_sha256"] == "d55134938043e1ab0796b1dd77de77193ca11008cb4fb881454359f66391a194"
        tree = normalized(ast.parse(raw.decode("utf-8-sig")))
        encoded = json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        report["ast_sha256"] = hashlib.sha256(encoded).hexdigest()
        report["matches_expected_structure"] = report["ast_sha256"] == "070745d9ebab39ed15c485f91edf1c351be6973b5ae01795efc5d0e8922296b3"
        manifest_raw = (folder / "diagnostic-manifest.json").read_bytes()
        if len(manifest_raw) > 64 * 1024:
            raise ValueError("manifest_too_large")
        previous = json.loads(manifest_raw)["diagnostic_helper_sha256"]
        report["matches_original_record"] = previous == report["actual_sha256"]
        entry = (folder / "entry.py").read_bytes()
        report["entry_sha256"] = hashlib.sha256(entry).hexdigest()
        report["entry_matches_expected"] = report["entry_sha256"] == "b57aa57fcd0b5011a15ccb2ed636d519ba1ced4bb9de0160c6bbeb887c2ff0be"
    except Exception as error:
        report["check_error_type"] = type(error).__name__
    return report


if __name__ == "__main__":
    folder = Path.home() / "sec-rule-refine-data/synthetic-chain-diagnostic-o4olbtkt"
    print(json.dumps(check(folder), ensure_ascii=False, indent=2))
