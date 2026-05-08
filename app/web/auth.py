from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.models import User
from app.security import verify_password

SESSION_USER_ID_KEY = "user_id"

router = APIRouter()


def get_current_user(request: Request) -> User | None:
    user_id = request.session.get(SESSION_USER_ID_KEY)
    if not user_id:
        return None

    session_factory = request.app.state.session_factory
    with session_factory() as db:
        return db.get(User, user_id)


@router.get("/login")
def login_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    session_factory = request.app.state.session_factory
    templates = request.app.state.templates

    with session_factory() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "用户名或密码错误"},
            status_code=401,
        )

    request.session[SESSION_USER_ID_KEY] = user.id
    return RedirectResponse(url="/", status_code=302)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
