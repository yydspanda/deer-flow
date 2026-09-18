"""Preparing another experiment cannot grant LAN callers configuration authority."""

import pytest
from fastapi.testclient import TestClient
from test_soc_corpus_batch_workbench import workbench as workbench
from test_soc_corpus_experiment_api import client

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions

ROOT = "/api/soc/dev/corpus-workbench"


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_lan_cannot_reset_saved_options_through_another_same_plan_experiment(tmp_path, monkeypatch, workbench, batch):
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    remote, application, calls = client(tmp_path)
    application.workbench = workbench
    application.dispatcher.start = lambda: None
    local = TestClient(remote.app, client=("127.0.0.1", 1234))
    owner_options = SocAnalysisExecutionOptions(normalization_review_mode="apply").model_dump(mode="json")
    defaults = application.defaults.model_dump(mode="json")
    owner_experiment = local.post(ROOT + "/experiments", json={"experiment_id": "EXP-owner", "name": "Owner settings"})
    assert owner_experiment.status_code == 201, owner_experiment.text
    alert_id = next(member.alert_id for member in workbench.batch_plan.members if member.batch == batch)
    selection = {"batch": batch, "alert_ids": [alert_id]}
    body = {"experiment_id": "EXP-owner", "selection": selection, "options": owner_options, "memory_mode": "none"}
    owner_round = local.post(ROOT + "/rounds", json=body, headers={"Idempotency-Key": "owner-options"})
    assert owner_round.status_code == 201, owner_round.text
    config_path = ROOT + f"/experiments/configuration?batch={batch}"
    assert remote.get(config_path).json()["saved_options"] == owner_options

    secondary = remote.post(ROOT + "/experiments", json={"experiment_id": "EXP-lan", "name": "Another same-plan experiment"})
    assert secondary.status_code == 201, secondary.text
    assert secondary.json()["plan_id"] == owner_experiment.json()["plan_id"]
    assert application.service.store.find_plan_experiment(workbench.batch_plan_id).experiment_id == "EXP-owner"
    attempted_reset = remote.post(ROOT + "/rounds", json={**body, "experiment_id": "EXP-lan", "options": defaults}, headers={"Idempotency-Key": "remote-reset"})
    assert attempted_reset.status_code == 403, attempted_reset.text
    assert application.service.store.list_rounds(experiment_id="EXP-lan") == []

    allowed = remote.post(ROOT + "/rounds", json={**body, "experiment_id": "EXP-lan"}, headers={"Idempotency-Key": "remote-saved"})
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["options"] == owner_options
    assert remote.get(config_path).json()["saved_options"] == owner_options

    updated_options = SocAnalysisExecutionOptions(normalization_review_mode="shadow").model_dump(mode="json")
    updated = local.post(ROOT + "/rounds", json={**body, "options": updated_options}, headers={"Idempotency-Key": "owner-update"})
    assert updated.status_code == 201, updated.text
    assert remote.get(config_path).json()["saved_options"] == updated_options
    stale_fork = remote.post(ROOT + "/rounds", json={**body, "experiment_id": "EXP-lan"}, headers={"Idempotency-Key": "remote-fork-options"})
    assert stale_fork.status_code == 403, stale_fork.text
    current_fork = remote.post(ROOT + "/rounds", json={**body, "experiment_id": "EXP-lan", "options": updated_options}, headers={"Idempotency-Key": "remote-current-options"})
    assert current_fork.status_code == 201, current_fork.text
    assert application.service.store.get_round(allowed.json()["round_id"]).options.model_dump(mode="json") == owner_options

    # Settings are scoped to a batch: its untouched sibling still uses deployment
    # defaults, including through a caller-created experiment for the same plan.
    other_batch = "validation" if batch == "learning" else "learning"
    other_alert = next(member.alert_id for member in workbench.batch_plan.members if member.batch == other_batch)
    assert remote.get(ROOT + f"/experiments/configuration?batch={other_batch}").json()["saved_options"] == defaults
    first_other_round = remote.post(
        ROOT + "/rounds",
        json={**body, "experiment_id": "EXP-lan", "selection": {"batch": other_batch, "alert_ids": [other_alert]}, "options": defaults},
        headers={"Idempotency-Key": "other-batch-defaults"},
    )
    assert first_other_round.status_code == 201, first_other_round.text
    assert first_other_round.json()["options"] == defaults
    assert not calls


def test_lan_cannot_create_new_work_from_an_old_plan_with_shared_members(tmp_path, monkeypatch, workbench):
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    remote, application, calls = client(tmp_path)
    application.dispatcher.start = lambda: None
    local = TestClient(remote.app, client=("127.0.0.1", 1234))
    defaults = application.defaults.model_dump(mode="json")
    old_body = {"experiment_id": "EXP-test", "selection": {"batch": "learning", "alert_ids": ["0"]}, "options": defaults, "memory_mode": "none"}
    old_round = local.post(ROOT + "/rounds", json=old_body, headers={"Idempotency-Key": "old-plan-before-update"})
    assert old_round.status_code == 201, old_round.text

    application.workbench = workbench
    prepared = local.post(ROOT + "/experiments", json={"experiment_id": "EXP-current", "name": "Current plan"})
    assert prepared.status_code == 201, prepared.text
    current_member = application.service.store.get_member("EXP-current", "0")
    old_member = application.service.store.get_member("EXP-test", "0")
    assert (old_member.alert_id, old_member.payload_hash, old_member.source_index) == (current_member.alert_id, current_member.payload_hash, current_member.source_index)
    assert application.service.store.get_experiment("EXP-test").plan_id != workbench.batch_plan_id
    owner_options = SocAnalysisExecutionOptions(normalization_review_mode="apply").model_dump(mode="json")
    owner_round = local.post(ROOT + "/rounds", json={**old_body, "experiment_id": "EXP-current", "options": owner_options}, headers={"Idempotency-Key": "current-plan-options"})
    assert owner_round.status_code == 201, owner_round.text
    assert remote.get(ROOT + "/experiments/configuration?batch=learning").json()["saved_options"] == owner_options

    rejected = remote.post(ROOT + "/rounds", json=old_body, headers={"Idempotency-Key": "old-plan-new-work"})
    assert rejected.status_code == 403, rejected.text
    assert "当前语料" in rejected.json()["detail"]
    assert len(application.service.store.list_rounds(experiment_id="EXP-test")) == 1
    replayed = remote.post(ROOT + "/rounds", json=old_body, headers={"Idempotency-Key": "old-plan-before-update"})
    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["round_id"] == old_round.json()["round_id"]
    assert not calls
