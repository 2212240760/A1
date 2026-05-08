from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.models import CompareJob, Dataset, DatasetVersion, JobStatus, UserPreference, UserRole
from app.web.auth import get_current_user


router = APIRouter()


@router.get("/")
def dashboard_page(request: Request, dataset_id: int | None = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory

    with session_factory() as db:
        datasets = (
            db.execute(
                select(Dataset).where(Dataset.owner_user_id == user.id).order_by(Dataset.id.desc())
            )
            .scalars()
            .all()
        )

        dataset_id_set = {ds.id for ds in datasets}
        pref = db.get(UserPreference, user.id)

        selected_dataset_id = None
        if dataset_id and dataset_id in dataset_id_set:
            selected_dataset_id = dataset_id
        elif pref and pref.compare_dataset_id and pref.compare_dataset_id in dataset_id_set:
            selected_dataset_id = pref.compare_dataset_id
        elif datasets:
            selected_dataset_id = datasets[0].id

        versions_for_dataset: list[DatasetVersion] = []
        if selected_dataset_id:
            versions_for_dataset = (
                db.execute(
                    select(DatasetVersion)
                    .where(DatasetVersion.dataset_id == selected_dataset_id)
                    .order_by(DatasetVersion.id.desc())
                )
                .scalars()
                .all()
            )

        version_id_set = {v.id for v in versions_for_dataset}
        default_version_a_id = None
        default_version_b_id = None
        if pref and pref.compare_version_a_id and pref.compare_version_b_id:
            if pref.compare_version_a_id in version_id_set and pref.compare_version_b_id in version_id_set:
                default_version_a_id = pref.compare_version_a_id
                default_version_b_id = pref.compare_version_b_id

        if not default_version_a_id or not default_version_b_id:
            if len(versions_for_dataset) == 1:
                default_version_a_id = versions_for_dataset[0].id
                default_version_b_id = versions_for_dataset[0].id
            elif len(versions_for_dataset) >= 2:
                default_version_a_id = versions_for_dataset[0].id
                default_version_b_id = versions_for_dataset[1].id

        latest_success_job = None
        summary_pretty = None
        result_files = None
        if selected_dataset_id and default_version_a_id and default_version_b_id:
            latest_success_job = db.execute(
                select(CompareJob)
                .where(
                    CompareJob.owner_user_id == user.id,
                    CompareJob.dataset_id == selected_dataset_id,
                    CompareJob.version_a_id == default_version_a_id,
                    CompareJob.version_b_id == default_version_b_id,
                    CompareJob.status == JobStatus.succeeded,
                )
                .order_by(CompareJob.id.desc())
                .limit(1)
            ).scalar_one_or_none()

            if latest_success_job and latest_success_job.summary_json:
                summary = json.loads(latest_success_job.summary_json)
                summary_pretty = json.dumps(summary, ensure_ascii=False, indent=2)
            if latest_success_job and latest_success_job.result_files_json:
                result_files = json.loads(latest_success_job.result_files_json)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "datasets": datasets,
            "selected_dataset_id": selected_dataset_id,
            "default_version_a_id": default_version_a_id,
            "default_version_b_id": default_version_b_id,
            "latest_success_job": latest_success_job,
            "summary_pretty": summary_pretty,
            "result_files": result_files,
            "is_admin": user.role == UserRole.admin,
        },
    )

