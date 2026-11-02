#!/usr/bin/env python3
"""Render a reviewed, provenance-checked EDL into a new versioned episode master."""

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
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from timeline_compiler import compile_timeline

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
PRODUCE_SCRIPTS = REPO_ROOT / "skills" / "short-drama-produce" / "scripts"
if str(PRODUCE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PRODUCE_SCRIPTS))

from production_tool import _project_file, find_project  # noqa: E402


class AssemblyError(ValueError):
    """The media tools, source assets, render, or technical checks are invalid."""


def _run(command: list[str], *, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AssemblyError(f"{label} could not complete") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise AssemblyError(f"{label} failed" + (f": {detail}" if detail else ""))
    return result


def _version(executable: str) -> str:
    result = _run([executable, "-version"], timeout=15, label="media tool version check")
    first = (result.stdout or result.stderr).splitlines()
    if not first:
        raise AssemblyError("media tool returned no version")
    return first[0][:300]


def _probe(executable: str, path: Path) -> dict[str, Any]:
    result = _run(
        [executable, "-v", "error", "-show_entries",
         "format=format_name,duration:stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,duration",
         "-of", "json", str(path)],
        timeout=60,
        label=f"media probe ({path.name})",
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssemblyError(f"media probe returned invalid JSON: {path.name}") from exc
    if not isinstance(value, Mapping) or not isinstance(value.get("streams"), list):
        raise AssemblyError(f"media probe returned invalid stream data: {path.name}")
    return dict(value)


def _duration(probe: Mapping[str, Any], label: str) -> float:
    raw = probe.get("format", {}).get("duration") if isinstance(probe.get("format"), Mapping) else None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise AssemblyError(f"media duration is missing: {label}") from exc
    if not math.isfinite(value) or value <= 0:
        raise AssemblyError(f"media duration is invalid: {label}")
    return value


def _input_stream(probe: Mapping[str, Any], media_type: str, label: str) -> Mapping[str, Any]:
    streams = [s for s in probe["streams"] if isinstance(s, Mapping) and s.get("codec_type") == media_type]
    if not streams:
        raise AssemblyError(f"{label} has no {media_type} stream")
    return streams[0]


def _ms(seconds: int) -> str:
    return f"{seconds / 1000:.3f}"


def _escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return re.sub(r"([\\':,;\[\]])", r"\\\1", value)


def _filters(edl: Mapping[str, Any], srt_path: Path | None) -> str:
    frame = edl["frame"]
    width, height = frame["width"], frame["height"]
    rate = f"{frame['fps_num']}/{frame['fps_den']}"
    rows: list[str] = []
    video_labels: list[str] = []
    for index, clip in enumerate(edl["video_clips"]):
        label = f"v{index}"
        video_labels.append(f"[{label}]")
        rows.append(
            f"[{index}:v]trim=start={_ms(clip['source_in_ms'])}:duration={_ms(clip['duration_ms'])},"
            f"setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={rate}[{label}]"
        )
    rows.append(f"{''.join(video_labels)}concat=n={len(video_labels)}:v=1:a=0[vbase]")
    video_out = "[vbase]"
    if srt_path is not None and edl["subtitle_mode"] == "burn_in":
        rows.append(f"[vbase]subtitles=filename='{_escape_filter_path(srt_path)}'[vout]")
        video_out = "[vout]"

    audio_clips = edl["audio_clips"]
    audio_labels: list[str] = []
    video_input_count = len(edl["video_clips"])
    total_seconds = _ms(edl["duration_ms"])
    for offset, clip in enumerate(audio_clips):
        index = video_input_count + offset
        label = f"a{offset}"
        audio_labels.append(f"[{label}]")
        filters = [
            f"atrim=start={_ms(clip['source_in_ms'])}:duration={_ms(clip['duration_ms'])}",
            "asetpts=PTS-STARTPTS",
            f"volume={clip['gain_db']:.6f}dB",
        ]
        if clip["fade_in_ms"]:
            filters.append(f"afade=t=in:st=0:d={_ms(clip['fade_in_ms'])}")
        if clip["fade_out_ms"]:
            fade_start = clip["duration_ms"] - clip["fade_out_ms"]
            filters.append(f"afade=t=out:st={_ms(fade_start)}:d={_ms(clip['fade_out_ms'])}")
        delay = clip["start_ms"]
        if delay:
            filters.append(f"adelay={delay}:all=1")
        rows.append(f"[{index}:a]{','.join(filters)}[{label}]")
    if audio_labels:
        rows.append(
            f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=longest:"
            f"dropout_transition=0:normalize=0,alimiter=limit=0.95,"
            f"atrim=duration={total_seconds},asetpts=PTS-STARTPTS[aout]"
        )
    rows.append(f"{video_out}null[vfinal]")
    return ";\n".join(rows) + ";\n"


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _manifest_file(root: Path, path: Path, media_type: str, *, relative: str | None = None) -> dict[str, Any]:
    digest, size = _sha256(path)
    return {
        "path": relative or path.relative_to(root).as_posix(),
        "media_type": media_type,
        "bytes": size,
        "sha256": digest,
    }


def _verify_sources(root: Path, sources: list[dict[str, Any]]) -> None:
    for source in sources:
        digest, size = _sha256(_project_file(root, source["path"]))
        if digest != source["sha256"] or size != source["bytes"]:
            raise AssemblyError(f"media source changed during assembly: {source['path']}")


def _render(
    root: Path,
    timeline_path: Path,
    output_dir: str,
    *,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    root = find_project(root).resolve()
    try:
        timeline_relative = timeline_path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise AssemblyError("timeline must be inside the project") from exc
    edl, srt, summary = compile_timeline(root, root / timeline_relative)
    episode_id = summary["episode_id"]
    parts = PurePosixPath(output_dir).parts
    if (
        PurePosixPath(output_dir).is_absolute() or ".." in parts or len(parts) != 4
        or parts[:3] != ("dist", episode_id, "assembly")
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}", parts[3]) is None
    ):
        raise AssemblyError("output directory must be dist/<EP>/assembly/<version>")
    final = root.joinpath(*parts)
    if final.exists():
        raise AssemblyError("output version already exists; choose a new version name")
    _project_file(root, (PurePosixPath(*parts[:3]) / ".render-probe").as_posix(), create_parent=True)
    if final.exists():
        raise AssemblyError("output version appeared while rendering")

    ffmpeg_version = _version(ffmpeg)
    ffprobe_version = _version(ffprobe)
    source_manifest: list[dict[str, Any]] = []
    input_paths: list[Path] = []
    for clip in edl["video_clips"] + edl["audio_clips"]:
        source = clip["source"]
        relative = source["path"]
        path = _project_file(root, relative)
        probe = _probe(ffprobe, path)
        expected_type = "video" if "transition" in clip else "audio"
        stream = _input_stream(probe, expected_type, relative)
        duration = _duration(probe, relative)
        if expected_type == "video":
            if not isinstance(stream.get("width"), int) or not isinstance(stream.get("height"), int):
                raise AssemblyError(f"video dimensions are invalid: {relative}")
        end = (clip["source_in_ms"] + clip["duration_ms"]) / 1000
        if end > duration + 0.001:
            raise AssemblyError(f"trim exceeds source duration: {relative}")
        input_paths.append(path)
        source_manifest.append({
            "job_id": source["job_id"], "run_id": source["run_id"],
            "path": relative, "media_type": source["media_type"],
            "bytes": source["bytes"], "sha256": source["sha256"],
            "duration_seconds": duration,
        })
    _verify_sources(root, source_manifest)

    temp_dir = Path(tempfile.mkdtemp(prefix=".render-", dir=final.parent))
    try:
        if srt is not None:
            (temp_dir / "subtitles.srt").write_bytes(srt)
        filter_path = temp_dir / "filter.txt"
        filter_path.write_text(_filters(edl, temp_dir / "subtitles.srt" if srt is not None else None), encoding="utf-8")
        output_name = edl["output_filename"]
        rendered = temp_dir / output_name
        command = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
        for path in input_paths:
            command.extend(["-i", str(path)])
        command.extend(["-/filter_complex", str(filter_path), "-map", "[vfinal]"])
        if edl["audio_clips"]:
            command.extend(["-map", "[aout]"])
        command.extend([
            "-t", _ms(edl["duration_ms"]), "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-r",
            f"{edl['frame']['fps_num']}/{edl['frame']['fps_den']}",
        ])
        if edl["audio_clips"]:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            command.append("-an")
        command.extend(["-movflags", "+faststart", str(rendered)])
        _run(command, timeout=3600, label="FFmpeg render")
        if not rendered.is_file() or rendered.stat().st_size == 0:
            raise AssemblyError("FFmpeg produced no master file")
        probe = _probe(ffprobe, rendered)
        video = _input_stream(probe, "video", rendered.name)
        expected_frame = edl["frame"]
        if video.get("codec_name") != "h264" or video.get("width") != expected_frame["width"] or video.get("height") != expected_frame["height"]:
            raise AssemblyError("rendered video codec or dimensions do not match the output profile")
        try:
            actual_fps = Fraction(str(video.get("avg_frame_rate", "0/1")))
        except (ValueError, ZeroDivisionError) as exc:
            raise AssemblyError("rendered frame rate is invalid") from exc
        if actual_fps != Fraction(expected_frame["fps_num"], expected_frame["fps_den"]):
            raise AssemblyError("rendered frame rate does not match the output profile")
        if edl["audio_clips"]:
            audio = _input_stream(probe, "audio", rendered.name)
            if audio.get("codec_name") != "aac":
                raise AssemblyError("rendered audio codec does not match the output profile")
        format_data = probe.get("format", {})
        if not isinstance(format_data, Mapping) or "mp4" not in str(format_data.get("format_name", "")):
            raise AssemblyError("rendered output is not recognized as an MP4 container")
        actual_duration = _duration(probe, rendered.name)
        fps = expected_frame["fps_num"] / expected_frame["fps_den"]
        tolerance = max(0.1, 2 / fps)
        expected_duration = edl["duration_ms"] / 1000
        if abs(actual_duration - expected_duration) > tolerance:
            raise AssemblyError("rendered duration differs from the approved timeline")
        _run(
            [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(rendered), "-f", "null", "-"],
            timeout=3600,
            label="rendered media decode check",
        )
        _verify_sources(root, source_manifest)

        (temp_dir / "timeline.edl.json").write_text(json.dumps(edl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_files = [_manifest_file(
            root, rendered, "video/mp4", relative=(PurePosixPath(*parts) / output_name).as_posix()
        )]
        if srt is not None:
            output_files.append(_manifest_file(
                root, temp_dir / "subtitles.srt", "application/x-subrip",
                relative=(PurePosixPath(*parts) / "subtitles.srt").as_posix(),
            ))
        output_files.append(_manifest_file(
            root, temp_dir / "timeline.edl.json", "application/json",
            relative=(PurePosixPath(*parts) / "timeline.edl.json").as_posix(),
        ))
        manifest = {
            "schema_version": "1.0",
            "episode_id": episode_id,
            "status": "assembly_succeeded",
            "quality_verdict": "not_assessed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "timeline": {
                "path": timeline_relative,
                "bytes": edl["timeline_bytes"],
                "sha256": edl["timeline_sha256"],
            },
            "tools": {"ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version},
            "render_profile": {
                "width": expected_frame["width"], "height": expected_frame["height"],
                "fps_num": expected_frame["fps_num"], "fps_den": expected_frame["fps_den"],
                "video_codec": "h264", "audio_codec": "aac" if edl["audio_clips"] else None,
                "subtitle_mode": edl["subtitle_mode"],
            },
            "technical_checks": {
                "status": "pass", "format_name": format_data.get("format_name"),
                "duration_seconds": actual_duration,
                "expected_duration_seconds": expected_duration,
                "video_stream": dict(video),
                "audio_stream_count": sum(1 for stream in probe["streams"] if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"),
            },
            "sources": source_manifest,
            "outputs": output_files,
        }
        manifest_path = temp_dir / "media-manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        filter_path.unlink(missing_ok=True)
        os.rename(temp_dir, final)
        return {
            "status": "assembly_succeeded", "episode_id": episode_id,
            "output_dir": final.relative_to(root).as_posix(),
            "master": (PurePosixPath(*parts) / output_name).as_posix(),
            "duration_ms": edl["duration_ms"], "quality_verdict": "not_assessed",
        }
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline", type=Path)
    parser.add_argument("--output-dir", required=True, help="new dist/<EP>/assembly/<version> directory")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)
    try:
        root = find_project(args.timeline)
        print(json.dumps(_render(root, args.timeline, args.output_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
