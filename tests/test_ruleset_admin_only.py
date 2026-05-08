import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import RuleSet, User, UserRole
from app.security import hash_password
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


def _create_user(client: TestClient, *, username: str, password: str, role: UserRole) -> None:
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
        )
        db.commit()


def test_ruleset_page_requires_login(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        resp = client.get("/rulesets", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)
        if resp.status_code == 302:
            assert resp.headers["location"] == "/login"


def test_ruleset_page_requires_admin(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        _create_user(client, username="u1", password="pw1", role=UserRole.user)
        client.post("/login", data={"username": "u1", "password": "pw1"})

        resp = client.get("/rulesets", follow_redirects=False)
        assert resp.status_code in (403, 302)


def test_admin_can_create_and_activate_ruleset(tmp_path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        client.post("/login", data={"username": "admin", "password": "admin"})

        resp = client.get("/rulesets")
        assert resp.status_code == 200

        definition = {
            "rules": [
                {
                    "name": "示例规则",
                    "severity": "warn",
                    "expr": "gt(col_a, 0)",
                    "message_template": "col_a 需要大于 0",
                }
            ]
        }
        resp = client.post(
            "/rulesets",
            data={
                "name": "RS1",
                "version": "v1",
                "definition_json": json.dumps(definition, ensure_ascii=False),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        session_factory = client.app.state.session_factory
        with session_factory() as db:
            ruleset = db.execute(select(RuleSet).where(RuleSet.name == "RS1")).scalar_one()

        resp = client.post(f"/rulesets/{ruleset.id}/activate", follow_redirects=False)
        assert resp.status_code == 302

        with session_factory() as db:
            active = (
                db.execute(select(RuleSet).where(RuleSet.is_active.is_(True)))
                .scalars()
                .all()
            )
            assert len(active) == 1
            assert active[0].id == ruleset.id

