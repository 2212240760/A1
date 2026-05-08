from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Dataset, DatasetVersion, Report
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


def test_reports_list_and_download_after_compare(tmp_path) -> None:
    xlsx_v1 = build_simple_xlsx_bytes(
        headers=["id", "score", "name"],
        rows=[["1", "10", "a"], ["2", "20", "b"]],
    )
    xlsx_v2 = build_simple_xlsx_bytes(
        headers=["id", "score", "name"],
        rows=[["1", "11", "a"], ["3", "30", "c"]],
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
                    xlsx_v1,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"dataset_name": "Demo", "version_label": "v1"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        resp = client.post(
            "/datasets/upload",
            files={
                "file": (
                    "demo.xlsx",
                    xlsx_v2,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"dataset_name": "Demo", "version_label": "v2"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        session_factory = client.app.state.session_factory
        with session_factory() as db:
            dataset = db.execute(select(Dataset).where(Dataset.name == "Demo")).scalar_one()
            versions = (
                db.execute(
                    select(DatasetVersion)
                    .where(DatasetVersion.dataset_id == dataset.id)
                    .order_by(DatasetVersion.id.asc())
                )
                .scalars()
                .all()
            )
            v1, v2 = versions

        resp = client.post(
            "/compare",
            data={
                "dataset_id": str(dataset.id),
                "version_a_id": str(v1.id),
                "version_b_id": str(v2.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with session_factory() as db:
            report = (
                db.execute(select(Report).order_by(Report.id.desc()))
                .scalars()
                .first()
            )
            assert report is not None

        resp = client.get("/reports")
        assert resp.status_code == 200
        assert report.title in resp.text

        for kind in ("excel", "pdf"):
            resp = client.get(f"/reports/{report.id}/download", params={"kind": kind})
            assert resp.status_code == 200
            assert resp.content

