import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Dataset, DatasetVersion, EvalJob
from app.services.rulesets import activate_ruleset, create_ruleset
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


def test_eval_job_lifecycle(tmp_path) -> None:
    xlsx_bytes = build_simple_xlsx_bytes(
        headers=["id", "score"],
        rows=[["1", "10"], ["2", "0"]],
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

            definition_json = json.dumps(
                {
                    "rules": [
                        {
                            "name": "score 非空",
                            "severity": "error",
                            "expr": "not_empty(score)",
                            "message_template": "{col} 不能为空",
                        },
                        {
                            "name": "score 大于 5",
                            "severity": "warn",
                            "expr": "gt(score, 5)",
                            "message_template": "{col} 必须 > {threshold}",
                        },
                    ]
                },
                ensure_ascii=False,
            )

            rs = create_ruleset(
                db,
                name="Demo Rules",
                version="v1",
                definition_json=definition_json,
                created_by=1,
            )
            activate_ruleset(db, ruleset_id=rs.id)
            db.commit()

        resp = client.post(
            "/eval-jobs",
            data={"dataset_version_id": str(version.id)},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/eval-jobs/")

        job_id = int(resp.headers["location"].split("/")[-1])

        with session_factory() as db:
            job = db.get(EvalJob, job_id)
            assert job is not None
            assert job.status.value == "succeeded"
            assert job.summary_json
            assert job.result_files_json

            files = json.loads(job.result_files_json)

        for key in ("summary_json", "details_xlsx", "report_html", "report_pdf"):
            path = Path(files[key])
            assert path.exists()
            assert path.is_file()

