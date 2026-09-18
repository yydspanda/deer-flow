"""Request-local configuration authority for the Host DEV corpus workbench."""

from ipaddress import ip_address

from fastapi import HTTPException, Request

from app.gateway.soc_dev_workbench import strict_env_bool
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions


def can_configure_corpus(request: Request) -> bool:
    if not strict_env_bool("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", False):
        return True
    # Host startup closes direct Gateway/Next LAN ingress and pins trusted proxy
    # peers. Uvicorn supplies the validated client; never trust Host or headers here.
    client = request.client
    try:
        return client is not None and ip_address(client.host).is_loopback
    except ValueError:
        return False


def require_saved_corpus_options(request: Request, submitted: SocAnalysisExecutionOptions, saved: SocAnalysisExecutionOptions) -> None:
    if not can_configure_corpus(request) and submitted != saved:
        raise HTTPException(status_code=403, detail="运行配置仅限部署本机修改；请刷新后沿用已保存配置运行。")
