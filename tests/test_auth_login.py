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


def test_login_page(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "登录" in resp.text


def test_login_rejects_invalid_credentials(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        resp = client.post("/login", data={"username": "u", "password": "bad"})
        assert resp.status_code in (200, 401)


def test_login_success_and_protect_root(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

        resp = client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

        resp = client.get("/")
        assert resp.status_code == 200
        assert "admin" in resp.text


def test_logout_clears_session(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.post("/login", data={"username": "admin", "password": "admin"})

        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
