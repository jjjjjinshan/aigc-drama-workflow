#!/usr/bin/env python3
"""Offline self-test for the standalone novel index."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from novel_index import build_index, sample_chapters, verify_index

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("selftest.py requires Python 3.9 or newer")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "novel.txt"
        index_path = root / "index.json"
        source.write_text(
            "第一章 起势\n"
            + "人物做出选择，代价随之发生，旧关系因此失衡，新的目标也被迫提前。" * 4
            + "\n第二章 反转\n"
            + "旧承诺被新的证据推翻，人物必须在名誉与家人之间付出不可逆的代价。" * 4
            + "\n",
            encoding="utf-8",
        )
        index = build_index(source)
        require(index["chapter_count"] == 2, "chapter count")
        require(index["problems"] == [], "valid chapter index")
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        require(verify_index(index_path, source)["verified"] is True, "fresh index")
        require(sample_chapters(index_path, 2)["sampled_count"] == 2, "sample count")

        legacy_index = json.loads(index_path.read_text(encoding="utf-8"))
        del legacy_index["source"]["encoding"]
        index_path.write_text(json.dumps(legacy_index, ensure_ascii=False), encoding="utf-8")
        require(verify_index(index_path, source)["verified"] is True, "legacy UTF-8 index")

        gb_source = root / "novel-gb18030.txt"
        gb_index_path = root / "index-gb18030.json"
        gb_source.write_bytes(source.read_bytes().decode("utf-8").encode("gb18030"))
        gb_index = build_index(gb_source, encoding="gb18030")
        require(gb_index["source"]["encoding"] == "gb18030", "record source encoding")
        gb_index_path.write_text(json.dumps(gb_index, ensure_ascii=False), encoding="utf-8")
        require(verify_index(gb_index_path, gb_source)["verified"] is True, "GB18030 index")

        source.write_text(source.read_text(encoding="utf-8") + "变化\n", encoding="utf-8")
        require(verify_index(index_path, source)["verified"] is False, "source drift")

    print("8 self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
