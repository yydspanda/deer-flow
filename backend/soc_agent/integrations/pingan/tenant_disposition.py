"""PingAn tenant disposition policy pack locator.

The generic Runtime never imports this module. Operators opt in by pointing
``SOC_TENANT_DISPOSITION_POLICY_PATH`` at the returned JSON file.
"""

from __future__ import annotations

from pathlib import Path

from soc_agent.contracts import TenantDispositionPolicy
from soc_agent.tenant_policy import load_tenant_disposition_policies

PINGAN_TENANT_DISPOSITION_POLICY_PATH = Path(__file__).resolve().parent / "policies" / "tenant-disposition-v2.json"
PINGAN_TENANT_DISPOSITION_SKILL_PATH = Path(__file__).resolve().parent / "policy_skills" / "disposition" / "SKILL.md"


def load_pingan_tenant_disposition_policy() -> TenantDispositionPolicy:
    policies = load_tenant_disposition_policies(PINGAN_TENANT_DISPOSITION_POLICY_PATH)
    if len(policies) != 1:
        raise ValueError("PingAn tenant disposition policy pack must contain exactly one policy")
    return policies[0]


__all__ = [
    "PINGAN_TENANT_DISPOSITION_POLICY_PATH",
    "PINGAN_TENANT_DISPOSITION_SKILL_PATH",
    "load_pingan_tenant_disposition_policy",
]
