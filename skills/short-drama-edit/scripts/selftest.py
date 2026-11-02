#!/usr/bin/env python3
"""Offline checks for timeline provenance, voice projection, and compilation."""

from __future__ import annotations

import json
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

from production_tool import confirm_job, prepare_job, run_job  # noqa: E402
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
        project = temp / "project"
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

    print("timeline compiler self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
