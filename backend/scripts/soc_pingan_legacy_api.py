"""Run the old ZEUS task API on top of durable SOC processing jobs."""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.pingan_compat import create_pingan_compat_app  # noqa: E402
from soc_agent.db import (  # noqa: E402
    SqlAlchemyProcessingJobRepository,
    to_sync_database_url,
    upgrade_soc_schema,
)
from soc_agent.integrations.pingan.legacy_compat import (  # noqa: E402
    PingAnLegacyTaskService,
)
from soc_agent.integrations.pingan.legacy_compat.wiring import (  # noqa: E402
    PingAnLegacyApiSettings,
)


def main() -> None:
    settings = PingAnLegacyApiSettings.from_env()
    if settings.auto_migrate:
        upgrade_soc_schema(settings.database_url)
    engine = create_engine(
        to_sync_database_url(settings.database_url),
        pool_pre_ping=True,
    )
    try:
        repository = SqlAlchemyProcessingJobRepository(sessionmaker(bind=engine, expire_on_commit=False))
        app = create_pingan_compat_app(
            service=PingAnLegacyTaskService(
                repository=repository,
                queue_ttl_seconds=settings.queue_ttl_seconds,
            ),
            app_keys=settings.app_keys,
            max_request_bytes=settings.max_request_bytes,
        )
        uvicorn.run(
            app,
            host=settings.bind_host,
            port=settings.port,
            workers=1,
            access_log=False,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
