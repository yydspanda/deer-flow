"""Opt-in contracts for a separately running Trace Agent; no startup side effects."""

from .client import TraceClient, TraceClientError, TraceClientReply
from .config import TraceClientSettings
from .handoff import TraceHandoffError, TraceHandoffMaterial, TraceQuestionSelection, TraceTaskBinding, build_trace_handoff

__all__ = ["TraceClient", "TraceClientError", "TraceClientReply", "TraceClientSettings", "TraceHandoffError", "TraceHandoffMaterial", "TraceQuestionSelection", "TraceTaskBinding", "build_trace_handoff"]
