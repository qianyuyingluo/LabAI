import re
from pathlib import Path

from app.parsers.types import DocumentContent, MAX_CELLS, check_zip_limits


def parse_docx(path: Path) -> str:
    return extract_docx(path).text


def extract_docx(path: Path) -> DocumentContent:
    from docx import Document
    from docx.text.paragraph import Paragraph

    check_zip_limits(path)
    document = Document(path)
    result = DocumentContent(metadata={"page_map_available": False, "format": "docx"})
    counters = {"paragraph": 0, "table": 0, "cells": 0}

    def inspect_visuals(element: object) -> None:
        tags = {node.tag.rsplit("}", 1)[-1] for node in element.iter()}
        if tags & {"drawing", "pict", "object"}:
            result.warn("DOCX contains pictures, drawings or embedded objects; their visual content was not extracted.")
        if tags & {"oMath", "oMathPara"}:
            result.warn("DOCX contains equations; mathematical structure was not extracted.")
        if tags & {"footnoteReference", "endnoteReference", "commentReference"}:
            result.warn("DOCX footnotes, endnotes or comments are referenced but their separate bodies were not extracted.")
        if tags & {"ins", "del", "moveFrom", "moveTo"}:
            result.warn("DOCX contains tracked changes; revision bodies may be omitted from the extracted view.")
        if "txbxContent" in tags:
            result.warn("DOCX contains floating text boxes; their content may not appear in the main reading order.")

    def cell_text(cell: object, depth: int = 0) -> str:
        if depth > 10:
            result.warn("Nested DOCX tables beyond 10 levels were omitted.", truncated=True)
            return ""
        values = []
        for item in cell.iter_inner_content():
            if isinstance(item, Paragraph):
                values.append(item.text)
            else:
                result.warn("Nested DOCX tables were rendered inside their containing cell; nested grid coordinates are not separately indexed.")
                for row in item.rows:
                    if counters["cells"] >= MAX_CELLS:
                        result.warn("DOCX table cell extraction limit reached.", truncated=True)
                        return "\n".join(values)
                    counters["cells"] += len(row.cells)
                    values.append(" | ".join(cell_text(value, depth + 1) for value in row.cells))
        return "\n".join(values)

    def add_table(table: object, part: str) -> None:
        counters["table"] += 1
        table_number = counters["table"]
        rows = []
        anchors = {}
        for row_number, row in enumerate(table.rows, 1):
            cells = row.cells
            if counters["cells"] + len(cells) > MAX_CELLS:
                result.warn("DOCX table cell extraction limit reached.", truncated=True)
                break
            counters["cells"] += len(cells)
            current = []
            for column, cell in enumerate(cells, 1 + row.grid_cols_before):
                key = cell._tc
                if key not in anchors:
                    anchors[key] = {"row": row_number, "column": column, "rowspan": 1,
                                    "colspan": cell._tc.grid_span, "text": cell_text(cell)}
                anchor = anchors[key]
                anchor["rowspan"] = max(anchor["rowspan"], row_number - anchor["row"] + 1)
                current.append((column, anchor))
            rows.append((row_number, current))
        for row_number, cells in rows:
            rendered = []
            metadata_cells = []
            for column, anchor in cells:
                coordinate = f"R{row_number}C{column}"
                origin = f"R{anchor['row']}C{anchor['column']}"
                merged = coordinate != origin
                text = f"[merged into {origin}]" if merged else anchor["text"]
                rendered.append(f"{coordinate}={text}")
                metadata_cells.append({"column": column, "anchor": origin, "rowspan": anchor["rowspan"],
                                       "colspan": anchor["colspan"], "text": text, "is_merged_continuation": merged})
            if any(anchor["text"].strip() for _, anchor in cells):
                result.add("table_row", " | ".join(rendered),
                           {"part": part, "table": table_number, "row": row_number},
                           {"cells": metadata_cells})
            if result.full:
                break

    def process(container: object, part: str) -> None:
        inspect_visuals(container._element)
        for item in container.iter_inner_content():
            if result.full:
                break
            if isinstance(item, Paragraph):
                counters["paragraph"] += 1
                style = item.style.name if item.style is not None else ""
                heading = re.search(r"heading\s*(\d+)", style, re.I)
                metadata = {"style": style}
                kind = "paragraph"
                if heading:
                    metadata["heading_level"] = int(heading[1])
                    kind = "heading"
                elif style.lower() in {"title", "subtitle"}:
                    kind = "heading"
                    metadata["heading_level"] = 0
                num_pr = item._p.pPr.numPr if item._p.pPr is not None else None
                if num_pr is not None or style.lower().startswith("list"):
                    kind = "list_item"
                    metadata["list_level"] = int(num_pr.ilvl.val) if num_pr is not None and num_pr.ilvl is not None else 0
                    if num_pr is not None and num_pr.numId is not None:
                        metadata["list_id"] = int(num_pr.numId.val)
                result.add(kind, item.text, {"part": part, "paragraph": counters["paragraph"]}, metadata)
            else:
                add_table(item, part)

    process(document, "body")
    seen_parts = set()
    for section in document.sections:
        if result.full:
            break
        for attribute in ("header", "first_page_header", "even_page_header", "footer", "first_page_footer", "even_page_footer"):
            container = getattr(section, attribute)
            if container.is_linked_to_previous:
                continue
            name = str(container.part.partname)
            if name not in seen_parts:
                seen_parts.add(name)
                process(container, name)
    result.metadata["cells_processed"] = counters["cells"]
    return result
