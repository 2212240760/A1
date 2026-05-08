import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Dataset, DatasetVersion
from app.settings import Settings
from xlsx_fixtures import build_simple_xlsx_bytes


def make_app(tmp_path):
    db_path = tmp_path / "test.db"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        data_dir=str(tmp_path),
        session_secret="test-session-secret",
        default_admin_username="admin",
        default_admin_password="admin",
    )
    return create_app(settings)


def test_upload_requires_login(tmp_path) -> None:
    xlsx_bytes = build_simple_xlsx_bytes(headers=["id", "value"], rows=[["1", "a"]])
    with TestClient(make_app(tmp_path)) as client:
        resp = client.post(
            "/datasets/upload",
            files={
                "file": (
                    "demo.xlsx",
                    xlsx_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"dataset_name": "Demo", "version_label": "v1"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 401, 403)


def test_upload_creates_dataset_version_after_login(tmp_path) -> None:
    xlsx_bytes = build_simple_xlsx_bytes(
        headers=["id", "value"],
        rows=[["1", "a"], ["2", "b"]],
    )
    with TestClient(make_app(tmp_path)) as client:
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        resp = client.post(
            "/datasets/upload",
            files={
                "file": (
                    "demo.xlsx",
                    xlsx_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"dataset_name": "Demo", "version_label": "v1"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/datasets/")

        session_factory = client.app.state.session_factory
        with session_factory() as db:
            dataset = db.execute(select(Dataset).where(Dataset.name == "Demo")).scalar_one()
            version = (
                db.execute(
                    select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
                )
                .scalars()
                .one()
            )

        assert version.version_label == "v1"
        assert version.sheet_name == "Sheet1"
        assert version.row_count == 2

        schema = json.loads(version.schema_json)
        assert [c["name"] for c in schema] == ["id", "value"]

        path = Path(version.source_file_path)
        assert path.exists()
        assert path.name == "source.xlsx"
