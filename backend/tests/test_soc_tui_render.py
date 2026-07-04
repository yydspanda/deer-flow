from __future__ import annotations

from rich.console import Console

from soc_agent.contracts import ActorContext, EntrySurface, SocAgentApprovalRequest, SocAgentRiskLevel
from soc_agent.tui.render import render_approval_request


def test_render_approval_request_shows_action_proposal_context() -> None:
    approval_request = SocAgentApprovalRequest(
        approval_request_id="APR-1",
        permission_decision_id="PERM-1",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="Lead Agent proposed action response.block_ip: Block source IP",
        requested_by=ActorContext(actor_id="analyst", surface=EntrySurface.TUI),
        source_proposal_id="SAP-1",
        action_payload={"ip": "1.2.3.4"},
        context_refs={"queue_id": "REV-1", "context_hash": "hash-1"},
    )
    console = Console(record=True, width=120)

    console.print(render_approval_request(approval_request))

    output = console.export_text()
    assert "source_proposal_id" in output
    assert "SAP-1" in output
    assert "action_payload" in output
    assert '"ip": "1.2.3.4"' in output
    assert "context_refs" in output
    assert '"queue_id": "REV-1"' in output
