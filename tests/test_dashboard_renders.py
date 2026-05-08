from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


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


def test_dashboard_redirects_when_not_logged_in(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


def test_dashboard_renders_when_logged_in(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.post("/login", data={"username": "admin", "password": "admin"})

        resp = client.get("/")
        assert resp.status_code == 200
        assert "对比驱动总览" in resp.text
        assert "/datasets" in resp.text
        assert "/eval-jobs" in resp.text
        assert "/compare" in resp.text

