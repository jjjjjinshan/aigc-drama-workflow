#!/usr/bin/env python3
"""Validate episode candidate chapter and extracted-point references."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


POINT_RANGE = re.compile(r"^P([1-9][0-9]*)(?:-P?([1-9][0-9]*))?$")
EXTRACTED_POINT = re.compile(r"^P([1-9][0-9]*)\s", re.MULTILINE)


def validate_candidates(analysis_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        index = json.loads((analysis_dir / "_index.json").read_text(encoding="utf-8-sig"))
        raw_chapters = index["chapters"]
        sequences = [chapter["sequence"] for chapter in raw_chapters]
        chapters = set(sequences)
        if (
            not chapters
            or any(type(sequence) is not int or sequence < 1 for sequence in sequences)
            or len(chapters) != len(sequences)
        ):
            raise ValueError("index contains invalid chapter sequences")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"cannot read chapter index: {exc}"]

    point_ids: dict[int, set[int]] = {}
    for sequence in chapters:
        path = analysis_dir / "chapters" / f"ch-{sequence:03d}-extract.md"
        try:
            points = [
                int(match.group(1))
                for match in EXTRACTED_POINT.finditer(path.read_text(encoding="utf-8-sig"))
            ]
            point_ids[sequence] = set(points)
            if len(point_ids[sequence]) != len(points):
                errors.append(f"chapter {sequence}: duplicate extracted point IDs")
        except (OSError, UnicodeError) as exc:
            errors.append(f"chapter {sequence}: cannot read extraction: {exc}")

    try:
        lines = (analysis_dir / "episode-candidates.jsonl").read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        return errors + [f"cannot read episode candidates: {exc}"]

    candidate_ids: set[str] = set()
    candidate_count = 0
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record: dict[str, Any] = json.loads(line)
        except (json.JSONDecodeError, TypeError) as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"line {line_number}: record must be a JSON object")
            continue
        if record.get("record_type") == "sources":
            continue
        if record.get("record_type") != "episode_candidate":
            errors.append(f"line {line_number}: unexpected record_type")
            continue

        candidate_count += 1
        candidate_id = record.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"line {line_number}: missing candidate_id")
            candidate_id = f"line {line_number}"
        elif candidate_id in candidate_ids:
            errors.append(f"line {line_number}: duplicate candidate_id {candidate_id}")
        candidate_ids.add(candidate_id)

        chapter_range = record.get("chapter_range")
        try:
            start = chapter_range["sequence_start"]
            end = chapter_range["sequence_end"]
        except (TypeError, KeyError, ValueError):
            errors.append(f"{candidate_id}: invalid chapter_range")
            continue
        if (
            type(start) is not int
            or type(end) is not int
            or start > end
            or start not in chapters
            or end not in chapters
            or sum(start <= sequence <= end for sequence in chapters) != end - start + 1
        ):
            errors.append(f"{candidate_id}: chapter_range is outside the indexed chapters")
            continue

        refs = record.get("source_point_refs")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{candidate_id}: source_point_refs must be a non-empty list")
            continue
        for ref in refs:
            try:
                sequence = ref["sequence"]
                point_range = ref["points"]
            except (TypeError, KeyError, ValueError):
                errors.append(f"{candidate_id}: invalid source_point_ref")
                continue
            if type(sequence) is not int or sequence < start or sequence > end:
                errors.append(f"{candidate_id}: source sequence {sequence} is outside chapter_range")
                continue
            match = POINT_RANGE.fullmatch(point_range) if isinstance(point_range, str) else None
            if not match:
                errors.append(f"{candidate_id}: invalid point range {point_range!r}")
                continue
            first = int(match.group(1))
            last = int(match.group(2) or first)
            if first > last:
                errors.append(f"{candidate_id}: reversed point range {point_range}")
                continue
            actual = point_ids.get(sequence, set())
            expected_count = last - first + 1
            present = {point for point in actual if first <= point <= last}
            if len(present) != expected_count:
                missing = (
                    [point for point in range(first, last + 1) if point not in actual]
                    if expected_count <= len(actual) + 1
                    else []
                )
                detail = (
                    ", ".join(f"P{point}" for point in missing)
                    if missing
                    else f"points in P{first}-P{last}"
                )
                errors.append(
                    f"{candidate_id}: sequence {sequence} references missing {detail}"
                )

    if candidate_count == 0:
        errors.append("no episode_candidate records found")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_dir", type=Path, help="项目开发/source-analysis directory")
    args = parser.parse_args()
    errors = validate_candidates(args.analysis_dir)
    if errors:
        print("Episode candidate validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Episode candidate chapter and source-point references are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
