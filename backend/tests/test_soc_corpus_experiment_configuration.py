import json
from dataclasses import replace
from types import SimpleNamespace

from soc_agent.application import corpus_experiments as application
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.llm import SocLLMSettings


def test_configuration_freezes_behavior_without_exporting_secrets_or_capacity(monkeypatch, tmp_path):
    model = SimpleNamespace(model_dump=lambda **_: {"name": "test", "api_key": "secret-model-token", "api_base": "http://private-endpoint"})
    monkeypatch.setattr(application, "get_app_config", lambda: SimpleNamespace(models=[model]))
    monkeypatch.setattr(application, "_implementation_hash", lambda: "a" * 64)
    path = tmp_path / "private.json"
    path.write_text('{"key": "private-catalog-token"}', encoding="utf-8")
    monkeypatch.setenv("SOC_TENANT_DISPOSITION_POLICY_PATH", str(path))
    settings, options = SocLLMSettings(), SocAnalysisExecutionOptions()
    original = application.corpus_configuration_snapshot(settings, options)
    serialized = json.dumps(original)
    for hidden in ("secret-model-token", "private-endpoint", str(path), "private-catalog-token"):
        assert hidden not in serialized
    changed_capacity = application.corpus_configuration_snapshot(replace(settings, max_concurrency=3), options)
    assert changed_capacity == original
    path.write_text('{"key": "private-catalog-changed"}', encoding="utf-8")
    assert application.corpus_configuration_snapshot(settings, options) != original
