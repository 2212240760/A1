import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dataset, DatasetVersion, EvalJob, JobStatus, ReportType, RuleSet
from app.services.ingest import iter_sheet_rows, parse_xlsx
from app.services.reports import create_report


@dataclass(frozen=True)
class EvalOutputPaths:
    summary_json: str
    details_xlsx: str
    report_html: str
    report_pdf: str


def create_and_run_eval_job(
    db: Session,
    *,
    data_dir: str,
    owner_user_id: int,
    dataset_version_id: int,
) -> EvalJob:
    ruleset = (
        db.execute(select(RuleSet).where(RuleSet.is_active.is_(True)))
        .scalars()
        .first()
    )
    if not ruleset:
        raise ValueError("未找到已激活的规则集")

    dataset_version = db.get(DatasetVersion, dataset_version_id)
    if not dataset_version:
        raise ValueError("未找到数据集版本")

    dataset = db.get(Dataset, dataset_version.dataset_id)
    if not dataset or dataset.owner_user_id != owner_user_id:
        raise ValueError("无权访问该数据集版本")

    job = EvalJob(
        owner_user_id=owner_user_id,
        dataset_version_id=dataset_version_id,
        ruleset_id=ruleset.id,
        status=JobStatus.running,
        started_at=datetime.utcnow(),
    )
    db.add(job)
    db.flush()

    _run_eval_job(db, data_dir=data_dir, job=job, ruleset=ruleset, dataset_version=dataset_version)
    return job


def _run_eval_job(
    db: Session,
    *,
    data_dir: str,
    job: EvalJob,
    ruleset: RuleSet,
    dataset_version: DatasetVersion,
) -> None:
    out_dir = Path(data_dir) / "jobs" / "eval" / str(job.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        file_bytes = Path(dataset_version.source_file_path).read_bytes()
        sheet_name, schema, _row_count = parse_xlsx(file_bytes=file_bytes, sheet_name=dataset_version.sheet_name)
        header_row = [c["name"] for c in schema]

        rules_payload = json.loads(ruleset.definition_json)
        rules = rules_payload.get("rules") if isinstance(rules_payload, dict) else None
        if not isinstance(rules, list):
            raise ValueError("规则集 definition_json.rules 必须是数组")

        violations: list[dict[str, object]] = []
        by_severity: dict[str, int] = {"info": 0, "warn": 0, "error": 0}
        by_rule: dict[str, int] = {}

        total_rows = 0
        for row_idx, row in enumerate(
            _iter_data_rows(file_bytes=file_bytes, sheet_name=sheet_name, header_len=len(header_row)),
            start=2,
        ):
            total_rows += 1
            row_map = {header_row[i]: row[i] for i in range(len(header_row))}
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                name = str(rule.get("name") or "")
                severity = str(rule.get("severity") or "error")
                expr = str(rule.get("expr") or "")
                template = str(rule.get("message_template") or "规则未通过")

                ok, meta = _eval_expr(expr, row_map=row_map)
                if ok:
                    continue

                msg = _render_message(
                    template,
                    {
                        "row": row_idx,
                        "sheet": sheet_name,
                        **meta,
                    },
                )
                col = str(meta.get("col") or "")
                value = str(meta.get("value") or "")

                violations.append(
                    {
                        "row": row_idx,
                        "sheet": sheet_name,
                        "column": col,
                        "value": value,
                        "rule_name": name,
                        "severity": severity,
                        "message": msg,
                        "expr": expr,
                    }
                )

                if severity not in by_severity:
                    by_severity[severity] = 0
                by_severity[severity] += 1
                by_rule[name] = by_rule.get(name, 0) + 1

        summary = {
            "job_id": job.id,
            "dataset_version_id": job.dataset_version_id,
            "ruleset_id": job.ruleset_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_rows": total_rows,
            "violation_count": len(violations),
            "by_severity": by_severity,
            "by_rule": by_rule,
        }

        summary_path = out_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        details_xlsx_path = out_dir / "details.xlsx"
        details_xlsx_path.write_bytes(_build_details_xlsx(summary=summary, violations=violations))

        report_html_path = out_dir / "report.html"
        report_html = _build_report_html(summary=summary, violations=violations)
        report_html_path.write_text(report_html, encoding="utf-8")

        report_pdf_path = out_dir / "report.pdf"
        report_pdf_path.write_bytes(_html_to_pdf_bytes(report_html))

        result_files = EvalOutputPaths(
            summary_json=str(summary_path),
            details_xlsx=str(details_xlsx_path),
            report_html=str(report_html_path),
            report_pdf=str(report_pdf_path),
        )

        job.summary_json = json.dumps(summary, ensure_ascii=False)
        job.result_files_json = json.dumps(result_files.__dict__, ensure_ascii=False)
        job.status = JobStatus.succeeded
        finished_at = datetime.utcnow()
        job.finished_at = finished_at

        dataset = db.get(Dataset, dataset_version.dataset_id)
        if dataset:
            title_time = finished_at.strftime("%Y%m%d-%H%M%S")
            create_report(
                db,
                dataset_id=dataset.id,
                type=ReportType.eval,
                ref_job_id=job.id,
                title=f"{dataset.name}-{dataset_version.version_label}-{title_time}",
                export_files={"excel": str(details_xlsx_path), "pdf": str(report_pdf_path)},
                created_by=job.owner_user_id,
            )
        db.flush()
    except Exception as e:
        job.status = JobStatus.failed
        job.error_message = str(e)
        job.finished_at = datetime.utcnow()
        db.flush()


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


_RE_NOT_EMPTY = re.compile(r"^not_empty\(\s*([^)]+)\s*\)$")
_RE_GT = re.compile(r"^gt\(\s*([^,]+)\s*,\s*([^)]+)\s*\)$")


def _eval_expr(expr: str, *, row_map: dict[str, str]) -> tuple[bool, dict[str, object]]:
    expr = (expr or "").strip()

    m = _RE_NOT_EMPTY.match(expr)
    if m:
        col = _unquote(m.group(1).strip())
        value = row_map.get(col, "")
        ok = bool(str(value).strip())
        return ok, {"col": col, "value": value}

    m = _RE_GT.match(expr)
    if m:
        col = _unquote(m.group(1).strip())
        threshold_raw = _unquote(m.group(2).strip())
        value = row_map.get(col, "")
        try:
            v = float(str(value).strip())
            threshold = float(str(threshold_raw).strip())
            ok = v > threshold
            return ok, {"col": col, "value": value, "threshold": threshold}
        except ValueError:
            return False, {"col": col, "value": value, "threshold": threshold_raw}

    return False, {"expr": expr}


def _unquote(value: str) -> str:
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _render_message(template: str, ctx: dict[str, object]) -> str:
    rendered = template
    for k, v in ctx.items():
        rendered = rendered.replace(f"{{{k}}}", str(v))
    return rendered


def _build_report_html(*, summary: dict[str, object], violations: list[dict[str, object]]) -> str:
    rows = []
    for v in violations:
        rows.append(
            "<tr>"
            f"<td>{v.get('row')}</td>"
            f"<td>{_html_escape(str(v.get('column') or ''))}</td>"
            f"<td>{_html_escape(str(v.get('severity') or ''))}</td>"
            f"<td>{_html_escape(str(v.get('rule_name') or ''))}</td>"
            f"<td>{_html_escape(str(v.get('message') or ''))}</td>"
            f"<td>{_html_escape(str(v.get('value') or ''))}</td>"
            "</tr>"
        )

    summary_json = _html_escape(json.dumps(summary, ensure_ascii=False, indent=2))

    return (
        "<!doctype html>"
        "<html lang='zh-CN'>"
        "<head><meta charset='utf-8' /><title>评估报告</title></head>"
        "<body>"
        "<h1>评估报告</h1>"
        "<h2>汇总</h2>"
        f"<pre>{summary_json}</pre>"
        "<h2>明细</h2>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>行号</th><th>列</th><th>级别</th><th>规则</th><th>信息</th><th>值</th></tr></thead>"
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


def _build_details_xlsx(
    *,
    summary: dict[str, object],
    violations: list[dict[str, object]],
) -> bytes:
    summary_rows = [["key", "value"]]
    for k, v in summary.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        summary_rows.append([str(k), str(v)])

    details_rows = [["row", "column", "severity", "rule_name", "message", "value"]]
    for v in violations:
        details_rows.append(
            [
                str(v.get("row") or ""),
                str(v.get("column") or ""),
                str(v.get("severity") or ""),
                str(v.get("rule_name") or ""),
                str(v.get("message") or ""),
                str(v.get("value") or ""),
            ]
        )

    return _build_xlsx_bytes(
        sheets=[
            ("Summary", summary_rows),
            ("Details", details_rows),
        ]
    )


def _build_xlsx_bytes(*, sheets: list[tuple[str, list[list[str]]]]) -> bytes:
    shared_strings: list[str] = []

    def add_string(value: str) -> int:
        if value in shared_strings:
            return shared_strings.index(value)
        shared_strings.append(value)
        return len(shared_strings) - 1

    worksheet_paths: list[str] = []
    worksheet_xmls: list[str] = []

    for idx, (sheet_name, rows) in enumerate(sheets, start=1):
        sheet_xml_rows = []
        for r_idx, row in enumerate(rows, start=1):
            cells = []
            for c_idx, value in enumerate(row, start=1):
                ref = f"{_index_to_col(c_idx)}{r_idx}"
                s_idx = add_string(str(value))
                cells.append(f'<c r="{ref}" t="s"><v>{s_idx}</v></c>')
            sheet_xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

        sheet_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            + "".join(sheet_xml_rows)
            + "</sheetData></worksheet>"
        )
        worksheet_xmls.append(sheet_xml)
        worksheet_paths.append(f"xl/worksheets/sheet{idx}.xml")

    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">'
        + "".join(f"<si><t>{_xml_escape(s)}</t></si>" for s in shared_strings)
        + "</sst>"
    )

    sheets_xml = "".join(
        f'<sheet name="{_xml_escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, (name, _rows) in enumerate(sheets, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets_xml}</sheets>"
        "</workbook>"
    )

    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            '<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet{idx}.xml"/>'.format(idx=idx)
            for idx in range(1, len(sheets) + 1)
        )
        + "</Relationships>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            '<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.format(
                idx=idx
            )
            for idx in range(1, len(sheets) + 1)
        )
        + '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        zf.writestr("xl/sharedStrings.xml", shared_xml)
        for path, xml in zip(worksheet_paths, worksheet_xmls, strict=True):
            zf.writestr(path, xml)

    return buf.getvalue()


def _index_to_col(idx: int) -> str:
    acc = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        acc = chr(ord("A") + rem) + acc
    return acc


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _html_to_pdf_bytes(html: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+\n", "\n", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    t = c.beginText(40, 800)
    t.setFont("Helvetica", 11)
    for ln in lines[:120]:
        t.textLine(ln)
    c.drawText(t)
    c.showPage()
    c.save()
    return buf.getvalue()


def _build_simple_pdf_bytes(lines: list[str]) -> bytes:
    content_lines = []
    y = 790
    for ln in lines[:80]:
        escaped = (
            ln.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        content_lines.append(f"1 0 0 1 50 {y} Tm ({escaped}) Tj")
        y -= 14

    stream = "BT /F1 12 Tf " + " ".join(content_lines) + " ET"
    stream_bytes = stream.encode("latin-1", errors="replace")

    objects: list[bytes] = []

    def add(obj: str | bytes) -> int:
        if isinstance(obj, bytes):
            objects.append(obj)
        else:
            objects.append(obj.encode("latin-1", errors="replace"))
        return len(objects)

    add("1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    add("2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    add(
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
    )
    add("4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    add(
        f"5 0 obj << /Length {len(stream_bytes)} >> stream\n".encode("latin-1")
        + stream_bytes
        + b"\nendstream endobj\n"
    )

    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj)
        if not obj.endswith(b"\n"):
            pdf.write(b"\n")

    xref_pos = pdf.tell()
    pdf.write(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
    pdf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.write(f"{off:010d} 00000 n \n".encode("latin-1"))

    pdf.write(
        (
            "trailer << /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".format(
                size=len(offsets), xref=xref_pos
            )
        ).encode("latin-1")
    )
    return pdf.getvalue()
