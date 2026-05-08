from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.models import Dataset, DatasetVersion, IngestJob, JobStatus
from app.services.ingest import ingest_excel
from app.web.auth import get_current_user


router = APIRouter()


@router.get("/datasets")
def datasets_page(request: Request):
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

    return templates.TemplateResponse(
        request,
        "datasets.html",
        {"datasets": datasets, "user": user},
    )


@router.post("/datasets/upload")
async def datasets_upload(
    request: Request,
    file: UploadFile = File(...),
    dataset_name: str = Form(...),
    version_label: str = Form("v1"),
    description: str | None = Form(None),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    file_bytes = await file.read()

    settings = request.app.state.settings
    session_factory = request.app.state.session_factory

    with session_factory() as db:
        dataset = db.execute(
            select(Dataset).where(
                Dataset.owner_user_id == user.id,
                Dataset.name == dataset_name,
            )
        ).scalar_one_or_none()
        if not dataset:
            dataset = Dataset(
                owner_user_id=user.id,
                name=dataset_name,
                description=description,
            )
            db.add(dataset)
            db.flush()

        version = DatasetVersion(
            dataset_id=dataset.id,
            version_label=version_label,
            source_file_path="",
            sheet_name="",
            schema_json="[]",
            row_count=0,
            created_by=user.id,
        )
        db.add(version)
        db.flush()

        job = IngestJob(
            dataset_version_id=version.id,
            status=JobStatus.running,
            started_at=datetime.utcnow(),
        )
        db.add(job)
        db.flush()

        try:
            result = ingest_excel(
                data_dir=settings.data_dir,
                dataset_id=dataset.id,
                version_id=version.id,
                file_bytes=file_bytes,
            )
            version.source_file_path = result.source_file_path
            version.sheet_name = result.sheet_name
            version.schema_json = result.schema_json
            version.row_count = result.row_count
            job.status = JobStatus.succeeded
            job.finished_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            job.status = JobStatus.failed
            job.error_message = str(e)
            job.finished_at = datetime.utcnow()
            db.commit()
            templates = request.app.state.templates
            dataset = db.get(Dataset, dataset.id)
            versions = (
                db.execute(
                    select(DatasetVersion)
                    .where(DatasetVersion.dataset_id == dataset.id)
                    .order_by(DatasetVersion.id.desc())
                )
                .scalars()
                .all()
            )
            return templates.TemplateResponse(
                request,
                "dataset_detail.html",
                {"dataset": dataset, "versions": versions, "user": user, "error": job.error_message},
                status_code=400,
            )

    return RedirectResponse(url=f"/datasets/{dataset.id}", status_code=302)


@router.get("/datasets/{dataset_id}")
def dataset_detail(request: Request, dataset_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    templates = request.app.state.templates
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        dataset = db.get(Dataset, dataset_id)
        if not dataset or dataset.owner_user_id != user.id:
            return RedirectResponse(url="/datasets", status_code=302)

        versions = (
            db.execute(
                select(DatasetVersion)
                .where(DatasetVersion.dataset_id == dataset.id)
                .order_by(DatasetVersion.id.desc())
            )
            .scalars()
            .all()
        )

    return templates.TemplateResponse(
        request,
        "dataset_detail.html",
        {"dataset": dataset, "versions": versions, "user": user, "error": None},
    )
