from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from app.models import CompareJob, Dataset, DatasetVersion, UserPreference
from app.services.compare import create_and_run_compare_job
from app.web.auth import get_current_user


router = APIRouter()


@router.get("/compare")
def compare_page(request: Request):
    return _render_compare_page(request, selected_job_id=None)


@router.post("/compare")
def compare_create(
    request: Request,
    dataset_id: int = Form(...),
    version_a_id: int = Form(...),
    version_b_id: int = Form(...),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        job = create_and_run_compare_job(
            db,
            data_dir=settings.data_dir,
            owner_user_id=user.id,
            dataset_id=dataset_id,
            version_a_id=version_a_id,
            version_b_id=version_b_id,
        )

        pref = db.get(UserPreference, user.id)
        if not pref:
            pref = UserPreference(user_id=user.id)
            db.add(pref)
        pref.compare_dataset_id = dataset_id
        pref.compare_version_a_id = version_a_id
        pref.compare_version_b_id = version_b_id
        db.commit()

    return RedirectResponse(url=f"/compare/{job.id}", status_code=302)


@router.get("/compare/{job_id}")
def compare_detail(request: Request, job_id: int):
    return _render_compare_page(request, selected_job_id=job_id)


@router.get("/compare/{job_id}/download/{file_key}")
def compare_download(request: Request, job_id: int, file_key: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    session_factory = request.app.state.session_factory
    with session_factory() as db:
        job = db.get(CompareJob, job_id)
        if not job or job.owner_user_id != user.id:
            return RedirectResponse(url="/compare", status_code=302)
        if not job.result_files_json:
            return RedirectResponse(url=f"/compare/{job_id}", status_code=302)

        files = json.loads(job.result_files_json)
        path = files.get(file_key)
        if not path:
            return RedirectResponse(url=f"/compare/{job_id}", status_code=302)

    return FileResponse(path=path, filename=path.split("/")[-1])


def _render_compare_page(request: Request, *, selected_job_id: int | None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory

    with session_factory() as db:
        jobs = (
            db.execute(
                select(CompareJob)
                .where(CompareJob.owner_user_id == user.id)
                .order_by(CompareJob.id.desc())
            )
            .scalars()
            .all()
        )
        datasets = (
            db.execute(select(Dataset).where(Dataset.owner_user_id == user.id).order_by(Dataset.id.desc()))
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

        pref = db.get(UserPreference, user.id)
        default_dataset_id, default_a_id, default_b_id = _pick_defaults(
            datasets=datasets,
            versions=versions,
            pref=pref,
        )

        selected_job = None
        summary = None
        summary_pretty = None
        result_files = None
        if selected_job_id is not None:
            selected_job = db.get(CompareJob, selected_job_id)
            if not selected_job or selected_job.owner_user_id != user.id:
                return RedirectResponse(url="/compare", status_code=302)
            if selected_job.summary_json:
                summary = json.loads(selected_job.summary_json)
                summary_pretty = json.dumps(summary, ensure_ascii=False, indent=2)
            if selected_job.result_files_json:
                result_files = json.loads(selected_job.result_files_json)

    return templates.TemplateResponse(
        request,
        "compare.html",
        {
            "user": user,
            "jobs": jobs,
            "datasets": datasets,
            "versions": versions,
            "default_dataset_id": default_dataset_id,
            "default_version_a_id": default_a_id,
            "default_version_b_id": default_b_id,
            "selected_job": selected_job,
            "summary": summary,
            "summary_pretty": summary_pretty,
            "result_files": result_files,
        },
    )


def _pick_defaults(
    *,
    datasets: list[Dataset],
    versions: list[tuple[DatasetVersion, Dataset]],
    pref: UserPreference | None,
) -> tuple[int | None, int | None, int | None]:
    if pref and pref.compare_dataset_id and pref.compare_version_a_id and pref.compare_version_b_id:
        return pref.compare_dataset_id, pref.compare_version_a_id, pref.compare_version_b_id

    if not datasets:
        return None, None, None

    dataset_id = datasets[0].id
    candidates = [v for v, ds in versions if ds.id == dataset_id]
    if not candidates:
        return dataset_id, None, None

    candidates_sorted = sorted(candidates, key=lambda v: v.id, reverse=True)
    if len(candidates_sorted) == 1:
        return dataset_id, candidates_sorted[0].id, candidates_sorted[0].id
    return dataset_id, candidates_sorted[0].id, candidates_sorted[1].id

