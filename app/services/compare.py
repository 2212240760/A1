import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import CompareJob, Dataset, DatasetVersion, JobStatus, ReportType
from app.services.eval import _build_xlsx_bytes, _html_to_pdf_bytes
from app.services.ingest import iter_sheet_rows, parse_xlsx
from app.services.reports import create_report


@dataclass(frozen=True)
class CompareOutputPaths:
    summary_json: str
    diff_xlsx: str
    report_html: str
    report_pdf: str


def create_and_run_compare_job(
    db: Session,
    *,
    data_dir: str,
    owner_user_id: int,
    dataset_id: int,
    version_a_id: int,
    version_b_id: int,
) -> CompareJob:
    dataset = db.get(Dataset, dataset_id)
    if not dataset or dataset.owner_user_id != owner_user_id:
        raise ValueError("无权访问该数据集")

    version_a = db.get(DatasetVersion, version_a_id)
    version_b = db.get(DatasetVersion, version_b_id)
    if not version_a or not version_b:
        raise ValueError("未找到数据集版本")
    if version_a.dataset_id != dataset_id or version_b.dataset_id != dataset_id:
        raise ValueError("版本不属于指定数据集")

    job = CompareJob(
        owner_user_id=owner_user_id,
        dataset_id=dataset_id,
        version_a_id=version_a_id,
        version_b_id=version_b_id,
        status=JobStatus.running,
        started_at=datetime.utcnow(),
    )
    db.add(job)
    db.flush()

    _run_compare_job(
        db,
        data_dir=data_dir,
        job=job,
        dataset=dataset,
        version_a=version_a,
        version_b=version_b,
    )
    return job


def _run_compare_job(
    db: Session,
    *,
    data_dir: str,
    job: CompareJob,
    dataset: Dataset,
    version_a: DatasetVersion,
    version_b: DatasetVersion,
) -> None:
    out_dir = Path(data_dir) / "jobs" / "compare" / str(job.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        file_a = Path(version_a.source_file_path).read_bytes()
        sheet_a, schema_a, _ = parse_xlsx(file_bytes=file_a, sheet_name=version_a.sheet_name)
        headers_a = [c["name"] for c in schema_a]
        rows_a = _load_rows(file_bytes=file_a, sheet_name=sheet_a, headers=headers_a)

        file_b = Path(version_b.source_file_path).read_bytes()
        sheet_b, schema_b, _ = parse_xlsx(file_bytes=file_b, sheet_name=version_b.sheet_name)
        headers_b = [c["name"] for c in schema_b]
        rows_b = _load_rows(file_bytes=file_b, sheet_name=sheet_b, headers=headers_b)

        pk_column = "id" if "id" in headers_a and "id" in headers_b else None
        pk_strategy = "id" if pk_column else "row_index"

        indexed_a = _index_rows(rows_a, pk_column=pk_column)
        indexed_b = _index_rows(rows_b, pk_column=pk_column)

        keys = set(indexed_a.keys()) | set(indexed_b.keys())
        all_columns = sorted(set(headers_a) | set(headers_b))

        details: list[dict[str, str]] = []
        counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
        column_change_counts: dict[str, int] = {}
        top_changed_keys: list[dict[str, object]] = []

        for key in sorted(keys, key=lambda x: str(x)):
            row_a = indexed_a.get(key)
            row_b = indexed_b.get(key)
            if row_a is None and row_b is None:
                continue
            if row_a is None:
                counts["added"] += 1
                for col in all_columns:
                    b_val = str(row_b.get(col) or "") if row_b else ""
                    if b_val:
                        details.append(
                            {
                                "key": str(key),
                                "change_type": "added",
                                "column": col,
                                "a_value": "",
                                "b_value": b_val,
                            }
                        )
                continue
            if row_b is None:
                counts["removed"] += 1
                for col in all_columns:
                    a_val = str(row_a.get(col) or "") if row_a else ""
                    if a_val:
                        details.append(
                            {
                                "key": str(key),
                                "change_type": "removed",
                                "column": col,
                                "a_value": a_val,
                                "b_value": "",
                            }
                        )
                continue

            changed_columns = []
            for col in all_columns:
                a_val = str(row_a.get(col) or "")
                b_val = str(row_b.get(col) or "")
                if a_val != b_val:
                    changed_columns.append(col)
                    details.append(
                        {
                            "key": str(key),
                            "change_type": "changed",
                            "column": col,
                            "a_value": a_val,
                            "b_value": b_val,
                        }
                    )
                    column_change_counts[col] = column_change_counts.get(col, 0) + 1

            if changed_columns:
                counts["changed"] += 1
                if len(top_changed_keys) < 10:
                    top_changed_keys.append({"key": str(key), "columns": changed_columns[:10]})
            else:
                counts["unchanged"] += 1

        top_columns = sorted(
            [{"column": k, "change_count": v} for k, v in column_change_counts.items()],
            key=lambda x: x["change_count"],
            reverse=True,
        )[:10]

        summary = {
            "job_id": job.id,
            "dataset_id": dataset.id,
            "dataset_name": dataset.name,
            "version_a_id": version_a.id,
            "version_b_id": version_b.id,
            "pk_strategy": pk_strategy,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "counts": counts,
            "top_changed_keys": top_changed_keys,
            "top_changed_columns": top_columns,
        }

        summary_path = out_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        diff_xlsx_path = out_dir / "diff.xlsx"
        diff_xlsx_path.write_bytes(_build_diff_xlsx(summary=summary, details=details))

        report_html_path = out_dir / "report.html"
        report_html = _build_report_html(summary=summary, details=details)
        report_html_path.write_text(report_html, encoding="utf-8")

        report_pdf_path = out_dir / "report.pdf"
        report_pdf_path.write_bytes(_html_to_pdf_bytes(report_html))

        result_files = CompareOutputPaths(
            summary_json=str(summary_path),
            diff_xlsx=str(diff_xlsx_path),
            report_html=str(report_html_path),
            report_pdf=str(report_pdf_path),
        )

        job.summary_json = json.dumps(summary, ensure_ascii=False)
        job.result_files_json = json.dumps(result_files.__dict__, ensure_ascii=False)
        job.status = JobStatus.succeeded
        finished_at = datetime.utcnow()
        job.finished_at = finished_at

        title_time = finished_at.strftime("%Y%m%d-%H%M%S")
        create_report(
            db,
            dataset_id=dataset.id,
            type=ReportType.compare,
            ref_job_id=job.id,
            title=f"{dataset.name}-{version_a.version_label}_vs_{version_b.version_label}-{title_time}",
            export_files={"excel": str(diff_xlsx_path), "pdf": str(report_pdf_path)},
            created_by=job.owner_user_id,
        )
        db.flush()
    except Exception as e:
        job.status = JobStatus.failed
        job.error_message = str(e)
        job.finished_at = datetime.utcnow()
        db.flush()


def _load_rows(
    *,
    file_bytes: bytes,
    sheet_name: str,
    headers: list[str],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in _iter_data_rows(file_bytes=file_bytes, sheet_name=sheet_name, header_len=len(headers)):
        row_map: dict[str, str] = {}
        for i, h in enumerate(headers):
            row_map[h] = str(row[i] if i < len(row) else "")
        out.append(row_map)
    return out


def _iter_data_rows(
    *,
    file_bytes: bytes,
    sheet_name: str,
    header_len: int,
) -> Iterable[list[str]]:
    it = iter_sheet_rows(file_bytes=file_bytes, sheet_name=sheet_name)
    _ = next(iter(it), None)
    for row in it:
        normalized = list(row[:header_len])
        if len(normalized) < header_len:
            normalized.extend([""] * (header_len - len(normalized)))
        yield normalized


def _index_rows(rows: list[dict[str, str]], *, pk_column: str | None) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for idx, row in enumerate(rows, start=1):
        if pk_column:
            key = str(row.get(pk_column) or "")
            if not key:
                key = f"ROW_{idx}"
        else:
            key = str(idx)
        indexed[key] = row
    return indexed


def _build_diff_xlsx(*, summary: dict[str, object], details: list[dict[str, str]]) -> bytes:
    summary_rows = [["key", "value"]]
    for k, v in summary.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        summary_rows.append([str(k), str(v)])

    details_rows = [["key", "change_type", "column", "a_value", "b_value"]]
    for d in details:
        details_rows.append(
            [
                str(d.get("key") or ""),
                str(d.get("change_type") or ""),
                str(d.get("column") or ""),
                str(d.get("a_value") or ""),
                str(d.get("b_value") or ""),
            ]
        )

    return _build_xlsx_bytes(
        sheets=[
            ("Summary", summary_rows),
            ("Details", details_rows),
        ]
    )


def _build_report_html(*, summary: dict[str, object], details: list[dict[str, str]]) -> str:
    summary_json = _html_escape(json.dumps(summary, ensure_ascii=False, indent=2))
    rows = []
    for d in details[:200]:
        rows.append(
            "<tr>"
            f"<td>{_html_escape(str(d.get('key') or ''))}</td>"
            f"<td>{_html_escape(str(d.get('change_type') or ''))}</td>"
            f"<td>{_html_escape(str(d.get('column') or ''))}</td>"
            f"<td>{_html_escape(str(d.get('a_value') or ''))}</td>"
            f"<td>{_html_escape(str(d.get('b_value') or ''))}</td>"
            "</tr>"
        )

    return (
        "<!doctype html>"
        "<html lang='zh-CN'>"
        "<head><meta charset='utf-8' /><title>对比报告</title></head>"
        "<body>"
        "<h1>对比报告</h1>"
        "<h2>汇总</h2>"
        f"<pre>{summary_json}</pre>"
        "<h2>差异明细（最多展示 200 条）</h2>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>Key</th><th>类型</th><th>列</th><th>A</th><th>B</th></tr></thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody></table>"
        "</body></html>"
    )


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
