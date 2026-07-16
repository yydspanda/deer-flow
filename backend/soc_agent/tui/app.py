"""Textual ReviewQueue workbench for SOC Agent."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from deerflow.tui.theme import THEME
from deerflow.tui.widgets.composer import ComposerInput
from soc_agent.contracts import (
    ActorContext,
    CorrectionCommand,
    EntrySurface,
    NormalizationMaintenanceIssueStatus,
    NormalizationMaintenanceIssueUpdateCommand,
    ReviewQueueCloseCommand,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocAgentApprovedActionCommand,
    SocDispositionOutcomeCommand,
    SocDispositionOutcomeReviewKind,
    SocDispositionOutcomeSource,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import (
    SocAgentApprovalService,
    SocDispositionEvaluationService,
    SocNormalizationMaintenanceService,
    SocReviewService,
    SocServiceError,
)
from soc_agent.tui.command_registry import filter_commands, resolve
from soc_agent.tui.render import render_header, render_main, render_palette, render_status
from soc_agent.tui.view_state import (
    add_notice,
    initial_state,
    select_approval_request,
    select_context,
    set_approval_action_result,
    set_approval_grant,
    set_approval_requests,
    set_items,
    set_loading,
    set_normalization_issues,
)

_HELP_TEXT = (
    "Commands: /refresh  /approvals  /normalization  "
    "/norm-update NMI-... acknowledged|resolved|ignored reason  "
    "/approval APR-...  /approve APR-... reason  /dry-run SAT-... route action  "
    "/execute SAT-... route action idempotency-key  /open REV-...  "
    "/close REV-... reason  /correct RUN-... verdict reason  "
    "/outcome DPROP-... disposition idempotency-key reason  "
    "/sample-outcome DSAMPLE-... DPROP-... disposition idempotency-key reason  /quit"
)


class SocReviewTUI(App):
    CSS = f"""
    Screen {{
        background: {THEME.bg};
        color: {THEME.text};
    }}
    #header {{
        height: 1;
        padding: 0 1;
        background: {THEME.panel};
    }}
    #scroll {{
        height: 1fr;
        padding: 1 2;
        background: {THEME.bg};
        scrollbar-size-vertical: 1;
    }}
    #main {{
        width: 100%;
        height: auto;
    }}
    #status {{
        height: 1;
        padding: 0 1;
        background: {THEME.panel};
        color: {THEME.muted};
    }}
    #palette {{
        height: auto;
        max-height: 10;
        margin: 0 1;
        padding: 0 1;
        background: {THEME.panel};
        border: round {THEME.border};
        display: none;
    }}
    #palette.open {{
        display: block;
    }}
    #composer {{
        height: 3;
        margin: 0 1 1 1;
        border: round {THEME.border};
        background: {THEME.panel};
    }}
    #composer:focus {{
        border: round {THEME.primary};
    }}
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True, show=True),
        Binding("ctrl+l", "redraw", "Redraw", show=False),
        Binding("down", "nav_down", show=False, priority=True),
        Binding("up", "nav_up", show=False, priority=True),
        Binding("tab", "palette_complete", show=False, priority=True),
        Binding("enter", "palette_accept", show=False, priority=True),
        Binding("escape", "escape", show=False, priority=True),
    ]

    def __init__(
        self,
        service: SocReviewService,
        *,
        approval_service: SocAgentApprovalService | None = None,
        normalization_service: SocNormalizationMaintenanceService | None = None,
        disposition_evaluation_service: SocDispositionEvaluationService | None = None,
        actor_id: str = "soc-review-tui",
        database_label: str = "",
    ) -> None:
        super().__init__()
        self.service = service
        self.approval_service = approval_service
        self.normalization_service = normalization_service
        self.disposition_evaluation_service = disposition_evaluation_service
        self.actor_id = actor_id
        self.database_label = database_label
        self.state = initial_state()
        self._palette_open = False
        self._palette_items = []
        self._palette_index = 0

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with VerticalScroll(id="scroll"):
            yield Static(id="main")
        yield Static(id="status")
        yield Static(id="palette")
        yield ComposerInput(placeholder="SOC review command...   ( / for commands )", id="composer")

    def on_mount(self) -> None:
        self._refresh_all()
        self._load_queue()
        self._load_approval_requests()
        self._load_normalization_issues()
        self.query_one("#composer", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self._close_palette()
        if text:
            self._handle_submit(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        if value.startswith("/") and " " not in value:
            self._palette_index = 0
            self._open_palette(filter_commands(value[1:]))
        else:
            self._close_palette()

    def _handle_submit(self, text: str) -> None:
        resolution = resolve(text)
        if resolution.kind == "unknown":
            self._notice(f"Unknown command {resolution.name!r}. Try /help.", tone="error")
            return
        self._handle_builtin(resolution.name, resolution.args)

    def _handle_builtin(self, name: str, args: str) -> None:
        if name == "quit":
            self.exit()
        elif name == "help":
            self._notice(_HELP_TEXT)
        elif name == "refresh":
            self._load_queue()
            self._load_approval_requests()
            self._load_normalization_issues()
        elif name == "approvals":
            self._load_approval_requests()
        elif name == "normalization":
            self._load_normalization_issues()
        elif name == "norm-update":
            self._update_normalization_issue(args)
        elif name == "approval":
            self._open_approval_request(args)
        elif name == "approve":
            self._approve_request(args)
        elif name == "dry-run":
            self._dry_run_approved_action(args)
        elif name == "execute":
            self._execute_approved_action(args)
        elif name == "open":
            self._open_context(args)
        elif name == "close":
            self._close_item(args)
        elif name == "correct":
            self._correct_run(args)
        elif name == "outcome":
            self._record_disposition_outcome(args, sampled=False)
        elif name == "sample-outcome":
            self._record_disposition_outcome(args, sampled=True)
        else:
            self._notice(f"/{name} is not available.", tone="error")

    def _load_queue(self) -> None:
        self.state = set_loading(self.state)
        self._refresh_status()
        try:
            items = self.service.list_queue(status=ReviewQueueStatus.OPEN, limit=50)
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = set_items(self.state, items)
        self._refresh_all()

    def _load_approval_requests(self) -> None:
        if self.approval_service is None:
            self._notice("Approval service is not configured.", tone="error")
            return
        self.state = set_loading(self.state)
        self._refresh_status()
        try:
            requests = self.approval_service.list_requests(status="pending", limit=50)
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = set_approval_requests(self.state, requests)
        self._refresh_all()

    def _load_normalization_issues(self) -> None:
        if self.normalization_service is None:
            return
        self.state = set_loading(self.state)
        self._refresh_status()
        try:
            issues = self.normalization_service.list_issues(
                status=NormalizationMaintenanceIssueStatus.OPEN,
                limit=50,
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = set_normalization_issues(self.state, issues)
        self._refresh_all()

    def _update_normalization_issue(self, args: str) -> None:
        issue_id, status, reason = _parse_normalization_update_args(args)
        if not issue_id or status not in {"acknowledged", "resolved", "ignored"} or not reason:
            self._notice(
                "Usage: /norm-update NMI-... acknowledged|resolved|ignored reason",
                tone="error",
            )
            return
        if self.normalization_service is None:
            self._notice("Normalization service is not configured.", tone="error")
            return
        try:
            self.normalization_service.update_issue(
                NormalizationMaintenanceIssueUpdateCommand(
                    issue_id=issue_id,
                    status=status,
                    reason=reason,
                ),
                context=_tui_normalization_context(),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self._notice(f"Updated normalization issue {issue_id} -> {status}.")
        self._load_normalization_issues()

    def _open_context(self, queue_id: str) -> None:
        queue_id = queue_id.strip()
        if not queue_id:
            self._notice("Usage: /open REV-...", tone="error")
            return
        try:
            context = self.service.get_investigation_context(queue_id)
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = select_context(self.state, context)
        self._refresh_all()

    def _open_approval_request(self, approval_request_id: str) -> None:
        approval_request_id = approval_request_id.strip()
        if not approval_request_id:
            self._notice("Usage: /approval APR-...", tone="error")
            return
        if self.approval_service is None:
            self._notice("Approval service is not configured.", tone="error")
            return
        try:
            approval_request = self.approval_service.get_request(approval_request_id)
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = select_approval_request(self.state, approval_request)
        self._refresh_all()

    def _approve_request(self, args: str) -> None:
        approval_request_id, _, reason = args.partition(" ")
        if not approval_request_id or not reason.strip():
            self._notice("Usage: /approve APR-... reason", tone="error")
            return
        if self.approval_service is None:
            self._notice("Approval service is not configured.", tone="error")
            return
        try:
            approval_request = self.approval_service.get_request(approval_request_id)
            grant = self.approval_service.approve(
                approval_request,
                context=_tui_approval_context(),
                reason=reason.strip(),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = select_approval_request(self.state, approval_request)
        self.state = set_approval_grant(self.state, grant)
        self._notice(f"Approved {approval_request_id}; token {grant.execution_token_id}.")
        self._load_approval_requests()

    def _dry_run_approved_action(self, args: str) -> None:
        execution_token_id, route, action, extra = _parse_approved_action_args(args)
        if not execution_token_id or not route or not action or extra:
            self._notice("Usage: /dry-run SAT-... route action", tone="error")
            return
        if self.approval_service is None:
            self._notice("Approval service is not configured.", tone="error")
            return
        try:
            result = self.approval_service.dry_run_approved_action(
                SocAgentApprovedActionCommand(
                    execution_token_id=execution_token_id,
                    route=route,
                    action=action,
                    dry_run=True,
                ),
                context=_tui_request_context(),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = set_approval_action_result(self.state, result)
        self._notice(f"Dry-run validated {execution_token_id}.")

    def _execute_approved_action(self, args: str) -> None:
        execution_token_id, route, action, idempotency_key = _parse_approved_action_args(args)
        if not execution_token_id or not route or not action or not idempotency_key:
            self._notice("Usage: /execute SAT-... route action idempotency-key", tone="error")
            return
        if self.approval_service is None:
            self._notice("Approval service is not configured.", tone="error")
            return
        try:
            result = self.approval_service.execute_approved_action(
                SocAgentApprovedActionCommand(
                    execution_token_id=execution_token_id,
                    route=route,
                    action=action,
                    dry_run=False,
                ),
                context=_tui_request_context(idempotency_key=idempotency_key),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = set_approval_action_result(self.state, result)
        self._notice(f"Executed approval boundary for {execution_token_id}.")

    def _close_item(self, args: str) -> None:
        queue_id, _, reason = args.partition(" ")
        if not queue_id or not reason.strip():
            self._notice("Usage: /close REV-... reason", tone="error")
            return
        try:
            self.service.close_queue_item(
                ReviewQueueCloseCommand(queue_id=queue_id, reason=reason.strip()),
                context=_tui_request_context(),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self._notice(f"Closed {queue_id}.")
        self._load_queue()

    def _correct_run(self, args: str) -> None:
        run_id, verdict_value, reason = _parse_correct_args(args)
        if not run_id or not verdict_value or not reason:
            self._notice("Usage: /correct RUN-... verdict reason", tone="error")
            return
        try:
            verdict = Verdict(verdict_value)
        except ValueError:
            self._notice(f"Unknown verdict {verdict_value!r}.", tone="error")
            return
        try:
            self.service.correct(
                CorrectionCommand(run_id=run_id, corrected_verdict=verdict, reason=reason),
                context=_tui_request_context(),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self._notice(f"Corrected {run_id} -> {verdict.value}.")
        self._load_queue()

    def _record_disposition_outcome(self, args: str, *, sampled: bool) -> None:
        sample_id, proposal_id, disposition_value, idempotency_key, reason = _parse_outcome_args(
            args,
            sampled=sampled,
        )
        usage = "/sample-outcome DSAMPLE-... DPROP-... disposition idempotency-key reason" if sampled else "/outcome DPROP-... disposition idempotency-key reason"
        if not proposal_id or not disposition_value or not idempotency_key or not reason:
            self._notice(f"Usage: {usage}", tone="error")
            return
        if self.disposition_evaluation_service is None:
            self._notice("Disposition evaluation service is not configured.", tone="error")
            return
        try:
            observed_disposition = SocOperationalDisposition(disposition_value)
            result = self.disposition_evaluation_service.record_outcome(
                SocDispositionOutcomeCommand(
                    proposal_id=proposal_id,
                    observed_disposition=observed_disposition,
                    review_kind=(SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW if sampled else SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION),
                    source=SocDispositionOutcomeSource.ANALYST,
                    sample_id=sample_id,
                    reason=reason,
                    idempotency_key=idempotency_key,
                ),
                context=_tui_outcome_context(
                    actor_id=self.actor_id,
                    idempotency_key=idempotency_key,
                    sampled=sampled,
                ),
            )
        except (SocServiceError, ValueError) as exc:
            self._notice(str(exc), tone="error")
            return
        self._notice(f"Recorded {result.outcome.outcome_id} for {proposal_id} -> {observed_disposition.value}.")
        self._open_context(result.outcome.queue_id)

    def _notice(self, text: str, *, tone: str = "info") -> None:
        self.state = add_notice(self.state, text, tone="error" if tone == "error" else "info")
        self._refresh_all()

    def _refresh_all(self) -> None:
        self.query_one("#header", Static).update(render_header(database_label=self.database_label))
        self.query_one("#main", Static).update(render_main(self.state))
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(render_status(self.state))

    def check_action(self, action: str, parameters):  # noqa: D401 - Textual hook
        custom = {"nav_up", "nav_down", "palette_complete", "palette_accept", "escape"}
        if action in custom:
            if action == "palette_accept":
                return True if self._palette_open else None
            return True
        return True

    def action_nav_down(self) -> None:
        if self._palette_open and self._palette_items:
            self._palette_index = min(self._palette_index + 1, len(self._palette_items) - 1)
            self._render_palette()

    def action_nav_up(self) -> None:
        if self._palette_open and self._palette_items:
            self._palette_index = max(self._palette_index - 1, 0)
            self._render_palette()

    def action_palette_complete(self) -> None:
        if self._palette_open:
            self._fill_from_palette()

    def action_palette_accept(self) -> None:
        if self._palette_open:
            item = self._current_palette_item()
            if item is not None:
                self.query_one("#composer", Input).value = ""
                self._close_palette()
                self._handle_submit(f"/{item.name}")

    def action_escape(self) -> None:
        self._close_palette()

    def action_redraw(self) -> None:
        self._refresh_all()

    def _open_palette(self, items) -> None:
        if not items:
            self._close_palette()
            return
        self._palette_items = items
        self._palette_index = min(self._palette_index, len(items) - 1)
        self._palette_open = True
        self.query_one("#palette", Static).add_class("open")
        self._render_palette()

    def _close_palette(self) -> None:
        if not self._palette_open and not self._palette_items:
            return
        self._palette_open = False
        self._palette_items = []
        self._palette_index = 0
        self.query_one("#palette", Static).remove_class("open")

    def _render_palette(self) -> None:
        self.query_one("#palette", Static).update(render_palette(self._palette_items, self._palette_index))

    def _current_palette_item(self):
        if 0 <= self._palette_index < len(self._palette_items):
            return self._palette_items[self._palette_index]
        return None

    def _fill_from_palette(self) -> None:
        item = self._current_palette_item()
        if item is None:
            return
        composer = self.query_one("#composer", Input)
        composer.value = f"/{item.name} "
        composer.cursor_position = len(composer.value)
        self._close_palette()


def _parse_correct_args(args: str) -> tuple[str, str, str]:
    run_id, _, rest = args.strip().partition(" ")
    verdict, _, reason = rest.strip().partition(" ")
    return run_id.strip(), verdict.strip(), reason.strip()


def _parse_approved_action_args(args: str) -> tuple[str, str, str, str]:
    execution_token_id, _, rest = args.strip().partition(" ")
    route, _, rest = rest.strip().partition(" ")
    action, _, extra = rest.strip().partition(" ")
    return execution_token_id.strip(), route.strip(), action.strip(), extra.strip()


def _parse_normalization_update_args(args: str) -> tuple[str, str, str]:
    issue_id, _, rest = args.strip().partition(" ")
    status, _, reason = rest.strip().partition(" ")
    return issue_id.strip(), status.strip(), reason.strip()


def _parse_outcome_args(
    args: str,
    *,
    sampled: bool,
) -> tuple[str | None, str, str, str, str]:
    first, _, rest = args.strip().partition(" ")
    if sampled:
        sample_id = first.strip() or None
        proposal_id, _, rest = rest.strip().partition(" ")
    else:
        sample_id = None
        proposal_id = first
    disposition, _, rest = rest.strip().partition(" ")
    idempotency_key, _, reason = rest.strip().partition(" ")
    return (
        sample_id,
        proposal_id.strip(),
        disposition.strip(),
        idempotency_key.strip(),
        reason.strip(),
    )


def _tui_request_context(*, idempotency_key: str | None = None) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(actor_id="soc-review-tui", surface=EntrySurface.TUI),
        idempotency_key=idempotency_key,
    )


def _tui_outcome_context(
    *,
    actor_id: str,
    idempotency_key: str,
    sampled: bool,
) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id=actor_id,
            surface=EntrySurface.TUI,
            roles=["soc_quality_reviewer" if sampled else "soc_analyst"],
        ),
        idempotency_key=idempotency_key,
    )


def _tui_approval_context() -> ServiceRequestContext:
    return ServiceRequestContext(actor=ActorContext(actor_id="soc-review-tui", surface=EntrySurface.TUI, roles=["soc_approver"]))


def _tui_normalization_context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-review-tui",
            surface=EntrySurface.TUI,
            roles=["soc_engineer"],
        )
    )
