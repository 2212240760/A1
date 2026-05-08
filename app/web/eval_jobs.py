from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from app.models import Dataset, DatasetVersion, EvalJob
from app.services.eval import create_and_run_eval_job
from app.web.auth import get_current_user


router = APIRouter()


@router.get("/eval-jobs")
def eval_jobs_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        jobs = (
            db.execute(
                select(EvalJob)
                .where(EvalJob.owner_user_id == user.id)
                .order_by(EvalJob.id.desc())
            )
            .scalars()
            .all()
        )
        versions = (
            db.execute(
                select(DatasetVersion, Dataset)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .where(Dataset.owner_user_id == user.id)
                .order_by(DatasetVersion.id.desc())
            )
            .all()
        )

    return templates.TemplateResponse(
        request,
        "eval_jobs.html",
        {
            "user": user,
            "jobs": jobs,
            "versions": versions,
            "selected_job": None,
            "summary_pretty": None,
            "result_files": None,
        },
    )


@router.post("/eval-jobs")
def eval_jobs_create(request: Request, dataset_version_id: int = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        job = create_and_run_eval_job(
            db,
            data_dir=settings.data_dir,
            owner_user_id=user.id,
            dataset_version_id=dataset_version_id,
        )
        db.commit()

    return RedirectResponse(url=f"/eval-jobs/{job.id}", status_code=302)


@router.get("/eval-jobs/{job_id}")
def eval_job_detail(request: Request, job_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        selected_job = db.get(EvalJob, job_id)
        if not selected_job or selected_job.owner_user_id != user.id:
            return RedirectResponse(url="/eval-jobs", status_code=302)

        jobs = (
            db.execute(
                select(EvalJob)
                .where(EvalJob.owner_user_id == user.id)
                .order_by(EvalJob.id.desc())
            )
            .scalars()
            .all()
        )
        versions = (
            db.execute(
                select(DatasetVersion, Dataset)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .where(Dataset.owner_user_id == user.id)
                .order_by(DatasetVersion.id.desc())
            )
            .all()
        )

        summary = None
        summary_pretty = None
        result_files = None
        if selected_job.summary_json:
            summary = json.loads(selected_job.summary_json)
            summary_pretty = json.dumps(summary, ensure_ascii=False, indent=2)
        if selected_job.result_files_json:
            result_files = json.loads(selected_job.result_files_json)

    return templates.TemplateResponse(
        request,
        "eval_jobs.html",
        {
            "user": user,
            "jobs": jobs,
            "versions": versions,
            "selected_job": selected_job,
            "summary": summary,
            "summary_pretty": summary_pretty,
            "result_files": result_files,
        },
    )


@router.get("/eval-jobs/{job_id}/download/{file_key}")
def eval_job_download(request: Request, job_id: int, file_key: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session_factory = request.app.state.session_factory
    with session_factory() as db:
        job = db.get(EvalJob, job_id)
        if not job or job.owner_user_id != user.id:
            return RedirectResponse(url="/eval-jobs", status_code=302)
        if not job.result_files_json:
            return RedirectResponse(url=f"/eval-jobs/{job_id}", status_code=302)

        files = json.loads(job.result_files_json)
        path = files.get(file_key)
        if not path:
            return RedirectResponse(url=f"/eval-jobs/{job_id}", status_code=302)

    return FileResponse(path=path, filename=path.split("/")[-1])
