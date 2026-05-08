import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import CompareJob, Dataset, DatasetVersion, UserPreference
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


def test_compare_updates_preference_and_outputs(tmp_path) -> None:
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
            assert len(versions) == 2
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
        assert resp.headers["location"].startswith("/compare/")
        job_id = int(resp.headers["location"].split("/")[-1])

        with session_factory() as db:
            pref = db.get(UserPreference, 1)
            assert pref is not None
            assert pref.compare_dataset_id == dataset.id
            assert pref.compare_version_a_id == v1.id
            assert pref.compare_version_b_id == v2.id

            job = db.get(CompareJob, job_id)
            assert job is not None
            assert job.status.value == "succeeded"
            assert job.summary_json
            assert job.result_files_json

            files = json.loads(job.result_files_json)

        for key in ("summary_json", "diff_xlsx", "report_html", "report_pdf"):
            path = Path(files[key])
            assert path.exists()
            assert path.is_file()

