from app.db import create_all, get_engine
from app.models import User


def test_create_tables(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    engine = get_engine(f"sqlite:///{db_path}")
    create_all(engine)

    with engine.connect() as conn:
        table_names = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "users" in table_names
        assert "datasets" in table_names
        assert "dataset_versions" in table_names
        assert "ingest_jobs" in table_names
        assert "rulesets" in table_names

    assert User.__tablename__ == "users"
