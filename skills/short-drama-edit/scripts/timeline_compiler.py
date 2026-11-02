#!/usr/bin/env python3
"""Validate a reviewed edit plan and compile a bounded EDL and SRT."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
PRODUCE_SCRIPTS = REPO_ROOT / "skills" / "short-drama-produce" / "scripts"
if str(PRODUCE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PRODUCE_SCRIPTS))

from production_tool import (  # noqa: E402
    _hash_production_output,
    _open_project_input,
    _project_file,
    _read_job,
    _validated_succeeded_outputs,
    find_project,
    job_status,
)

SCHEMA_VERSION = "1.0"
MAX_TIMELINE_MS = 24 * 60 * 60 * 1000
MAX_TIMELINE_BYTES = 2 * 1024 * 1024
MAX_SHEET_BYTES = 20 * 1024 * 1024
EPISODE_RE = re.compile(r"^EP[0-9]{3,}$")
IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,79}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.mp4$")
VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
AUDIO_TYPES = {
    "audio/wav", "audio/mpeg", "audio/mp4", "audio/aac", "audio/flac", "audio/ogg"
}
ROLES = {"dialogue", "music", "sfx", "ambience"}


class TimelineError(ValueError):
    """The edit plan or one of its pinned inputs is invalid."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TimelineError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise TimelineError(f"unsupported JSON number: {value}")


def _strict_json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise TimelineError("timeline JSON block is invalid") from exc


def _timeline_document(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TimelineError("timeline must be UTF-8") from exc
    blocks = re.findall(r"(?ms)^```json\s*\r?\n(.*?)^```\s*$", text)
    if len(blocks) != 1:
        raise TimelineError("timeline Markdown must contain exactly one JSON block")
    value = _strict_json(blocks[0])
    if not isinstance(value, dict):
        raise TimelineError("timeline JSON block must be an object")
    return value


def _exact_fields(value: object, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise TimelineError(f"{label} fields are invalid")
    return value


def _integer(value: object, label: str, *, minimum: int = 0, maximum: int = MAX_TIMELINE_MS) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise TimelineError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def _relative_project_path(value: object, label: str) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise TimelineError(f"{label} must be a project-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise TimelineError(f"{label} must be a safe project-relative path")
    return path.as_posix()


def _source_record(root: Path, episode_id: str, raw: object, expected: str) -> dict[str, Any]:
    source = _exact_fields(raw, {"job_id", "run_id", "path"}, "media source")
    job_id, run_id = source["job_id"], source["run_id"]
    relative = _relative_project_path(source["path"], "media source path")
    parts = PurePosixPath(relative).parts
    if (
        not isinstance(job_id, str)
        or JOB_ID_RE.fullmatch(job_id) is None
        or not isinstance(run_id, str)
        or RUN_ID_RE.fullmatch(run_id) is None
        or len(parts) < 4
        or parts[:2] != ("dist", episode_id)
    ):
        raise TimelineError("media source identity or path is invalid")
    status = job_status(root, job_id=job_id)
    latest = status.get("latest_run")
    if status.get("state") != "succeeded" or not isinstance(latest, Mapping):
        raise TimelineError(f"media job is not currently successful: {job_id}")
    if expected == "video" and status.get("modality") != "video":
        raise TimelineError(f"video clip does not reference a video job: {job_id}")
    if expected == "audio" and status.get("modality") not in {"tts", "music"}:
        raise TimelineError(f"audio clip does not reference an audio job: {job_id}")
    if latest.get("run_id") != run_id or latest.get("status") != "succeeded":
        raise TimelineError(f"media run is stale or failed: {job_id}/{run_id}")
    stored_job = _read_job(root, job_id)
    if latest.get("fingerprint") != stored_job.get("fingerprint"):
        raise TimelineError(f"media run belongs to an older job specification: {job_id}/{run_id}")
    try:
        _validated_succeeded_outputs(stored_job, latest)
    except ValueError as exc:
        raise TimelineError(f"media run output record is invalid: {job_id}/{run_id}") from exc
    records = latest.get("outputs")
    output = next(
        (item for item in records if isinstance(item, Mapping) and item.get("path") == relative),
        None,
    ) if isinstance(records, list) else None
    if not isinstance(output, Mapping):
        raise TimelineError(f"media path is not an output of the selected run: {relative}")
    media_type = output.get("media_type")
    if media_type not in (VIDEO_TYPES if expected == "video" else AUDIO_TYPES):
        raise TimelineError(f"media type does not match {expected}: {relative}")
    digest, size = _hash_production_output(root, relative)
    if digest != output.get("sha256") or size != output.get("bytes"):
        raise TimelineError(f"media bytes changed after production: {relative}")
    return {
        "job_id": job_id,
        "run_id": run_id,
        "path": relative,
        "modality": status["modality"],
        "media_type": media_type,
        "bytes": size,
        "sha256": digest,
    }


def _read_project_file(root: Path, relative: str, *, maximum: int) -> tuple[bytes, str]:
    path = _project_file(root, relative)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    with _open_project_input(root, relative) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            if size > maximum:
                raise TimelineError(f"project input is too large: {relative}")
            digest.update(chunk)
            chunks.append(chunk)
    if not path.is_file():
        raise TimelineError(f"project input is missing: {relative}")
    return b"".join(chunks), digest.hexdigest()


def _load_voice_lines(
    root: Path, episode_id: str, raw: object
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    source = _exact_fields(raw, {"sheet", "index", "screenplay"}, "voice_source")
    paths = {
        name: _relative_project_path(value, f"voice_source.{name}")
        for name, value in source.items()
    }
    expected_paths = {
        "sheet": f"episodes/{episode_id}/voice-record-sheet.jsonl",
        "index": f"episodes/{episode_id}/screenplay-index.jsonl",
        "screenplay": f"episodes/{episode_id}/剧本.md",
    }
    if paths != expected_paths:
        raise TimelineError("voice source must point to this episode's canonical files")
    contents: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    for key, relative in paths.items():
        contents[key], hashes[key] = _read_project_file(
            root, relative, maximum=MAX_SHEET_BYTES if key != "screenplay" else 50 * 1024 * 1024
        )
    checker = REPO_ROOT / "skills" / "short-drama-write" / "scripts" / "voice_sheet_check.py"
    if not checker.is_file():
        raise TimelineError("voice sheet checker is unavailable in this project")
    command = [
        sys.executable,
        str(checker),
        str(root / paths["sheet"]),
        "--index",
        str(root / paths["index"]),
        "--screenplay",
        str(root / paths["screenplay"]),
    ]
    try:
        checked = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TimelineError("voice sheet projection check could not run") from exc
    if checked.returncode != 0:
        raise TimelineError("voice sheet does not match the current screenplay")
    try:
        sheet_result = json.loads(checked.stdout)
    except json.JSONDecodeError as exc:
        raise TimelineError("voice sheet checker returned invalid output") from exc
    if not isinstance(sheet_result, Mapping) or sheet_result.get("status") != "pass":
        raise TimelineError("voice sheet projection check failed")
    lines: dict[str, str] = {}
    try:
        sheet_text = contents["sheet"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TimelineError("voice sheet must be UTF-8") from exc
    for raw_line in sheet_text.splitlines():
        if not raw_line.strip():
            continue
        record = _strict_json(raw_line)
        if not isinstance(record, Mapping) or record.get("record_type") == "sources":
            continue
        line_id, line_text = record.get("line_id"), record.get("line_text")
        if not isinstance(line_id, str) or not isinstance(line_text, str) or not line_text.strip():
            raise TimelineError("voice sheet line record is invalid")
        lines[line_id] = line_text
    sources = {
        key: {
            "path": paths[key],
            "bytes": len(contents[key]),
            "sha256": hashes[key],
        }
        for key in paths
    }
    return lines, sources


def _finite_gain(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TimelineError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or not -60 <= number <= 12:
        raise TimelineError(f"{label} must be between -60 and 12 dB")
    return number


def _srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _srt_text(captions: list[dict[str, Any]]) -> bytes:
    blocks = []
    for index, caption in enumerate(captions, 1):
        blocks.append(
            f"{index}\r\n{_srt_time(caption['start_ms'])} --> "
            f"{_srt_time(caption['end_ms'])}\r\n{caption['text']}"
        )
    return ("\r\n\r\n".join(blocks) + ("\r\n" if blocks else "")).encode("utf-8-sig")


def compile_timeline(root: Path, timeline_path: Path) -> tuple[dict[str, Any], bytes | None, dict[str, Any]]:
    root = find_project(root)
    timeline_relative = timeline_path.resolve().relative_to(root.resolve()).as_posix()
    parts = PurePosixPath(timeline_relative).parts
    if len(parts) != 3 or parts[0] != "episodes" or parts[2] != "成片编排.md":
        raise TimelineError("timeline must be episodes/<EP>/成片编排.md")
    episode_id = parts[1]
    if EPISODE_RE.fullmatch(episode_id) is None:
        raise TimelineError("timeline episode ID is invalid")
    timeline_bytes, _ = _read_project_file(
        root, timeline_relative, maximum=MAX_TIMELINE_BYTES
    )
    value = _timeline_document(timeline_bytes)
    timeline = _exact_fields(
        value,
        {
            "schema_version", "episode_id", "review_status", "frame", "subtitle_mode",
            "voice_source", "video_clips", "audio_clips", "captions", "output",
        },
        "timeline",
    )
    if timeline["schema_version"] != SCHEMA_VERSION or timeline["episode_id"] != episode_id:
        raise TimelineError("timeline version or episode ID does not match its path")
    if timeline["review_status"] != "approved":
        raise TimelineError("creator approval is required before compilation")
    frame = _exact_fields(timeline["frame"], {"width", "height", "fps_num", "fps_den"}, "frame")
    width = _integer(frame["width"], "frame.width", minimum=320, maximum=3840)
    height = _integer(frame["height"], "frame.height", minimum=320, maximum=3840)
    if width % 2 or height % 2:
        raise TimelineError("frame width and height must be even")
    fps_num = _integer(frame["fps_num"], "frame.fps_num", minimum=1, maximum=60)
    fps_den = _integer(frame["fps_den"], "frame.fps_den", minimum=1, maximum=1001)
    if not 1 <= fps_num / fps_den <= 60:
        raise TimelineError("frame rate must be between 1 and 60 fps")
    subtitle_mode = timeline["subtitle_mode"]
    if subtitle_mode not in {"none", "sidecar", "burn_in"}:
        raise TimelineError("subtitle_mode must be none, sidecar, or burn_in")

    video_raw = timeline["video_clips"]
    audio_raw = timeline["audio_clips"]
    captions_raw = timeline["captions"]
    if not isinstance(video_raw, list) or not video_raw or len(video_raw) > 1000:
        raise TimelineError("video_clips must contain 1 to 1000 entries")
    if not isinstance(audio_raw, list) or len(audio_raw) > 5000:
        raise TimelineError("audio_clips must contain at most 5000 entries")
    if not isinstance(captions_raw, list) or len(captions_raw) > 5000:
        raise TimelineError("captions must be a list of at most 5000 entries")

    clips: list[dict[str, Any]] = []
    clip_ids: set[str] = set()
    total_ms = 0
    for raw in video_raw:
        clip = _exact_fields(raw, {"clip_id", "source", "source_in_ms", "duration_ms"}, "video clip")
        clip_id = clip["clip_id"]
        if not isinstance(clip_id, str) or IDENTIFIER_RE.fullmatch(clip_id) is None or clip_id in clip_ids:
            raise TimelineError("video clip ID is invalid or repeated")
        clip_ids.add(clip_id)
        source_in = _integer(clip["source_in_ms"], f"{clip_id}.source_in_ms")
        duration = _integer(clip["duration_ms"], f"{clip_id}.duration_ms", minimum=1)
        source = _source_record(root, episode_id, clip["source"], "video")
        start, end = total_ms, total_ms + duration
        if end > MAX_TIMELINE_MS:
            raise TimelineError("episode exceeds 24 hours")
        total_ms = end
        clips.append(
            {
                "clip_id": clip_id,
                "source": source,
                "source_in_ms": source_in,
                "start_ms": start,
                "end_ms": end,
                "duration_ms": duration,
                "transition": "cut",
            }
        )

    audio: list[dict[str, Any]] = []
    audio_ids: set[str] = set()
    dialogue_by_line: dict[str, dict[str, Any]] = {}
    for raw in audio_raw:
        clip = _exact_fields(
            raw,
            {
                "clip_id", "role", "source", "source_in_ms", "timeline_start_ms",
                "duration_ms", "gain_db", "fade_in_ms", "fade_out_ms",
            } | ({"voice_line_id"} if isinstance(raw, Mapping) and raw.get("role") == "dialogue" else set()),
            "audio clip",
        )
        clip_id, role = clip["clip_id"], clip["role"]
        if not isinstance(clip_id, str) or IDENTIFIER_RE.fullmatch(clip_id) is None or clip_id in clip_ids | audio_ids:
            raise TimelineError("audio clip ID is invalid or repeated")
        if role not in ROLES:
            raise TimelineError("audio role is invalid")
        audio_ids.add(clip_id)
        source_in = _integer(clip["source_in_ms"], f"{clip_id}.source_in_ms")
        start = _integer(clip["timeline_start_ms"], f"{clip_id}.timeline_start_ms")
        duration = _integer(clip["duration_ms"], f"{clip_id}.duration_ms", minimum=1)
        end = start + duration
        if end > total_ms:
            raise TimelineError(f"audio clip extends beyond the video: {clip_id}")
        gain = _finite_gain(clip["gain_db"], f"{clip_id}.gain_db")
        fade_in = _integer(clip["fade_in_ms"], f"{clip_id}.fade_in_ms")
        fade_out = _integer(clip["fade_out_ms"], f"{clip_id}.fade_out_ms")
        if fade_in + fade_out > duration:
            raise TimelineError(f"audio fades exceed clip duration: {clip_id}")
        source = _source_record(root, episode_id, clip["source"], "audio")
        if role == "dialogue" and source["modality"] != "tts":
            raise TimelineError(f"dialogue clip must reference a TTS job: {clip_id}")
        if role == "music" and source["modality"] != "music":
            raise TimelineError(f"music clip must reference a music job: {clip_id}")
        item = {
            "clip_id": clip_id,
            "role": role,
            "source": source,
            "source_in_ms": source_in,
            "start_ms": start,
            "end_ms": end,
            "duration_ms": duration,
            "gain_db": gain,
            "fade_in_ms": fade_in,
            "fade_out_ms": fade_out,
        }
        if role == "dialogue":
            line_id = clip.get("voice_line_id")
            if not isinstance(line_id, str) or re.fullmatch(r"VLINE-[A-Z0-9-]{1,80}", line_id) is None or line_id in dialogue_by_line:
                raise TimelineError("dialogue clip requires a unique VLINE ID")
            item["voice_line_id"] = line_id
            dialogue_by_line[line_id] = item
        elif "voice_line_id" in clip:
            raise TimelineError("only dialogue clips may use voice_line_id")
        audio.append(item)

    output = _exact_fields(timeline["output"], {"filename"}, "output")
    filename = output["filename"]
    if not isinstance(filename, str) or FILENAME_RE.fullmatch(filename) is None:
        raise TimelineError("output filename must be a safe MP4 basename")

    voice_lines: dict[str, str] = {}
    voice_sources: dict[str, dict[str, Any]] = {}
    voice_source = timeline["voice_source"]
    if dialogue_by_line or captions_raw:
        voice_lines, voice_sources = _load_voice_lines(root, episode_id, voice_source)
        missing = set(dialogue_by_line) - set(voice_lines)
        if missing:
            raise TimelineError("dialogue references unknown voice lines: " + ", ".join(sorted(missing)))
    elif voice_source is not None:
        raise TimelineError("voice_source is only needed for dialogue or captions")
    if subtitle_mode == "none" and captions_raw:
        raise TimelineError("captions must be empty when subtitle_mode is none")
    if subtitle_mode != "none" and set(dialogue_by_line) != {
        caption.get("voice_line_id") for caption in captions_raw if isinstance(caption, Mapping)
    }:
        raise TimelineError("sidecar/burn-in captions must cover each selected dialogue clip once")

    compiled_captions: list[dict[str, Any]] = []
    seen_captions: set[str] = set()
    for raw in captions_raw:
        caption = _exact_fields(raw, {"voice_line_id", "timeline_start_ms", "duration_ms"}, "caption")
        line_id = caption["voice_line_id"]
        if not isinstance(line_id, str) or line_id in seen_captions or line_id not in dialogue_by_line:
            raise TimelineError("caption line ID is invalid or repeated")
        seen_captions.add(line_id)
        start = _integer(caption["timeline_start_ms"], f"{line_id}.timeline_start_ms")
        duration = _integer(caption["duration_ms"], f"{line_id}.duration_ms", minimum=1)
        end = start + duration
        dialogue = dialogue_by_line[line_id]
        if start < dialogue["start_ms"] or end > dialogue["end_ms"]:
            raise TimelineError(f"caption falls outside its dialogue audio: {line_id}")
        text = voice_lines.get(line_id)
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 2000
            or "\x00" in text
            or re.search(r"\r?\n\s*\r?\n", text)
        ):
            raise TimelineError(f"voice line cannot be represented as an SRT cue: {line_id}")
        compiled_captions.append(
            {"voice_line_id": line_id, "start_ms": start, "end_ms": end, "text": text}
        )
    compiled_captions.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["voice_line_id"]))

    edl = {
        "schema_version": SCHEMA_VERSION,
        "compiler": "short-drama-edit/timeline_compiler.py",
        "episode_id": episode_id,
        "review_status": "approved",
        "quality_verdict": "not_assessed",
        "frame": {"width": width, "height": height, "fps_num": fps_num, "fps_den": fps_den},
        "duration_ms": total_ms,
        "subtitle_mode": subtitle_mode,
        "subtitle_file": "subtitles.srt" if compiled_captions and subtitle_mode != "none" else None,
        "output_filename": filename,
        "timeline_sha256": hashlib.sha256(timeline_bytes).hexdigest(),
        "timeline_bytes": len(timeline_bytes),
        "voice_sources": voice_sources,
        "video_clips": clips,
        "audio_clips": audio,
        "captions": [
            {key: caption[key] for key in ("voice_line_id", "start_ms", "end_ms")}
            for caption in compiled_captions
        ],
    }
    summary = {
        "status": "pass",
        "episode_id": episode_id,
        "duration_ms": total_ms,
        "video_clip_count": len(clips),
        "audio_clip_count": len(audio),
        "caption_count": len(compiled_captions),
        "quality_verdict": "not_assessed",
    }
    srt = _srt_text(compiled_captions) if compiled_captions and subtitle_mode != "none" else None
    return edl, srt, summary


def write_compilation(root: Path, episode_id: str, output_dir: str, edl: Mapping[str, Any], srt: bytes | None) -> Path:
    parts = PurePosixPath(output_dir).parts
    if (
        PurePosixPath(output_dir).is_absolute()
        or ".." in parts
        or len(parts) != 4
        or parts[:3] != ("dist", episode_id, "assembly")
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}", parts[3]) is None
    ):
        raise TimelineError("output directory must be dist/<EP>/assembly/<version>")
    final = root.joinpath(*parts)
    if final.exists():
        raise TimelineError("output version already exists; choose a new version name")
    _project_file(
        root,
        (PurePosixPath(*parts[:3]) / ".compile-probe").as_posix(),
        create_parent=True,
    )
    if final.exists():
        raise TimelineError("output version appeared while compiling")
    staging = Path(tempfile.mkdtemp(prefix=".assembly-", dir=final.parent))
    try:
        (staging / "timeline.edl.json").write_text(
            json.dumps(edl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if srt is not None:
            (staging / "subtitles.srt").write_bytes(srt)
        os.rename(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline", type=Path)
    parser.add_argument("--output-dir", help="new project-relative dist/<EP>/assembly/<version> directory")
    args = parser.parse_args(argv)
    try:
        root = find_project(args.timeline)
        edl, srt, summary = compile_timeline(root, args.timeline)
        if args.output_dir:
            output = write_compilation(root, summary["episode_id"], args.output_dir, edl, srt)
            summary["output_dir"] = output.relative_to(root).as_posix()
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
