#!/usr/bin/env python3
"""Offline checks for timeline provenance, voice projection, and compilation."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MINIMUM_PYTHON = (3, 10)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("short-drama-edit needs Python 3.10 or newer")

PRODUCE_SCRIPTS = Path(__file__).resolve().parents[2] / "short-drama-produce" / "scripts"
if str(PRODUCE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PRODUCE_SCRIPTS))

from production_tool import _job_key, _write_run, confirm_job, prepare_job, run_job  # noqa: E402
import media_assembler  # noqa: E402
from media_assembler import AssemblyError, _render  # noqa: E402
from timeline_compiler import TimelineError, compile_timeline, write_compilation  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def create_media_job(
    project: Path,
    temp: Path,
    adapter_config: Path,
    job_id: str,
    modality: str,
    output: str,
) -> dict[str, Any]:
    raw_path = temp / f"{job_id}.json"
    raw_path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "modality": modality,
                "adapter": "fixture",
                "prompt": "offline edit compiler self-test",
                "references": [],
                "outputs": [output],
                "parameters": {"duration": 5} if modality == "video" else {},
                "overwrite": False,
            }
        ),
        encoding="utf-8",
    )
    preview = prepare_job(project, raw_path)
    confirm_job(project, job_id=job_id, confirmation=preview["confirmation"])
    return run_job(project, job_id=job_id, adapter_config=adapter_config)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        project = temp / "漫剧项目"
        episode = project / "episodes" / "EP001"
        episode.mkdir(parents=True)
        (project / "short-drama.json").write_text("{}\n", encoding="utf-8")
        fixture = PRODUCE_SCRIPTS / "fixture_adapter.py"
        adapter_config = temp / "adapters.json"
        adapter_config.write_text(
            json.dumps(
                {"adapters": {"fixture": {"command": [sys.executable, str(fixture)]}}}
            ),
            encoding="utf-8",
        )
        video_result = create_media_job(
            project,
            temp,
            adapter_config,
            "EDIT-VIDEO-001",
            "video",
            "dist/EP001/video/shot.mp4",
        )
        audio_result = create_media_job(
            project,
            temp,
            adapter_config,
            "EDIT-TTS-001",
            "tts",
            "dist/EP001/audio/line.wav",
        )
        music_result = create_media_job(
            project,
            temp,
            adapter_config,
            "EDIT-MUSIC-001",
            "music",
            "dist/EP001/audio/music.wav",
        )

        screenplay = "# EP001 测试\n\n## EP001-SC001 内 · 站台 · 深夜\n\n沈青：你好。\n"
        screenplay_bytes = screenplay.encode("utf-8")
        (episode / "剧本.md").write_bytes(screenplay_bytes)
        block = "沈青：你好。".encode("utf-8")
        start = screenplay_bytes.index(block)
        end = start + len(block)
        index = [
            {"record_type": "screenplay_index_meta", "source_byte_length": len(screenplay_bytes)},
            {
                "record_type": "block",
                "block_id": "BLK-EP001-SC001-D01",
                "kind": "dialogue",
                "tag": None,
                "byte_start": start,
                "byte_end": end,
                "speaker": "沈青",
            },
        ]
        (episode / "screenplay-index.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in index),
            encoding="utf-8",
        )
        sheet = [
            {
                "record_type": "sources",
                "schema_version": "1.0.0",
                "sources": {
                    "screenplay-index": {
                        "owner": "short-drama-write",
                        "artifact": "episodes/EP001/screenplay-index.jsonl",
                    }
                },
            },
            {
                "line_id": "VLINE-EP001-SC001-001",
                "episode_id": "EP001",
                "scene_id": "EP001-SC001",
                "speaker": "CHAR-SHENQING",
                "speaker_display": "沈青",
                "line_text": "你好。",
                "source_ref": {"src": "screenplay-index", "record_id": "BLK-EP001-SC001-D01"},
                "channel": "sync",
            },
        ]
        (episode / "voice-record-sheet.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in sheet),
            encoding="utf-8",
        )

        video_output = video_result["outputs"][0]
        audio_output = audio_result["outputs"][0]
        music_output = music_result["outputs"][0]
        timeline = {
            "schema_version": "1.0",
            "episode_id": "EP001",
            "review_status": "approved",
            "frame": {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1},
            "subtitle_mode": "sidecar",
            "voice_source": {
                "sheet": "episodes/EP001/voice-record-sheet.jsonl",
                "index": "episodes/EP001/screenplay-index.jsonl",
                "screenplay": "episodes/EP001/剧本.md",
            },
            "video_clips": [
                {
                    "clip_id": "CLIP-EP001-001",
                    "source": {"job_id": video_result["job_id"], "run_id": video_result["run_id"], "path": video_output["path"]},
                    "source_in_ms": 0,
                    "duration_ms": 4000,
                }
            ],
            "audio_clips": [
                {
                    "clip_id": "AUDIO-EP001-001",
                    "role": "dialogue",
                    "voice_line_id": "VLINE-EP001-SC001-001",
                    "source": {"job_id": audio_result["job_id"], "run_id": audio_result["run_id"], "path": audio_output["path"]},
                    "source_in_ms": 0,
                    "timeline_start_ms": 1000,
                    "duration_ms": 1000,
                    "gain_db": 0,
                    "fade_in_ms": 0,
                    "fade_out_ms": 0,
                },
                {
                    "clip_id": "AUDIO-EP001-MUSIC",
                    "role": "music",
                    "source": {"job_id": music_result["job_id"], "run_id": music_result["run_id"], "path": music_output["path"]},
                    "source_in_ms": 0,
                    "timeline_start_ms": 0,
                    "duration_ms": 4000,
                    "gain_db": -18,
                    "fade_in_ms": 300,
                    "fade_out_ms": 400,
                }
            ],
            "captions": [
                {"voice_line_id": "VLINE-EP001-SC001-001", "timeline_start_ms": 1000, "duration_ms": 1000}
            ],
            "output": {"filename": "EP001-master.mp4"},
        }
        timeline_path = episode / "成片编排.md"
        timeline_path.write_text(
            "# EP001 成片编排\n\n```json\n"
            + json.dumps(timeline, ensure_ascii=False, indent=2)
            + "\n```\n",
            encoding="utf-8",
        )
        edl, srt, summary = compile_timeline(project, timeline_path)
        require(summary["status"] == "pass", "valid timeline failed")
        require(summary["audio_clip_count"] == 2, "dialogue and music did not both compile")
        require(edl["quality_verdict"] == "not_assessed", "compiler overstated media quality")
        require(edl["voice_sources"]["sheet"]["sha256"], "voice sheet provenance was omitted")
        require(srt is not None and "你好。" in srt.decode("utf-8-sig"), "subtitle was not projected from voice sheet")
        require(edl["video_clips"][0]["end_ms"] == 4000, "video timing was not compiled")
        output = write_compilation(project, "EP001", "dist/EP001/assembly/v001", edl, srt)
        require((output / "timeline.edl.json").is_file(), "EDL was not written")
        require((output / "subtitles.srt").is_file(), "SRT was not written")
        try:
            write_compilation(project, "EP001", "dist/EP001/assembly/v001", edl, srt)
        except TimelineError:
            pass
        else:
            raise AssertionError("compiler overwrote an existing assembly version")

        original_run = media_assembler._run
        original_probe = media_assembler._probe
        captured: dict[str, Any] = {}

        def fake_run(command: list[str], *, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
            if label == "FFmpeg render":
                filter_index = command.index("-/filter_complex") + 1
                captured["filter"] = Path(command[filter_index]).read_text(encoding="utf-8")
                Path(command[-1]).write_bytes(b"synthetic mp4")
            return subprocess.CompletedProcess(command, 0, "fixture tool version\n", "")

        def fake_probe(executable: str, path: Path) -> dict[str, Any]:
            if path.name == "EP001-master.mp4":
                return {
                    "format": {"format_name": "mov,mp4,m4a", "duration": "4.0"},
                    "streams": [
                        {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                }
            durations = {"shot.mp4": "4.0", "line.wav": "1.0", "music.wav": "4.0"}
            media_type = "video" if path.name == "shot.mp4" else "audio"
            stream = {"codec_type": media_type, "codec_name": "fixture"}
            if media_type == "video":
                stream.update({"width": 1920, "height": 1080})
            return {"format": {"duration": durations[path.name]}, "streams": [stream]}

        try:
            media_assembler._run = fake_run
            media_assembler._probe = fake_probe
            assembled = _render(
                project, timeline_path, "dist/EP001/assembly/v002",
                ffmpeg="fixture-ffmpeg", ffprobe="fixture-ffprobe",
            )
            require(assembled["status"] == "assembly_succeeded", "fixture assembly failed")
            require("concat=n=1:v=1:a=0" in captured["filter"], "video clips were not assembled in order")
            require("adelay=1000:all=1" in captured["filter"], "dialogue placement was not rendered")
            assembly = project / "dist/EP001/assembly/v002"
            manifest = json.loads((assembly / "media-manifest.json").read_text(encoding="utf-8"))
            require(manifest["quality_verdict"] == "not_assessed", "renderer overstated media quality")
            require(all(".render-" not in item["path"] for item in manifest["outputs"]), "manifest exposed temporary paths")
            require(not (assembly / "filter.txt").exists(), "temporary filter script leaked into delivery")
            try:
                _render(project, timeline_path, "dist/EP001/assembly/v002", ffmpeg="fixture-ffmpeg", ffprobe="fixture-ffprobe")
            except AssemblyError as exc:
                require("already exists" in str(exc), "existing version failed for the wrong reason")
            else:
                raise AssertionError("renderer overwrote an existing version")

            def fail_render(command: list[str], *, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
                if label == "FFmpeg render":
                    raise AssemblyError("fixture render failure")
                return subprocess.CompletedProcess(command, 0, "fixture tool version\n", "")

            media_assembler._run = fail_render
            try:
                _render(project, timeline_path, "dist/EP001/assembly/v003", ffmpeg="fixture-ffmpeg", ffprobe="fixture-ffprobe")
            except AssemblyError:
                pass
            else:
                raise AssertionError("renderer accepted a failed FFmpeg execution")
            parent = project / "dist/EP001/assembly"
            require(not (parent / "v003").exists(), "failed render published a version")
            require(not any(item.name.startswith(".render-") for item in parent.iterdir()), "failed render left a staging directory")
        finally:
            media_assembler._run = original_run
            media_assembler._probe = original_probe

        ffmpeg = os.environ.get("AIGC_DRAMA_FFMPEG") or shutil.which("ffmpeg")
        ffprobe = os.environ.get("AIGC_DRAMA_FFPROBE") or shutil.which("ffprobe")
        if ffmpeg and ffprobe:
            def make_real_output(result: dict[str, Any], media_type: str, duration: int) -> None:
                output_record = result["outputs"][0]
                target = project / output_record["path"]
                temp_output = temp / f"{result['job_id']}-real{target.suffix}"
                if media_type == "video":
                    command = [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c=blue:s=640x360:r=30:d={duration}", "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(duration), str(temp_output),
                    ]
                else:
                    command = [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={duration}", "-c:a", "pcm_s16le",
                        "-t", str(duration), str(temp_output),
                    ]
                created = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=60, check=False,
                )
                require(created.returncode == 0, "FFmpeg could not create synthetic source media")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(temp_output, target)
                run_record = json.loads((project / ".short-drama" / "production" / "runs" / _job_key(result["job_id"]) / f"{result['run_id']}.json").read_text(encoding="utf-8"))
                run_record["outputs"][0].update({
                    "bytes": target.stat().st_size,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                })
                _write_run(project, result["job_id"], run_record)

            make_real_output(video_result, "video", 4)
            make_real_output(audio_result, "audio", 2)
            make_real_output(music_result, "audio", 4)
            real = _render(
                project, timeline_path, "dist/EP001/assembly/v003", ffmpeg=ffmpeg, ffprobe=ffprobe
            )
            require(real["status"] == "assembly_succeeded", "real FFmpeg assembly failed")
            real_manifest = json.loads((project / "dist/EP001/assembly/v003/media-manifest.json").read_text(encoding="utf-8"))
            require(real_manifest["technical_checks"]["status"] == "pass", "real media probe did not pass")
            require(real_manifest["technical_checks"]["audio_stream_count"] == 1, "real output is missing its mixed audio stream")
            timeline["subtitle_mode"] = "burn_in"
            timeline_path.write_text(
                "# EP001 成片编排\n\n```json\n" + json.dumps(timeline, ensure_ascii=False, indent=2) + "\n```\n",
                encoding="utf-8",
            )
            burned = _render(
                project, timeline_path, "dist/EP001/assembly/v004", ffmpeg=ffmpeg, ffprobe=ffprobe
            )
            require(burned["status"] == "assembly_succeeded", "real burn-in subtitle render failed")
            burned_manifest = json.loads((project / "dist/EP001/assembly/v004/media-manifest.json").read_text(encoding="utf-8"))
            require(burned_manifest["render_profile"]["subtitle_mode"] == "burn_in", "subtitle render mode was not recorded")
            timeline["subtitle_mode"] = "sidecar"
            timeline_path.write_text(
                "# EP001 成片编排\n\n```json\n" + json.dumps(timeline, ensure_ascii=False, indent=2) + "\n```\n",
                encoding="utf-8",
            )

        video_job_file = temp / "EDIT-VIDEO-001.json"
        original_job = json.loads(video_job_file.read_text(encoding="utf-8"))
        changed_job = {**original_job, "prompt": "a newer but not rerun prompt"}
        video_job_file.write_text(json.dumps(changed_job), encoding="utf-8")
        prepare_job(project, video_job_file)
        try:
            compile_timeline(project, timeline_path)
        except TimelineError as exc:
            require("older job specification" in str(exc), "stale run failed for the wrong reason")
        else:
            raise AssertionError("compiler accepted a run from an older job fingerprint")
        video_job_file.write_text(json.dumps(original_job), encoding="utf-8")
        prepare_job(project, video_job_file)

        media_path = project / video_output["path"]
        media_path.write_bytes(media_path.read_bytes() + b"tampered")
        try:
            compile_timeline(project, timeline_path)
        except TimelineError as exc:
            require("bytes changed" in str(exc), "changed media failed for the wrong reason")
        else:
            raise AssertionError("compiler accepted media changed after its run")

    if ffmpeg and ffprobe:
        print("timeline compiler and real FFmpeg assembly self-test passed")
    else:
        print("timeline compiler and fixture assembly self-test passed (FFmpeg integration skipped: binaries unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
