"""Generated fixtures exercise content fidelity without private testfiles or APIs."""

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from app.parsers.local_parser import ParsedFileText, parse_uploaded_file
from app.parsers.types import ContentBlock, DocumentContent


class DocumentParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="labai-parser-tests-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def parse(self, path: Path, file_type: str = "document") -> ParsedFileText:
        return parse_uploaded_file(SimpleNamespace(
            id="fixture", original_filename=path.name, local_path=str(path),
            file_type=file_type, mime_type="application/octet-stream",
            extension=path.suffix[1:], size=path.stat().st_size,
        ))

    def test_docx_keeps_run_words_and_paragraph_table_order(self) -> None:
        from docx import Document

        document = Document()
        document.add_heading("Experiment", level=1)
        paragraph = document.add_paragraph()
        paragraph.add_run("micro")
        paragraph.add_run("scope").bold = True
        table = document.add_table(rows=2, cols=2)
        for row, values in zip(table.rows, [("Sample", "Value"), ("A", "10")]):
            for cell, value in zip(row.cells, values):
                cell.text = value
        document.add_paragraph("After table")
        path = self.root / "structure.docx"
        document.save(path)
        parsed = self.parse(path)
        self.assertEqual(parsed.parse_status, "success", parsed.error)
        self.assertEqual([b.kind for b in parsed.blocks], ["heading", "paragraph", "table_row", "table_row", "paragraph"])
        self.assertEqual(parsed.blocks[1].text, "microscope")
        self.assertEqual(parsed.blocks[0].metadata["heading_level"], 1)
        self.assertIn("R2C2=10", parsed.blocks[3].text)
        self.assertEqual(parsed.blocks[3].location["row"], 2)
        self.assertEqual(parsed.blocks[-1].text, "After table")
        self.assertFalse(parsed.metadata["page_map_available"])

    def test_docx_preserves_merge_origins(self) -> None:
        from docx import Document

        document = Document()
        table = document.add_table(rows=3, cols=3)
        table.cell(0, 0).merge(table.cell(1, 1)).text = "Merged label"
        table.cell(2, 2).text = "tail"
        path = self.root / "merged.docx"
        document.save(path)
        parsed = self.parse(path)
        origin = parsed.blocks[0].metadata["cells"][0]
        self.assertEqual((origin["rowspan"], origin["colspan"]), (2, 2))
        continuation = parsed.blocks[1].metadata["cells"][1]
        self.assertEqual(continuation["anchor"], "R1C1")
        self.assertTrue(continuation["is_merged_continuation"])
        self.assertEqual(parsed.text.count("Merged label"), 1)

    def test_docx_equations_are_reported_as_partial(self) -> None:
        from docx import Document
        from docx.oxml import OxmlElement

        document = Document()
        paragraph = document.add_paragraph("Equation below")
        paragraph._p.append(OxmlElement("m:oMath"))
        path = self.root / "equation.docx"
        document.save(path)
        parsed = self.parse(path)
        self.assertEqual(parsed.parse_status, "partial")
        self.assertTrue(any("equations" in warning for warning in parsed.warnings))
        self.assertFalse(parsed.metadata.get("parse_truncated", False))

    def test_pptx_uses_actual_order_group_text_tables_and_notes(self) -> None:
        from pptx import Presentation
        from pptx.util import Inches

        presentation = Presentation()
        first = presentation.slides.add_slide(presentation.slide_layouts[5])
        first.shapes.title.text = "Original first"
        second = presentation.slides.add_slide(presentation.slide_layouts[5])
        second.shapes.title.text = "Original second"
        second.notes_slide.notes_text_frame.text = "Speaker-only fact"
        group = second.shapes.add_group_shape()
        box = group.shapes.add_textbox(0, 0, Inches(2), Inches(1))
        box.text_frame.text = "Nested group fact"
        table = second.shapes.add_table(2, 2, 0, 0, Inches(2), Inches(1)).table
        table.cell(0, 0).text = "Header"
        table.cell(1, 1).text = "42"
        ids = presentation.slides._sldIdLst
        ids.insert(0, ids[1])
        path = self.root / "reordered.pptx"
        presentation.save(path)
        parsed = self.parse(path, "presentation")
        self.assertEqual(parsed.parse_status, "success", parsed.error)
        titles = [block for block in parsed.blocks if block.kind == "slide_title"]
        self.assertEqual([block.text for block in titles], ["Original second", "Original first"])
        self.assertEqual([block.location["slide"] for block in titles], [1, 2])
        self.assertTrue(any(block.text == "Nested group fact" and "/" in block.location["shape"] for block in parsed.blocks))
        note = next(block for block in parsed.blocks if block.kind == "speaker_notes")
        self.assertEqual(note.location["slide"], 1)
        self.assertIn("Speaker-only fact", note.text)
        self.assertTrue(any("R2C2=42" in block.text for block in parsed.blocks))

    def test_pptx_empty_slides_are_not_fake_text(self) -> None:
        from pptx import Presentation

        presentation = Presentation()
        presentation.slides.add_slide(presentation.slide_layouts[6])
        path = self.root / "empty.pptx"
        presentation.save(path)
        parsed = self.parse(path)
        self.assertEqual(parsed.parse_status, "partial")
        self.assertEqual(parsed.blocks, [])
        self.assertEqual(parsed.text_chars, 0)
        self.assertIsNotNone(parsed.error)

    def test_excel_preserves_formulas_cache_formats_and_merges(self) -> None:
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.title = "实验"
        sheet.append(["Value", "Ratio", "Cached formula", "No cache"])
        sheet.append([2, 0.125, "=A2*2", "=A2*3"])
        sheet["B2"].number_format = "0.0%"
        sheet.merge_cells("A4:B4")
        sheet["A4"] = "Merged header"
        path = self.root / "formulas.xlsx"
        book.save(path)
        with ZipFile(path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        xml = ElementTree.fromstring(entries["xl/worksheets/sheet1.xml"])
        cell = xml.find(f".//{namespace}c[@r='C2']")
        cell.find(namespace + "v").text = "4"
        entries["xl/worksheets/sheet1.xml"] = ElementTree.tostring(xml)
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        parsed = self.parse(path, "spreadsheet")
        row = next(block for block in parsed.blocks if block.location.get("row") == 2)
        cells = {cell["coordinate"]: cell for cell in row.metadata["cells"]}
        self.assertEqual(cells["C2"]["formula"], "=A2*2")
        self.assertEqual(cells["C2"]["cached_value"], 4)
        self.assertIsNone(cells["D2"]["cached_value"])
        self.assertEqual(cells["B2"]["number_format"], "0.0%")
        self.assertEqual(cells["B2"]["value"], 0.125)
        self.assertIn("A4:B4", parsed.metadata["sheets"]["实验"]["merged_ranges"])
        self.assertEqual(parsed.metadata["formulas_without_cache"], 1)
        self.assertEqual(parsed.parse_status, "partial")
        self.assertFalse(parsed.metadata["formulas_recalculated"])
        self.assertEqual(parsed.metadata["sheets"]["实验"]["first_row"][0], {"coordinate": "A1", "value": "Value"})
        self.assertTrue(all("first_row" not in block.metadata for block in parsed.blocks))

    def test_excel_dimension_limit_reports_partial(self) -> None:
        from openpyxl import Workbook

        book = Workbook()
        book.active["A1"] = "visible"
        book.active["A5"] = "omitted"
        path = self.root / "bounded.xlsx"
        book.save(path)
        with patch("app.parsers.excel_parser.MAX_ROWS", 2):
            parsed = self.parse(path)
        self.assertIn("visible", parsed.text)
        self.assertNotIn("omitted", parsed.text)
        self.assertEqual(parsed.parse_status, "partial")
        self.assertTrue(parsed.metadata["parse_truncated"])

    def test_pdf_preserves_physical_page_and_reports_empty_page(self) -> None:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 40 Td (Second page evidence) Tj ET")
        page[NameObject("/Contents")] = stream
        path = self.root / "pages.pdf"
        writer.write(path)
        parsed = self.parse(path)
        self.assertEqual(parsed.parse_status, "partial")
        self.assertEqual(parsed.metadata["empty_pages"], [1])
        self.assertEqual(parsed.blocks[0].location["page"], 2)
        self.assertIn("Second page evidence", parsed.blocks[0].text)

    def test_blank_pdf_has_no_extracted_content(self) -> None:
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        path = self.root / "blank.pdf"
        writer.write(path)
        parsed = self.parse(path)
        self.assertEqual(parsed.text, "")
        self.assertIsNotNone(parsed.error)
        self.assertNotEqual(parsed.parse_status, "success")
        self.assertTrue(any("OCR" in warning for warning in parsed.warnings))

    def test_csv_preserves_quotes_multiline_values_and_rows(self) -> None:
        path = self.root / "quoted.csv"
        path.write_text('name,value\n"alpha,beta","line1\nline2"\n', encoding="utf-8", newline="\n")
        parsed = self.parse(path)
        self.assertEqual(parsed.parse_status, "success", parsed.error)
        row = parsed.blocks[1]
        self.assertEqual(row.location, {"row": 2, "line_start": 2, "line_end": 3})
        self.assertEqual([cell["value"] for cell in row.metadata["cells"]], ["alpha,beta", "line1\nline2"])
        self.assertEqual(parsed.metadata["first_row"], ["name", "value"])
        self.assertTrue(all("first_row" not in block.metadata for block in parsed.blocks))

    def test_gb18030_is_not_accidentally_decoded_as_utf16(self) -> None:
        path = self.root / "chinese.txt"
        path.write_bytes("实验数据".encode("gb18030"))
        parsed = self.parse(path)
        self.assertEqual(parsed.blocks[0].text, "实验数据")
        self.assertEqual(parsed.metadata["encoding"], "gb18030")

    def test_text_indentation_is_preserved(self) -> None:
        path = self.root / "code.txt"
        path.write_text("    first\n        second", encoding="utf-8")
        parsed = self.parse(path)
        self.assertEqual(parsed.blocks[0].text, "    first\n        second")

    def test_byte_limit_does_not_change_utf8_encoding_at_character_boundary(self) -> None:
        path = self.root / "unicode.txt"
        path.write_bytes("实验数据".encode("utf-8"))
        with patch("app.parsers.text_parser.TEXT_BYTE_LIMIT", 7):
            parsed = self.parse(path)
        self.assertEqual(parsed.blocks[0].text, "实验")
        self.assertEqual(parsed.metadata["encoding"], "utf-8-sig")
        self.assertTrue(parsed.metadata["parse_truncated"])

    def test_large_metadata_has_a_separate_budget(self) -> None:
        document = DocumentContent()
        with patch("app.parsers.types.MAX_BLOCK_METADATA_CHARS", 100):
            document.add("table_row", "A1=large value", {"row": 1}, {"cells": [{"value": "x" * 1000}]})
        self.assertEqual(document.blocks[0].metadata, {"metadata_truncated": True})
        self.assertEqual(document.parse_status, "partial")
        self.assertTrue(document.metadata["parse_truncated"])

    def test_json_has_escaped_source_pointers(self) -> None:
        path = self.root / "sample.json"
        path.write_text(json.dumps({"a/b": {"~value": [True, 2]}, "empty": []}), encoding="utf-8")
        parsed = self.parse(path)
        self.assertEqual(parsed.parse_status, "success")
        self.assertEqual([block.location["json_pointer"] for block in parsed.blocks], ["/a~1b/~0value/0", "/a~1b/~0value/1", "/empty"])
        self.assertEqual(parsed.blocks[0].text, "true")

    def test_invalid_and_unsupported_files_do_not_report_success(self) -> None:
        for suffix, value, status in [("json", "{broken", "error"), ("docx", "not a ZIP", "error"), ("doc", "legacy", "unsupported")]:
            with self.subTest(suffix=suffix):
                path = self.root / ("bad." + suffix)
                path.write_text(value, encoding="utf-8")
                parsed = self.parse(path)
                self.assertEqual(parsed.parse_status, status)
                self.assertEqual(parsed.blocks, [])
                self.assertIsNotNone(parsed.error)

    def test_zip_expansion_limit_is_checked_before_loading(self) -> None:
        path = self.root / "oversized.docx"
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "x" * 1000)
        with patch("app.parsers.types.MAX_ZIP_UNCOMPRESSED_BYTES", 100):
            parsed = self.parse(path)
        self.assertEqual(parsed.parse_status, "partial")
        self.assertTrue(parsed.metadata["parse_truncated"])
        self.assertIn("expanded-size", parsed.error)

    def test_output_budget_and_ids_are_stable_and_json_serializable(self) -> None:
        path = self.root / "long.txt"
        path.write_text("first\n\n" + "x" * 1000, encoding="utf-8")
        with patch("app.parsers.types.MAX_OUTPUT_CHARS", 150):
            parsed = self.parse(path)
            again = self.parse(path)
        self.assertEqual(parsed.text, again.text)
        self.assertLessEqual(len(parsed.text), 150)
        self.assertEqual([b.id for b in parsed.blocks], ["b000001", "b000002"])
        self.assertTrue(parsed.metadata["parse_truncated"])
        json.dumps(asdict(parsed), ensure_ascii=False)
        self.assertEqual(ContentBlock("id", "text", "body", {}).metadata, {})
        self.assertEqual(DocumentContent().blocks, [])


if __name__ == "__main__":
    unittest.main()
