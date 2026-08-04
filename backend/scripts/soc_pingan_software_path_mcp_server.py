"""Entrypoint for the PingAn historical software-path MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.software_path_mcp_server import main  # noqa: E402, I001


if __name__ == "__main__":
    main()
