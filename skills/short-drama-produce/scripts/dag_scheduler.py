#!/usr/bin/env python3
"""Dependency-aware scheduling helpers for v1.1 shot production.

The scheduler keeps independent ASSET shots eligible for concurrent execution and
holds CHAIN_PREV shots until their declared parent has produced a video. The
it runs alongside the production tool in this skill.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from production_tool import (
    _project_file,
    confirm_job,
    find_project,
    normalize_cost_estimate,
    prepare_job,
    run_job,
)


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
    topological_order: list[str] = []

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
        topological_order.append(shot_id)

    for node in nodes:
        visit(node.shot_id, [])

    parallel_pool = [node.shot_id for node in nodes if node.start_frame_source == "ASSET"]
    waiting_pool = [node.shot_id for node in nodes if node.start_frame_source == "CHAIN_PREV"]
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
    if output_path.exists():
        raise FileExistsError(f"tail frame output already exists: {output_path.name}")
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
        "-n",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no tail frame")
    return output_path


def inject_head_frame(
    job: Mapping[str, Any], frame_path: Path, *, project_root: Path
) -> dict[str, Any]:
    """Return a child job carrying the parent's tail frame as its Head Frame."""
    if frame_path.is_absolute() or ".." in frame_path.parts:
        raise DagError("head frame must be a project-relative path")
    root = project_root.resolve()
    resolved = (root / frame_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DagError("head frame escapes the project root") from exc
    try:
        details = resolved.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"head frame is missing: {frame_path}") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise DagError("head frame must be a regular file")
    child = dict(job)
    child["head_frame"] = frame_path.as_posix()
    child["start_frame_source"] = "CHAIN_PREV"
    if not child.get("parent_shot_id"):
        raise DagError("CHAIN_PREV job requires parent_shot_id")
    return child


def _verified_parent_video(
    project_root: Path, result: Mapping[str, Any]
) -> tuple[Path, str]:
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise DagError("a chained shot requires exactly one parent video output")
    output = outputs[0]
    if not isinstance(output, Mapping):
        raise DagError("parent video output record is invalid")
    relative = output.get("path")
    digest_expected = output.get("sha256")
    size_expected = output.get("bytes")
    if (
        not isinstance(relative, str)
        or not isinstance(digest_expected, str)
        or not isinstance(size_expected, int)
        or output.get("media_type")
        not in {"video/mp4", "video/quicktime", "video/webm"}
    ):
        raise DagError("parent result is not a verified video output")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or len(pure.parts) < 3 or pure.parts[0] != "dist":
        raise DagError("parent video path is invalid")
    root = project_root.resolve()
    path = root.joinpath(*pure.parts)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        details = resolved.lstat()
    except FileNotFoundError as exc:
        raise DagError("parent video output is missing") from exc
    except ValueError as exc:
        raise DagError("parent video output escapes the project root") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise DagError("parent video output is not a regular file")
    digest = hashlib.sha256()
    size = 0
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    if size != size_expected or digest.hexdigest() != digest_expected:
        raise DagError("parent video changed after its production run")
    run_id = result.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_id) is None
    ):
        raise DagError("parent production run id is invalid")
    return path, pure.parts[1]


def _prepare_job_file(root: Path, job: Mapping[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="short-drama-job-") as directory:
        path = Path(directory) / "job.json"
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return prepare_job(root, path)


def _validate_production_plans(
    graph: Mapping[str, Any], plans: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    node_ids = [str(node["shot_id"]) for node in graph.get("nodes", [])]
    if set(plans) != set(node_ids):
        missing = sorted(set(node_ids) - set(plans))
        extra = sorted(set(plans) - set(node_ids))
        raise DagError(f"job manifest mismatch; missing={missing}, extra={extra}")
    dependencies = graph.get("dependencies", {})
    normalized: dict[str, dict[str, Any]] = {}
    totals: dict[str, Decimal] = {}
    for shot_id in node_ids:
        plan = plans[shot_id]
        if not isinstance(plan, Mapping) or set(plan) != {"job", "estimated_cost"}:
            raise DagError(f"job manifest entry is invalid: {shot_id}")
        job = plan.get("job")
        if not isinstance(job, Mapping):
            raise DagError(f"job specification is invalid: {shot_id}")
        if job.get("modality") != "video":
            raise DagError(f"shot job must use video modality: {shot_id}")
        if not isinstance(job.get("job_id"), str):
            raise DagError(f"shot job has no job_id: {shot_id}")
        expected_parent = dependencies.get(shot_id)
        if expected_parent is None:
            if job.get("start_frame_source", "ASSET") != "ASSET" or job.get("parent_shot_id") is not None or job.get("head_frame") is not None:
                raise DagError(f"ASSET shot has chained frame fields: {shot_id}")
        elif job.get("parent_shot_id") != expected_parent or job.get("start_frame_source") != "CHAIN_PREV" or job.get("head_frame") is not None:
            raise DagError(f"job dependency does not match storyboard: {shot_id}")
        estimate = normalize_cost_estimate(plan.get("estimated_cost"))
        amount = Decimal(estimate["amount"])
        totals[estimate["currency"]] = totals.get(estimate["currency"], Decimal(0)) + amount
        normalized[shot_id] = {
            "job": dict(job),
            "estimated_cost": estimate,
        }
    return normalized, {currency: format(amount, "f") for currency, amount in totals.items()}


def run_production_dag(
    project_root: Path,
    graph: Mapping[str, Any],
    plans: Mapping[str, Any],
    adapter_config: Path,
    *,
    request_confirmation: Callable[[Mapping[str, Any]], str],
    ffmpeg: str = "ffmpeg",
    extract_frame: Callable[[Path, Path], Path] | None = None,
) -> dict[str, Any]:
    """Run one shot at a time, confirming each exact job before paid execution."""
    root = find_project(project_root)
    normalized, totals = _validate_production_plans(graph, plans)
    node_list = graph.get("nodes", [])
    nodes = {str(node["shot_id"]): node for node in node_list}
    dependencies = graph.get("dependencies", {})
    results: dict[str, Any] = {}
    topological_order = graph.get("topological_order", [])
    if not nodes or len(nodes) != len(node_list):
        raise DagError("dependency graph must contain unique shot nodes")
    for shot_id, node in nodes.items():
        parent = node.get("parent_shot_id")
        source = node.get("start_frame_source")
        if source == "CHAIN_PREV":
            if parent is None or dependencies.get(shot_id) != parent:
                raise DagError(f"dependency graph disagrees with storyboard: {shot_id}")
        elif source == "ASSET":
            if parent is not None or shot_id in dependencies:
                raise DagError(f"ASSET shot has a parent dependency: {shot_id}")
        else:
            raise DagError(f"unknown frame source in dependency graph: {shot_id}")
    if set(topological_order) != set(nodes):
        raise DagError("dependency graph has no complete topological order")
    if len(topological_order) != len(nodes) or any(
        parent not in nodes or child not in nodes or parent == child
        for child, parent in dependencies.items()
    ):
        raise DagError("dependency graph contains invalid nodes or edges")
    order_index = {shot_id: index for index, shot_id in enumerate(topological_order)}
    if any(
        order_index[parent] >= order_index[child]
        for child, parent in dependencies.items()
    ):
        raise DagError("dependency graph order violates a parent edge")
    invocation_id = uuid.uuid4().hex

    for shot_id in topological_order:
        job = dict(normalized[shot_id]["job"])
        parent_id = dependencies.get(shot_id)
        if parent_id is not None:
            parent_result = results.get(parent_id)
            if not isinstance(parent_result, Mapping):
                raise DagError(f"parent shot has not completed: {parent_id}")
            parent_video, episode = _verified_parent_video(root, parent_result)
            parent_run = str(parent_result["run_id"])
            frame_relative = PurePosixPath(
                "dist",
                episode,
                "frames",
                parent_run,
                invocation_id,
                f"{shot_id}.png",
            )
            frame_path = _project_file(
                root, frame_relative.as_posix(), create_parent=True
            )
            if extract_frame is None:
                extract_tail_frame(parent_video, frame_path, ffmpeg=ffmpeg)
            else:
                extract_frame(parent_video, frame_path)
            job = inject_head_frame(
                job, Path(frame_relative.as_posix()), project_root=root
            )

        preview = _prepare_job_file(root, job)
        estimate = normalized[shot_id]["estimated_cost"]
        display = {**preview, "estimated_cost": estimate}
        confirmation = request_confirmation(display)
        if confirmation != preview["confirmation"]:
            raise DagError(f"production cancelled before running {shot_id}")
        confirm_job(root, job_id=str(job["job_id"]), confirmation=confirmation)
        result = run_job(
            root,
            job_id=str(job["job_id"]),
            adapter_config=adapter_config,
            estimated_cost=estimate,
        )
        results[shot_id] = result

    return {
        "state": "succeeded",
        "completed": list(topological_order),
        "estimated_totals": totals,
        "results": results,
    }


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
    if max_workers < 1:
        raise DagError("max_workers must be positive")
    if max_workers == 1:
        results: dict[str, Any] = {}
        completed: list[str] = []
        for shot_id in graph.get("topological_order", node_ids):
            results[shot_id] = execute(shot_id, jobs[shot_id])
            completed.append(shot_id)
        if set(completed) != set(node_ids):
            raise DagError("DAG execution stopped before all shots completed")
        return {"state": "succeeded", "completed": completed, "results": results}
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
    parser.add_argument(
        "--execute", action="store_true", help="Run the DAG after per-shot confirmation."
    )
    parser.add_argument(
        "--manifest", type=Path, help="JSON object with a jobs map keyed by shot ID."
    )
    parser.add_argument(
        "--adapter-config", type=Path, help="Adapter config stored outside the project."
    )
    args = parser.parse_args()
    try:
        graph = build_dag(parse_storyboard(args.storyboard))
        if not args.execute:
            print(json.dumps(graph, ensure_ascii=False, indent=None if args.as_json else 2))
            return 0
        if args.manifest is None or args.adapter_config is None:
            parser.error("--execute requires --manifest and --adapter-config")
        root = find_project(args.storyboard)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != {"jobs"}
            or not isinstance(manifest["jobs"], Mapping)
        ):
            raise DagError("manifest must contain exactly one object field: jobs")

        def confirm(preview: Mapping[str, Any]) -> str:
            print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
            return input("输入预览中的完整 confirmation 才运行此镜头；其他输入取消：").strip()

        result = run_production_dag(
            root,
            graph,
            manifest["jobs"],
            args.adapter_config,
            request_confirmation=confirm,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
