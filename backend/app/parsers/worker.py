"""One isolated parsing job. No database, model client, or application startup."""
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from app.parsers.local_parser import parse_uploaded_file


def main() -> None:
    payload = json.load(sys.stdin)
    result = parse_uploaded_file(SimpleNamespace(**payload["file"]))
    Path(payload["output"]).write_text(
        json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
