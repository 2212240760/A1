from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.models import Report
from app.services.reports import list_reports, resolve_download_path
from app.web.auth import get_current_user


router = APIRouter()


@router.get("/reports")
def reports_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        reports = list_reports(db, created_by=user.id)
        rows: list[dict[str, object]] = []
        for r in reports:
            files = {}
            if r.export_files_json:
                parsed = json.loads(r.export_files_json)
                if isinstance(parsed, dict):
                    files = parsed
            rows.append({"report": r, "files": files})

    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "user": user,
            "rows": rows,
        },
    )


@router.get("/reports/{report_id}/download")
def report_download(request: Request, report_id: int, kind: str = Query(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session_factory = request.app.state.session_factory
    with session_factory() as db:
        report = db.get(Report, report_id)
        if not report or report.created_by != user.id:
            return RedirectResponse(url="/reports", status_code=302)

        try:
            path, filename = resolve_download_path(report, kind=kind)
        except ValueError:
            return RedirectResponse(url="/reports", status_code=302)

    return FileResponse(path=path, filename=filename)

