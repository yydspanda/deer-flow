from sqlalchemy import create_engine, inspect, text

from soc_agent.db.migration_runner import upgrade_soc_schema


def test_experiment_migration_upgrades_existing_database_without_losing_history(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    upgrade_soc_schema(url, revision="0028_corpus_list")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE operator_owned_probe (value TEXT)"))
        connection.execute(text("INSERT INTO operator_owned_probe VALUES ('keep')"))
    upgrade_soc_schema(url)
    upgrade_soc_schema(url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM soc_alembic_version")) == "0031_memory_working_drafts"
        assert connection.scalar(text("SELECT value FROM operator_owned_probe")) == "keep"
        tables = inspect(connection).get_table_names()
        assert all(t in tables for t in ("soc_corpus_experiments", "soc_corpus_experiment_members", "soc_corpus_rounds", "soc_corpus_round_items"))
        assert "concurrency_key" in {c["name"] for c in inspect(connection).get_columns("soc_processing_jobs")}
        assert any(i["name"] == "uq_soc_processing_jobs_active_scope" and i["unique"] for i in inspect(connection).get_indexes("soc_processing_jobs"))
    engine.dispose()
