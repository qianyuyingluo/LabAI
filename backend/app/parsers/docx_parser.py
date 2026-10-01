from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile


def parse_docx(path: Path) -> str:
    with ZipFile(path) as archive:
        parts = ["word/document.xml"]
        parts.extend(
            name
            for name in sorted(archive.namelist())
            if name.startswith("word/header") or name.startswith("word/footer")
        )
        return "\n\n".join(read_xml_text(archive, name) for name in parts if name in archive.namelist())


def read_xml_text(archive: ZipFile, name: str) -> str:
    root = ElementTree.fromstring(archive.read(name))
    chunks = [
        node.text
        for node in root.iter()
        if node.text and node.tag.rsplit("}", 1)[-1] == "t"
    ]
    return " ".join(chunks)
