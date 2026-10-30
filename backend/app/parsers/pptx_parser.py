from pathlib import Path

from app.parsers.types import DocumentContent, MAX_CELLS, MAX_PAGES, check_zip_limits


def parse_pptx(path: Path) -> str:
    return extract_pptx(path).text


def extract_pptx(path: Path) -> DocumentContent:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    check_zip_limits(path)
    presentation = Presentation(path)
    result = DocumentContent(metadata={"slide_count": len(presentation.slides), "notes_included": True})
    if len(presentation.slides) > MAX_PAGES:
        result.warn(f"Only the first {MAX_PAGES} presentation slides were extracted.", truncated=True)
    cells_seen = 0
    empty_slides = []

    def visit(shapes: object, slide_number: int, title_element: object, group_path: str = "", depth: int = 0) -> None:
        nonlocal cells_seen
        if depth > 20:
            result.warn("Presentation shape groups beyond 20 levels were omitted.", truncated=True)
            return
        for shape in shapes:
            if result.full:
                return
            shape_path = f"{group_path}/{shape.shape_id}".lstrip("/")
            location = {"slide": slide_number, "shape": shape_path}
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                visit(shape.shapes, slide_number, title_element, shape_path, depth + 1)
            elif shape.has_table:
                table = shape.table
                origins = {}
                for row_number, row in enumerate(table.rows, 1):
                    if cells_seen + len(row.cells) > MAX_CELLS:
                        result.warn("Presentation table cell extraction limit reached.", truncated=True)
                        break
                    cells_seen += len(row.cells)
                    cells = []
                    text = []
                    for column, cell in enumerate(row.cells, 1):
                        coordinate = f"R{row_number}C{column}"
                        if cell.is_merge_origin:
                            for r in range(row_number, row_number + cell.span_height):
                                for c in range(column, column + cell.span_width):
                                    origins[(r, c)] = coordinate
                        anchor = origins.get((row_number, column), coordinate)
                        value = f"[merged into {anchor}]" if cell.is_spanned else cell.text
                        text.append(f"{coordinate}={value}")
                        cells.append({"column": column, "anchor": anchor, "text": value,
                                      "rowspan": cell.span_height, "colspan": cell.span_width,
                                      "is_merged_continuation": cell.is_spanned})
                    if any(cell["text"].strip() for cell in cells):
                        result.add("table_row", " | ".join(text),
                                   {**location, "table": shape.shape_id, "row": row_number}, {"cells": cells})
                    if result.full:
                        return
            elif shape.has_text_frame:
                for paragraph_number, paragraph in enumerate(shape.text_frame.paragraphs, 1):
                    kind = "slide_title" if shape._element is title_element else "paragraph"
                    result.add(kind, paragraph.text,
                               {**location, "paragraph": paragraph_number},
                               {"list_level": paragraph.level, "shape_name": shape.name})
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE or getattr(shape, "has_chart", False):
                result.warn("Presentation contains pictures or charts; their visual content and chart data were not extracted.")
            tags = {node.tag.rsplit("}", 1)[-1] for node in shape._element.iter()}
            if tags & {"oMath", "oMathPara", "oleObj", "relIds"}:
                result.warn("Presentation contains equations, embedded objects or diagrams that were not fully extracted.")

    for number, slide in enumerate(presentation.slides, 1):
        if number > MAX_PAGES or result.full:
            break
        before = len(result.blocks)
        title = slide.shapes.title
        visit(slide.shapes, number, title._element if title is not None else None)
        if slide.has_notes_slide and not result.full:
            frame = slide.notes_slide.notes_text_frame
            if frame is not None:
                result.add("speaker_notes", frame.text, {"slide": number, "part": "notes"})
        if len(result.blocks) == before:
            empty_slides.append(number)
    if empty_slides:
        result.metadata["empty_slides"] = empty_slides
        result.warn(f"Slides without extractable text or notes: {', '.join(map(str, empty_slides))}; image/diagram content was not interpreted.")
    result.metadata["cells_processed"] = cells_seen
    return result
