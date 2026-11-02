#!/usr/bin/env python3
"""Offline self-test for the standalone novel index."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from novel_index import build_index, sample_chapters, verify_index
from validate_episode_candidates import validate_candidates

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
        require(
            index["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest(),
            "source content hash",
        )
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        require(verify_index(index_path, source)["verified"] is True, "fresh index")
        require(sample_chapters(index_path, 2)["sampled_count"] == 2, "sample count")

        legacy_index = json.loads(index_path.read_text(encoding="utf-8"))
        del legacy_index["source"]["encoding"]
        del legacy_index["source"]["sha256"]
        index_path.write_text(json.dumps(legacy_index, ensure_ascii=False), encoding="utf-8")
        require(verify_index(index_path, source)["verified"] is True, "legacy UTF-8 index")

        source.write_text(
            source.read_text(encoding="utf-8").replace("起势", "开篇"),
            encoding="utf-8",
        )
        require(
            verify_index(root / "index.json", source)["verified"] is True,
            "legacy same-line compatibility",
        )
        current_index = build_index(source)
        index_path.write_text(json.dumps(current_index, ensure_ascii=False), encoding="utf-8")
        malformed_index = json.loads(json.dumps(current_index))
        malformed_index["source"]["sha256"] = None
        malformed_path = root / "index-malformed.json"
        malformed_path.write_text(json.dumps(malformed_index), encoding="utf-8")
        require(
            verify_index(malformed_path, source)["verified"] is False,
            "malformed source hash",
        )
        source.write_text(
            source.read_text(encoding="utf-8").replace("开篇", "改写"),
            encoding="utf-8",
        )
        result = verify_index(index_path, source)
        require(
            result["verified"] is False
            and "source content hash does not match the indexed source" in result["problems"],
            "same-line source drift",
        )

        gb_source = root / "novel-gb18030.txt"
        gb_index_path = root / "index-gb18030.json"
        gb_source.write_bytes(source.read_bytes().decode("utf-8").encode("gb18030"))
        gb_index = build_index(gb_source, encoding="gb18030")
        require(gb_index["source"]["encoding"] == "gb18030", "record source encoding")
        gb_index_path.write_text(json.dumps(gb_index, ensure_ascii=False), encoding="utf-8")
        require(verify_index(gb_index_path, gb_source)["verified"] is True, "GB18030 index")

        scoped_source = root / "novel-scoped.txt"
        scoped_source.write_text(
            "front matter\n"
            "第一章 开端\n正文展示人物目标、选择和代价，并推动故事继续发展；眼前压力迫使人物立刻行动并承受后果。\n"
            "第二章 反转\n新证据改变人物关系并带来压力，迫使角色再次作出选择；旧方法已经不再奏效，形成新的目标。\n"
            "第三章 结果\n人物兑现开篇承诺，同时面对一个新的后续问题，为接下来的故事留下入口。\n",
            encoding="utf-8",
        )
        scoped_index = build_index(scoped_source, start_line=2, end_line=5)
        scoped_path = root / "index-scoped.json"
        scoped_path.write_text(json.dumps(scoped_index), encoding="utf-8")
        require(
            scoped_index["scope"] == {"line_start": 2, "line_end": 5}
            and scoped_index["chapter_count"] == 2
            and scoped_index["problems"] == [],
            "scoped index records only complete selected chapters",
        )
        require(
            verify_index(scoped_path, scoped_source)["verified"] is True,
            "scoped index verifies against the complete source",
        )
        require(
            sample_chapters(scoped_path, 12)["scope"] == scoped_index["scope"],
            "scoped sample reports its source range",
        )
        bad_scope = json.loads(json.dumps(scoped_index))
        bad_scope["scope"]["line_end"] = 4
        bad_scope_path = root / "index-bad-scope.json"
        bad_scope_path.write_text(json.dumps(bad_scope), encoding="utf-8")
        require(
            verify_index(bad_scope_path, scoped_source)["verified"] is False,
            "scope drift is rejected",
        )
        misaligned_scope = build_index(scoped_source, start_line=3, end_line=5)
        require(
            any("start on a chapter heading" in problem for problem in misaligned_scope["problems"]),
            "scope must begin at a chapter boundary",
        )
        scoped_source.write_text(
            scoped_source.read_text(encoding="utf-8").replace("front matter", "changed preface"),
            encoding="utf-8",
        )
        require(
            "source content hash does not match the indexed source"
            in verify_index(scoped_path, scoped_source)["problems"],
            "changes outside the selected range still invalidate the source hash",
        )

        current_index = build_index(source)
        index_path.write_text(json.dumps(current_index, ensure_ascii=False), encoding="utf-8")
        source.write_text(source.read_text(encoding="utf-8") + "变化\n", encoding="utf-8")
        require(verify_index(index_path, source)["verified"] is False, "source drift")

    with tempfile.TemporaryDirectory() as directory:
        analysis = Path(directory)
        chapters = analysis / "chapters"
        chapters.mkdir()
        index = {"chapters": [{"sequence": 1}, {"sequence": 2}]}
        (analysis / "_index.json").write_text(json.dumps(index), encoding="utf-8")
        (chapters / "ch-001-extract.md").write_text(
            "P1 状态 | 建立当前目标\nP2 转折 | 目标发生变化\n", encoding="utf-8"
        )
        (chapters / "ch-002-extract.md").write_text(
            "P1 行动 | 角色作出选择\n", encoding="utf-8"
        )
        candidate = {
            "record_type": "episode_candidate",
            "candidate_id": "E1",
            "chapter_range": {"sequence_start": 1, "sequence_end": 2},
            "source_point_refs": [
                {"sequence": 1, "points": "P1-P2"},
                {"sequence": 2, "points": "P1"},
            ],
        }
        candidates = analysis / "episode-candidates.jsonl"
        candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
        require(validate_candidates(analysis) == [], "valid episode source points")
        index["chapters"].append({"sequence": 1})
        (analysis / "_index.json").write_text(json.dumps(index), encoding="utf-8")
        require(
            any("invalid chapter sequences" in problem for problem in validate_candidates(analysis)),
            "reject duplicate indexed chapter sequences",
        )
        index["chapters"].pop()
        (analysis / "_index.json").write_text(json.dumps(index), encoding="utf-8")
        (chapters / "ch-001-extract.md").write_text(
            "P1 状态 | 建立当前目标\nP1 状态 | 重复编号\nP2 转折 | 目标发生变化\n",
            encoding="utf-8",
        )
        require(
            any("duplicate extracted point IDs" in problem for problem in validate_candidates(analysis)),
            "reject duplicate extracted point identifiers",
        )
        (chapters / "ch-001-extract.md").write_text(
            "P1 状态 | 建立当前目标\nP2 转折 | 目标发生变化\n", encoding="utf-8"
        )
        candidate["source_point_refs"][0]["points"] = "P1-P3"
        candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
        require(
            any("P3" in problem for problem in validate_candidates(analysis)),
            "reject missing source point",
        )
        candidate["source_point_refs"] = [{"sequence": 2, "points": "P1"}]
        candidate["chapter_range"] = {"sequence_start": 1, "sequence_end": 1}
        candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
        require(
            any("outside chapter_range" in problem for problem in validate_candidates(analysis)),
            "reject source point outside candidate chapter range",
        )
        candidates.write_text("[]\n", encoding="utf-8")
        require(
            any("record must be a JSON object" in problem for problem in validate_candidates(analysis)),
            "reject non-object JSONL record",
        )

    print("24 self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
