from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models import UserRole
from app.services.rulesets import activate_ruleset, create_ruleset, list_rulesets
from app.web.auth import get_current_user

router = APIRouter()


def _require_admin(request: Request):
    user = get_current_user(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=302)
    if user.role != UserRole.admin:
        return user, HTMLResponse("Forbidden", status_code=403)
    return user, None


@router.get("/rulesets")
def rulesets_page(request: Request):
    user, denial = _require_admin(request)
    if denial:
        return denial

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        rulesets = list_rulesets(db)

    default_definition = json.dumps(
        {
            "rules": [
                {
                    "name": "非空检查",
                    "severity": "error",
                    "expr": "not_empty(value)",
                    "message_template": "value 不能为空",
                }
            ]
        },
        ensure_ascii=False,
        indent=2,
    )

    return templates.TemplateResponse(
        request,
        "rulesets.html",
        {
            "user": user,
            "rulesets": rulesets,
            "error": None,
            "default_definition": default_definition,
        },
    )


@router.post("/rulesets")
def rulesets_create(
    request: Request,
    name: str = Form(...),
    version: str = Form("v1"),
    definition_json: str = Form(...),
):
    user, denial = _require_admin(request)
    if denial:
        return denial

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        try:
            create_ruleset(
                db,
                name=name,
                version=version,
                definition_json=definition_json,
                created_by=user.id,
            )
            db.commit()
            return RedirectResponse(url="/rulesets", status_code=302)
        except ValueError as e:
            db.rollback()
            rulesets = list_rulesets(db)
            return templates.TemplateResponse(
                request,
                "rulesets.html",
                {
                    "user": user,
                    "rulesets": rulesets,
                    "error": str(e),
                    "default_definition": definition_json,
                },
                status_code=400,
            )


@router.post("/rulesets/{ruleset_id}/activate")
def rulesets_activate(request: Request, ruleset_id: int):
    user, denial = _require_admin(request)
    if denial:
        return denial

    session_factory = request.app.state.session_factory
    with session_factory() as db:
        activate_ruleset(db, ruleset_id=ruleset_id)
        db.commit()

    return RedirectResponse(url="/rulesets", status_code=302)

