import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class IngestResult:
    source_file_path: str
    sheet_name: str
    schema_json: str
    row_count: int


def save_upload_to_disk(
    *,
    data_dir: str,
    dataset_id: int,
    version_id: int,
    file_bytes: bytes,
) -> str:
    uploads_dir = Path(data_dir) / "uploads" / str(dataset_id) / str(version_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    path = uploads_dir / "source.xlsx"
    path.write_bytes(file_bytes)
    return str(path)


def ingest_excel(
    *,
    data_dir: str,
    dataset_id: int,
    version_id: int,
    file_bytes: bytes,
    sheet_name: str | None = None,
) -> IngestResult:
    source_file_path = save_upload_to_disk(
        data_dir=data_dir,
        dataset_id=dataset_id,
        version_id=version_id,
        file_bytes=file_bytes,
    )
    parsed_sheet_name, schema, row_count = parse_xlsx(
        file_bytes=file_bytes,
        sheet_name=sheet_name,
    )
    schema_json = json.dumps(schema, ensure_ascii=False)
    return IngestResult(
        source_file_path=source_file_path,
        sheet_name=parsed_sheet_name,
        schema_json=schema_json,
        row_count=row_count,
    )


def parse_xlsx(
    *,
    file_bytes: bytes,
    sheet_name: str | None = None,
) -> tuple[str, list[dict[str, object]], int]:
    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        workbook_xml = _read_xml(zf, "xl/workbook.xml")
        workbook_rels = _read_xml(zf, "xl/_rels/workbook.xml.rels")
        shared_strings = _read_shared_strings(zf)

        sheets = _workbook_sheets(workbook_xml, workbook_rels)
        if not sheets:
            raise ValueError("Excel 文件中未找到 sheet")

        selected = None
        if sheet_name:
            for s in sheets:
                if s.name == sheet_name:
                    selected = s
                    break
            if not selected:
                raise ValueError(f"未找到名为 {sheet_name} 的 sheet")
        else:
            selected = sheets[0]

        sheet_xml = _read_xml(zf, selected.path)
        headers = _read_header_row(sheet_xml, shared_strings)
        data_rows = _count_data_rows(sheet_xml)

        schema = [{"index": idx, "name": name} for idx, name in enumerate(headers)]
        return selected.name, schema, data_rows


@dataclass(frozen=True)
class _SheetRef:
    name: str
    path: str


def _read_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        raw = zf.read(name)
    except KeyError as e:
        raise ValueError(f"无效的 xlsx（缺少 {name}）") from e
    return ET.fromstring(raw)


def _workbook_sheets(
    workbook_xml: ET.Element,
    workbook_rels: ET.Element,
) -> list[_SheetRef]:
    ns_main = _ns(workbook_xml)
    ns_rel = _ns(workbook_rels)

    rid_to_target: dict[str, str] = {}
    for rel in workbook_rels.findall(f".//{{{ns_rel}}}Relationship"):
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if not rid or not target:
            continue
        if not target.startswith("/"):
            target = f"xl/{target.lstrip('./')}"
        else:
            target = target.lstrip("/")
        rid_to_target[rid] = target

    sheets_el = workbook_xml.find(f".//{{{ns_main}}}sheets")
    if sheets_el is None:
        return []

    sheets: list[_SheetRef] = []
    for sheet in sheets_el.findall(f".//{{{ns_main}}}sheet"):
        name = sheet.attrib.get("name")
        rid = sheet.attrib.get(f"{{http://schemas.openxmlformats.org/officeDocument/2006/relationships}}id")
        if not name or not rid:
            continue
        target = rid_to_target.get(rid)
        if not target:
            continue
        sheets.append(_SheetRef(name=name, path=target))
    return sheets


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = _read_xml(zf, "xl/sharedStrings.xml")
    except ValueError:
        return []

    ns = _ns(root)
    strings: list[str] = []
    for si in root.findall(f".//{{{ns}}}si"):
        text_parts = []
        for t in si.findall(f".//{{{ns}}}t"):
            if t.text:
                text_parts.append(t.text)
        strings.append("".join(text_parts))
    return strings


def _read_header_row(sheet_xml: ET.Element, shared_strings: list[str]) -> list[str]:
    ns = _ns(sheet_xml)
    first_row = sheet_xml.find(f".//{{{ns}}}sheetData/{{{ns}}}row")
    if first_row is None:
        return []

    headers: list[tuple[str, str]] = []
    for cell in first_row.findall(f"./{{{ns}}}c"):
        ref = cell.attrib.get("r") or ""
        col = "".join(ch for ch in ref if ch.isalpha())
        headers.append((col, _cell_value(cell, ns, shared_strings)))

    headers_sorted = sorted(headers, key=lambda x: _col_to_index(x[0]))
    return [name if name else f"COL_{i+1}" for i, (_, name) in enumerate(headers_sorted)]


def _count_data_rows(sheet_xml: ET.Element) -> int:
    ns = _ns(sheet_xml)
    rows = sheet_xml.findall(f".//{{{ns}}}sheetData/{{{ns}}}row")
    if not rows:
        return 0
    return max(len(rows) - 1, 0)


def _cell_value(cell: ET.Element, ns: str, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        t = cell.find(f".//{{{ns}}}t")
        return (t.text or "").strip() if t is not None else ""

    v = cell.find(f"./{{{ns}}}v")
    if v is None or v.text is None:
        return ""
    raw = v.text.strip()

    if cell_type == "s":
        try:
            idx = int(raw)
        except ValueError:
            return ""
        return shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
    return raw


def _ns(root: ET.Element) -> str:
    if root.tag.startswith("{") and "}" in root.tag:
        return root.tag.split("}")[0].lstrip("{")
    return ""


def _col_to_index(col: str) -> int:
    acc = 0
    for ch in col.upper():
        if not ("A" <= ch <= "Z"):
            continue
        acc = acc * 26 + (ord(ch) - ord("A") + 1)
    return acc


def ensure_valid_xlsx(file_bytes: bytes) -> None:
    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        _ = zf.namelist()


def iter_sheet_rows(
    *,
    file_bytes: bytes,
    sheet_name: str | None = None,
) -> Iterable[list[str]]:
    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        workbook_xml = _read_xml(zf, "xl/workbook.xml")
        workbook_rels = _read_xml(zf, "xl/_rels/workbook.xml.rels")
        shared_strings = _read_shared_strings(zf)
        sheets = _workbook_sheets(workbook_xml, workbook_rels)
        if not sheets:
            return []

        selected = sheets[0]
        if sheet_name:
            for s in sheets:
                if s.name == sheet_name:
                    selected = s
                    break

        sheet_xml = _read_xml(zf, selected.path)
        ns = _ns(sheet_xml)
        for row in sheet_xml.findall(f".//{{{ns}}}sheetData/{{{ns}}}row"):
            cells = []
            for cell in row.findall(f"./{{{ns}}}c"):
                cells.append(_cell_value(cell, ns, shared_strings))
            yield cells

