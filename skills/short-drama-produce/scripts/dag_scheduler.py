#!/usr/bin/env python3
"""Dependency-aware scheduling helpers for v1.1 shot production.

The scheduler keeps independent ASSET shots eligible for concurrent execution and
holds CHAIN_PREV shots until their declared parent has produced a video. The
module is stdlib-only so it can be copied with the production skill.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


SHOT_HEADING_RE = re.compile(r"^##\s+(SHOT-[A-Z0-9-]+)\b", re.MULTILINE)
FIELD_RE = re.compile(r"^-\s+([^：\n]+)：(.+)$", re.MULTILINE)
FRAME_SOURCES = {"ASSET", "CHAIN_PREV"}


class DagError(ValueError):
    """The storyboard cannot be represented as an acyclic dependency graph."""


@dataclass(frozen=True)
class ShotNode:
    shot_id: str
    start_frame_source: str
    parent_shot_id: str | None


def parse_storyboard(path: Path) -> list[ShotNode]:
    """Read v1.1 frame-source fields from a creator storyboard Markdown file."""
    document = path.read_text(encoding="utf-8")
    headings = list(SHOT_HEADING_RE.finditer(document))
    nodes: list[ShotNode] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(document)
        section = document[heading.start():end]
        fields = {key: value.strip().rstrip("。") for key, value in FIELD_RE.findall(section)}
        shot_id = heading.group(1)
        source = fields.get("start_frame_source")
        parent = fields.get("parent_shot_id")
        if source not in FRAME_SOURCES:
            raise DagError(
                f"CHECK_011_INVALID_START_FRAME_SOURCE: {shot_id}: "
                "start_frame_source must be ASSET or CHAIN_PREV"
            )
        if source == "ASSET":
            if parent not in {None, "", "无", "null", "None"}:
                raise DagError(
                    f"CHECK_012_INVALID_PARENT_SHOT: {shot_id}: ASSET shot has a parent"
                )
            parent = None
        elif parent in {None, "", "无", "null", "None"}:
            raise DagError(
                f"CHECK_012_INVALID_PARENT_SHOT: {shot_id}: CHAIN_PREV shot has no parent"
            )
        nodes.append(ShotNode(shot_id, source, parent))
    if not nodes:
        raise DagError("storyboard contains no SHOT sections")
    return nodes


def build_dag(nodes: list[ShotNode]) -> dict[str, Any]:
    """Build dependency and pool views, rejecting unknown parents and cycles."""
    by_id = {node.shot_id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise DagError("duplicate shot id in storyboard")
    dependencies: dict[str, str] = {}
    for node in nodes:
        if node.start_frame_source == "CHAIN_PREV":
            assert node.parent_shot_id is not None
            if node.parent_shot_id not in by_id:
                raise DagError(
                    f"CHECK_012_INVALID_PARENT_SHOT: {node.shot_id}: "
                    f"unknown parent {node.parent_shot_id}"
                )
            if node.parent_shot_id == node.shot_id:
                raise DagError(
                    f"CHECK_013_CIRCULAR_DEPENDENCY: {node.shot_id}: self dependency"
                )
            dependencies[node.shot_id] = node.parent_shot_id

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(shot_id: str, path: list[str]) -> None:
        if shot_id in visiting:
            cycle = path[path.index(shot_id):] + [shot_id]
            raise DagError("CHECK_013_CIRCULAR_DEPENDENCY: " + " -> ".join(cycle))
        if shot_id in visited:
            return
        visiting.add(shot_id)
        parent = dependencies.get(shot_id)
        if parent is not None:
            visit(parent, path + [shot_id])
        visiting.remove(shot_id)
        visited.add(shot_id)

    for node in nodes:
        visit(node.shot_id, [])

    parallel_pool = [node.shot_id for node in nodes if node.start_frame_source == "ASSET"]
    waiting_pool = [node.shot_id for node in nodes if node.start_frame_source == "CHAIN_PREV"]
    topological_order = [node.shot_id for node in nodes if node.shot_id in visited]
    return {
        "nodes": [node.__dict__ for node in nodes],
        "dependencies": dependencies,
        "parallel_pool": parallel_pool,
        "waiting_pool": waiting_pool,
        "topological_order": topological_order,
    }


def extract_tail_frame(
    video_path: Path,
    output_path: Path,
    *,
    ffmpeg: str = "ffmpeg",
    clean_frames: int = 1,
) -> Path:
    """Extract a clean tail frame with FFmpeg after a parent video completes."""
    if clean_frames not in {1, 10}:
        raise ValueError("clean_frames must be 1 or 10")
    if not video_path.is_file():
        raise FileNotFoundError(f"parent video is missing: {video_path}")
    if shutil.which(ffmpeg) is None and not Path(ffmpeg).is_file():
        raise FileNotFoundError(f"ffmpeg executable is unavailable: {ffmpeg}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # -sseof seeks from the end. Selecting a short tail window avoids a partial
    # final frame while -frames:v limits the output to one deterministic PNG.
    seek = "-0.2" if clean_frames == 1 else "-0.5"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-sseof",
        seek,
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-y",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no tail frame")
    return output_path


def inject_head_frame(job: Mapping[str, Any], frame_path: Path) -> dict[str, Any]:
    """Return a child job carrying the parent's tail frame as its Head Frame."""
    if not frame_path.is_file():
        raise FileNotFoundError(f"head frame is missing: {frame_path}")
    if frame_path.is_absolute() or ".." in frame_path.parts:
        raise DagError("head frame must be a project-relative path")
    child = dict(job)
    child["head_frame"] = frame_path.as_posix()
    child["start_frame_source"] = "CHAIN_PREV"
    if not child.get("parent_shot_id"):
        raise DagError("CHAIN_PREV job requires parent_shot_id")
    return child


def run_dag(
    graph: Mapping[str, Any],
    jobs: Mapping[str, Mapping[str, Any]],
    execute: Callable[[str, Mapping[str, Any]], Any],
    *,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Execute independent roots concurrently and chain nodes after their parent.

    ``execute`` owns production confirmation and returns the completed output for
    one shot. This function only enforces ordering; it never bypasses the
    prepare/confirm gate.
    """
    dependencies = dict(graph.get("dependencies", {}))
    node_ids = [str(node["shot_id"]) for node in graph.get("nodes", [])]
    missing = [shot_id for shot_id in node_ids if shot_id not in jobs]
    if missing:
        raise DagError("missing job specs: " + ", ".join(missing))
    results: dict[str, Any] = {}
    completed: set[str] = set()

    def execute_one(shot_id: str) -> tuple[str, Any]:
        return shot_id, execute(shot_id, jobs[shot_id])

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        roots = [shot_id for shot_id in node_ids if shot_id not in dependencies]
        pending: dict[Future[tuple[str, Any]], str] = {
            pool.submit(execute_one, shot_id): shot_id for shot_id in roots
        }
        while pending:
            for future in as_completed(list(pending)):
                shot_id = pending.pop(future)
                key, result = future.result()
                results[key] = result
                completed.add(key)
                children = [
                    child for child, parent in dependencies.items() if parent == shot_id
                ]
                for child in children:
                    if child in completed or any(
                        pending_shot == child for pending_shot in pending.values()
                    ):
                        continue
                    if dependencies[child] in completed:
                        pending[pool.submit(execute_one, child)] = child
                break
    if len(completed) != len(node_ids):
        raise DagError("DAG execution stopped before all shots completed")
    return {"state": "succeeded", "completed": node_ids, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storyboard", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    graph = build_dag(parse_storyboard(args.storyboard))
    print(json.dumps(graph, ensure_ascii=False, indent=None if args.as_json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
