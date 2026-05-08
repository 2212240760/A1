from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app.db import create_all, get_engine, get_session_factory
from app.models import User, UserRole
from app.security import hash_password
from app.settings import Settings
from app.web.auth import get_current_user, router as auth_router
from app.web.datasets import router as datasets_router
from app.web.rulesets import router as rulesets_router

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=False,
    )

    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    app.state.settings = settings
    app.state.templates = templates
    app.state.engine = engine
    app.state.session_factory = session_factory

    @app.on_event("startup")
    def _startup() -> None:
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        create_all(engine)

        with session_factory() as db:
            existing = db.execute(
                select(User).where(User.username == settings.default_admin_username)
            ).scalar_one_or_none()
            if existing:
                return

            db.add(
                User(
                    username=settings.default_admin_username,
                    password_hash=hash_password(settings.default_admin_password),
                    role=UserRole.admin,
                )
            )
            db.commit()

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        user = get_current_user(request)
        if not user:
            return RedirectResponse(url="/login", status_code=302)

        admin_link = ""
        if user.role == UserRole.admin:
            admin_link = "<p><a href='/rulesets'>规则集（管理员）</a></p>"

        return HTMLResponse(
            f"<h1>Grid Eval Web</h1>"
            f"<p>你好，{user.username}</p>"
            f"<p><a href='/datasets'>数据集</a></p>"
            f"{admin_link}"
            f"<form method='post' action='/logout'><button type='submit'>退出登录</button></form>"
        )

    app.include_router(auth_router)
    app.include_router(datasets_router)
    app.include_router(rulesets_router)

    return app


app = create_app()
