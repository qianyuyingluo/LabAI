from datetime import date, datetime, time
from pathlib import Path
import posixpath
from xml.etree import ElementTree
from zipfile import ZipFile

from app.parsers.text_parser import format_cell
from app.parsers.types import DocumentContent, MAX_CELLS, MAX_COLUMNS, MAX_ROWS, check_zip_limits


def parse_xlsx(path: Path) -> str:
    return extract_xlsx(path).text


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def _sheet_metadata(path: Path, result: DocumentContent) -> dict[str, dict]:
    """Read merge declarations without loading a dense, editable workbook."""
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    metadata = {}
    with ZipFile(path) as archive:
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {}
        for rel in relationships:
            if rel.attrib.get("TargetMode") == "External":
                continue
            target = rel.attrib.get("Target", "")
            targets[rel.attrib["Id"]] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        for sheet in book.findall(f"{{{main_ns}}}sheets/{{{main_ns}}}sheet"):
            member = targets.get(sheet.attrib.get(f"{{{rel_ns}}}id"))
            details = {"merged_ranges": [], "state": sheet.attrib.get("state", "visible")}
            metadata[sheet.attrib["name"]] = details
            if not member:
                continue
            with archive.open(member) as handle:
                for _, node in ElementTree.iterparse(handle, events=("end",)):
                    name = node.tag.rsplit("}", 1)[-1]
                    if name == "mergeCell":
                        if len(details["merged_ranges"]) < 10_000:
                            details["merged_ranges"].append(node.attrib["ref"])
                        else:
                            result.warn("Excel merged-range metadata was limited to 10000 entries per sheet.", truncated=True)
                    elif name in {"drawing", "legacyDrawing", "oleObjects"}:
                        result.warn("Excel contains drawings, charts or embedded objects; their visual content was not extracted.")
                    node.clear()
    return metadata


def extract_xlsx(path: Path) -> DocumentContent:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter, range_boundaries

    check_zip_limits(path)
    result = DocumentContent(metadata={"formulas_recalculated": False})
    sheet_metadata = _sheet_metadata(path, result)
    result.metadata["sheets"] = sheet_metadata
    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    cached = None
    cells_seen = 0
    formulas_without_cache = 0
    try:
        cached = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        result.metadata["sheet_names"] = workbook.sheetnames
        for sheet in workbook.worksheets:
            if result.full or cells_seen >= MAX_CELLS:
                result.warn("Excel extraction stopped before all worksheets were processed.", truncated=True)
                break
            max_row = min(sheet.max_row or MAX_ROWS, MAX_ROWS)
            max_column = min(sheet.max_column or MAX_COLUMNS, MAX_COLUMNS)
            details = sheet_metadata.setdefault(sheet.title, {"merged_ranges": []})
            details.update({"declared_rows": sheet.max_row, "declared_columns": sheet.max_column})
            if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS:
                result.warn(f"Sheet {sheet.title!r} was limited to {MAX_ROWS} rows and {MAX_COLUMNS} columns.", truncated=True)
            ranges = [(merged, range_boundaries(merged)) for merged in details["merged_ranges"]]
            formula_rows = sheet.iter_rows(max_row=max_row, max_col=max_column)
            cached_rows = cached[sheet.title].iter_rows(max_row=max_row, max_col=max_column)
            for row_number, (row, values) in enumerate(zip(formula_rows, cached_rows), 1):
                if cells_seen + len(row) > MAX_CELLS:
                    result.warn(f"Excel extraction stopped at the {MAX_CELLS}-cell limit.", truncated=True)
                    break
                cells_seen += len(row)
                cells = []
                text = []
                row_ranges = [name for name, (_, r1, _, r2) in ranges if r1 <= row_number <= r2]
                for column, (cell, cached_cell) in enumerate(zip(row, values), 1):
                    coordinate = f"{get_column_letter(column)}{row_number}"
                    value = cell.value
                    if value is None:
                        continue
                    number_format = cell.number_format
                    metadata = {"coordinate": coordinate, "number_format": number_format}
                    if cell.data_type == "f":
                        formula = value if isinstance(value, str) else getattr(value, "text", "[Unsupported formula representation]")
                        cached_value = _json_value(cached_cell.value)
                        metadata.update({"value": cached_value, "formula": formula, "cached_value": cached_value})
                        rendered = f"{coordinate}: formula={formula}; cached={format_cell(cached_value) if cached_value is not None else '[unavailable]'}"
                        if cached_value is None:
                            formulas_without_cache += 1
                    else:
                        metadata["value"] = _json_value(value)
                        rendered = f"{coordinate}={format_cell(metadata['value'])}"
                    if number_format != "General":
                        rendered += f" [number_format={number_format}]"
                    cells.append(metadata)
                    text.append(rendered)
                if cells:
                    if "first_row" not in details:
                        details["first_row"] = [{"coordinate": cell["coordinate"], "value": cell.get("value")} for cell in cells]
                    if row_ranges:
                        text.append("merged ranges: " + ", ".join(row_ranges))
                    result.add("table_row", " | ".join(text),
                               {"sheet": sheet.title, "row": row_number, "range": f"A{row_number}:{get_column_letter(max_column)}{row_number}"},
                               {"cells": cells, "merged_ranges": row_ranges})
                if result.full:
                    break
    finally:
        workbook.close()
        if cached is not None:
            cached.close()
    if formulas_without_cache:
        result.warn(f"{formulas_without_cache} formula cells have no available cached value; formulas were preserved but not calculated.")
    result.metadata["formulas_without_cache"] = formulas_without_cache
    result.metadata["cells_processed"] = cells_seen
    return result
