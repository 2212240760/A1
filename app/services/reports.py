import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Report, ReportType


def create_report(
    db: Session,
    *,
    dataset_id: int,
    type: ReportType,
    ref_job_id: int,
    title: str,
    export_files: dict[str, str],
    created_by: int,
) -> Report:
    report = Report(
        dataset_id=dataset_id,
        type=type,
        ref_job_id=ref_job_id,
        title=title,
        export_files_json=json.dumps(export_files, ensure_ascii=False),
        created_at=datetime.utcnow(),
        created_by=created_by,
    )
    db.add(report)
    db.flush()
    return report


def list_reports(db: Session, *, created_by: int) -> list[Report]:
    return (
        db.execute(
            select(Report).where(Report.created_by == created_by).order_by(Report.id.desc())
        )
        .scalars()
        .all()
    )


def resolve_download_path(report: Report, *, kind: str) -> tuple[str, str]:
    kind = (kind or "").strip().lower()
    if kind not in {"excel", "pdf"}:
        raise ValueError("kind 必须为 excel 或 pdf")

    files = json.loads(report.export_files_json or "{}")
    if not isinstance(files, dict):
        raise ValueError("export_files_json 非法")

    path = files.get(kind)
    if not path:
        raise ValueError("未找到对应导出文件")

    filename = path.split("/")[-1] or f"report-{report.id}.{ 'xlsx' if kind == 'excel' else 'pdf' }"
    return str(path), filename

