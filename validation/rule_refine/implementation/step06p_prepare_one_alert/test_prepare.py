import base64
import copy
import hashlib
import io
import json
import os
import pickle
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import build_command
import runner

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def packet():
    files = build_command.sources()
    return {
        "files": files,
        "source_pins": json.loads(
            (HERE / "source-pins.json").read_text(encoding="utf-8")
        ),
        "pure_pins": {
            name: hashlib.sha256(
                (build_command.PURE_ROOT / name).read_bytes()
            ).hexdigest()
            for name in build_command.PURE_FILES
        },
    }


def load(root):
    raw = json.dumps(packet()).encode()
    runner.PAYLOAD = base64.b64encode(zlib.compress(raw)).decode()
    runner.EXPECTED = hashlib.sha256(raw).hexdigest()
    return runner.load_tools(root)[0]


def request(identity="PRIVATE-ONE", path="/original"):
    return {
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": identity,
        "session_id": "PRIVATE-SESSION",
        "extra_envelope": {"keep": "original"},
        "alert_data": {
            "alert": {
                "alertId": identity,
                "hitLog": [
                    {
                        "topic": "ptp-nids",
                        "ruleCode": "RPAADM_002627",
                        "zeusRawLogs": [{"path": path, "extra": "PRIVATE-RAW"}],
                    }
                ],
            },
            "relatedAlertList": [],
            "extra_data": {"number": 1, "none": None},
        },
    }


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "sec_know_model"
        pure = self.root / "my_workflows/zeus/rule_refine"
        pure.mkdir(parents=True)
        for name in build_command.PURE_FILES:
            (pure / name).write_bytes((build_command.PURE_ROOT / name).read_bytes())
        self.modules = load(self.root)
        self.tool = self.modules["prepare.py"]
        self.data_dir = self.home / "sec-rule-refine-data"
        self.data_dir.mkdir()
        self.csv_path = self.data_dir / "rules.csv"
        self.csv_path.write_text(
            "company_code,topic,rule_code,rule_name,terms,standard_field_config\nSYNTHETIC,ptp-nids,RPAADM_002627,synthetic,[],{}\n",
            encoding="utf-8",
        )
        self.rules = self.modules["csv_config_source.py"].CsvRuleConfigSource(
            self.csv_path
        )
        self.options = {
            "inventory": self.modules["materials_check.py"],
            "rules": self.rules,
            "normalize": self.modules["ingress.py"].normalize_received_alert,
            "request_model": self.modules["request_schema.py"].WorkFlowTaskInput,
        }

    def select(self, rows, identities=None):
        values = {"alert_full_data": rows}
        if identities is not None:
            values["alert_id"] = identities
        return self.tool.select(pd.DataFrame(values), **self.options)

    def fixture_files(self):
        model = self.root / "service/io_models.py"
        model.parent.mkdir()
        model.write_text(build_command.sources()["request_schema.py"], encoding="utf-8")
        sample = self.root / "nas/original" / self.options["inventory"].SAMPLE
        sample.parent.mkdir(parents=True)
        frame = pd.DataFrame({"alert_full_data": [request()]})
        frame.columns = pd.Index(frame.columns.tolist(), dtype=object)
        sample.write_bytes(pickle.dumps(frame))
        self.tool.SAMPLE_SHA = hashlib.sha256(sample.read_bytes()).hexdigest()
        self.tool.CSV_SHA = self.rules.file_sha256
        return sample

    def prepare(self):
        with (
            patch("pathlib.Path.home", return_value=self.home),
            patch.dict(os.environ, {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}),
        ):
            return self.tool.prepare(
                self.root,
                inventory=self.options["inventory"],
                csv_source=self.modules["csv_config_source.py"].CsvRuleConfigSource,
                normalize=self.options["normalize"],
                request_model=self.options["request_model"],
                reviewed_schema_source=build_command.sources()["request_schema.py"],
                source_pins=packet()["source_pins"],
            )

    def test_original_fields_are_preserved_and_only_copies_reach_validation(self):
        original = request()
        before = copy.deepcopy(original)
        normalize = self.options["normalize"]

        def mutating_normalizer(payload, *args, **kwargs):
            result = normalize(payload, *args, **kwargs)
            payload["alert_data"]["extra_data"] = "changed by validator"
            return result

        self.options["normalize"] = mutating_normalizer
        raw, observations, summary = self.select([original], [original["alert_id"]])
        self.assertEqual(json.loads(raw), before)
        self.assertEqual(original, before)
        self.assertEqual(observations[0]["alert_id"], original["alert_id"])
        self.assertFalse(summary["worker_alert_schema_validated"])
        self.assertNotIn("PRIVATE", json.dumps(summary))

    def test_unwrapped_data_gets_envelope_without_modifying_identity_or_fields(self):
        data = request()["alert_data"]
        raw, _, summary = self.select([data])
        saved = json.loads(raw)
        self.assertEqual(saved["alert_data"], data)
        self.assertEqual(saved["alert_id"], data["alert"]["alertId"])
        self.assertTrue(summary["request_wrapper_created"])
        self.assertNotIn("session_id", saved)

    def test_mismatched_identities_and_conflicting_payloads_are_excluded(self):
        bad = request("bad")
        bad["alert_id"] = "different"
        raw, _, summary = self.select(
            [bad, request("conflict", "/a"), request("conflict", "/b"), request("good")]
        )
        self.assertEqual(json.loads(raw)["alert_id"], "good")
        self.assertEqual(summary["conflicting_identity_count"], 1)
        with self.assertRaisesRegex(self.tool.PreparationError, "no_eligible_request"):
            self.select([request()], ["different"])

    def test_numpy_integer_column_agrees_with_unchanged_payload_identity(self):
        raw, _, _ = self.select([request("123")], [123])
        self.assertEqual(json.loads(raw)["alert_id"], "123")

    def test_ambiguous_csv_and_invalid_json_are_not_repaired(self):
        with self.csv_path.open("a", encoding="utf-8") as stream:
            stream.write("SECOND,ptp-nids,RPAADM_002627,synthetic,[],{}\n")
        self.options["rules"] = self.modules[
            "csv_config_source.py"
        ].CsvRuleConfigSource(self.csv_path)
        with self.assertRaises(self.tool.PreparationError):
            self.select([request()])
        self.options["rules"] = self.rules
        duplicate = '{"alert_id":"x","alert_id":"y"}'
        with self.assertRaises(self.tool.PreparationError):
            self.select([duplicate])
        huge = request()
        huge["large"] = "x" * (2 * 1024 * 1024)
        with self.assertRaises(self.tool.PreparationError):
            self.select([huge])

    def test_first_200_only_and_identical_duplicates_count_once(self):
        raw, _, summary = self.select([request(), copy.deepcopy(request())])
        self.assertEqual(summary["eligible_distinct_alerts_for_target"], 1)
        self.assertEqual(json.loads(raw), request())
        with self.assertRaises(self.tool.PreparationError):
            self.select([None] * 200 + [request()])

    def test_reviewed_schema_and_callback_source_pins_match_original_archive(self):
        with zipfile.ZipFile(REPO / "validation/sec_know_model.zip") as archive:
            source = archive.read("service/io_models.py").decode("utf-8")
            self.assertTrue(
                self.tool.request_schema_matches(
                    source, build_command.sources()["request_schema.py"]
                )
            )
            self.assertFalse(
                self.tool.request_schema_matches(
                    source.replace("alert_id: str", "alert_id: int"),
                    build_command.sources()["request_schema.py"],
                )
            )
            for name, digest in packet()["source_pins"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)

    def test_private_local_files_and_summary_separate_from_raw_request(self):
        sample = self.fixture_files()
        before = sample.read_bytes()
        result = self.prepare()
        self.assertEqual(result["status"], "prepared_not_submitted")
        self.assertFalse(
            result["callback_review"]["running_api_and_worker_profile_verified"]
        )
        self.assertEqual(
            result["callback_review"]["callback_profile_for_local_on_disk"], "unknown"
        )
        self.assertNotIn("PRIVATE", json.dumps(result))
        directory = Path(result["output_dir"])
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(
            set(p.name for p in directory.iterdir()),
            {"request.json", "observation-preview.json", "selection.json"},
        )
        for path in directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        saved = (directory / "request.json").read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), result["request_sha256"])
        self.assertEqual(json.loads(saved), request())
        self.assertEqual(sample.read_bytes(), before)
        self.assertEqual(
            sum(
                result[key]
                for key in (
                    "business_alerts_submitted",
                    "model_calls",
                    "redis_calls",
                    "database_calls",
                )
            ),
            0,
        )

    def test_sample_hash_change_stops_before_creating_output(self):
        sample = self.fixture_files()
        sample.write_bytes(b"changed")
        with self.assertRaisesRegex(self.tool.PreparationError, "sample_changed"):
            self.prepare()
        self.assertEqual(list(self.data_dir.iterdir()), [self.csv_path])

    def test_bundle_mismatch_and_non_local_mode_do_not_create_output(self):
        runner.PAYLOAD = "broken"
        with self.assertRaises(Exception):
            runner.load_tools(self.root)
        (self.root / "my_workflows/zeus/rule_refine/ingress.py").write_text(
            "raise RuntimeError('MUST-NOT-IMPORT')", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "installed_pure_module_changed"):
            load(self.root)
        with (
            patch.dict(os.environ, {"env_profile": "PRD"}),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(runner.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "skipped_non_local")
        self.assertEqual(list(self.data_dir.iterdir()), [self.csv_path])

    def test_complete_embedded_command_with_synthetic_inputs(self):
        self.fixture_files()
        p = packet()
        p["files"]["prepare.py"] = (
            p["files"]["prepare.py"]
            .replace(
                "7dcf39081920a8531f7458149f1c98c9d256e297c62c10ee9d385c39a091f2cf",
                self.tool.SAMPLE_SHA,
            )
            .replace(
                "729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976",
                self.tool.CSV_SHA,
            )
        )
        raw = json.dumps(p).encode()
        source = (
            (HERE / "runner.py")
            .read_text(encoding="utf-8")
            .replace("__PAYLOAD__", base64.b64encode(zlib.compress(raw)).decode())
            .replace("__DIGEST__", hashlib.sha256(raw).hexdigest())
        )
        env = {
            **os.environ,
            "HOME": str(self.home),
            "env_profile": "LOCAL",
            "ENV_PROFILE": "LOCAL",
        }
        result = subprocess.run(
            [sys.executable, "-B", "-c", source],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("PRIVATE", result.stdout)
        summary = json.loads(result.stdout.split("\n", 1)[1])
        self.assertEqual(summary["status"], "prepared_not_submitted")
        self.assertEqual(
            json.loads((Path(summary["output_dir"]) / "request.json").read_bytes()),
            request(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
