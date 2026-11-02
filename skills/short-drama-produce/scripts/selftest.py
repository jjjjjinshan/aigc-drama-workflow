#!/usr/bin/env python3
"""Offline self-test for confirmation-gated production and provider profiles."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from production_tool import (
    ConfirmationRequiredError,
    _project_lock,
    confirm_job,
    prepare_job,
    run_job,
    _canonical,
    _job_path,
    _normalize_job,
    _validate_stored_job,
    sha256_bytes,
)
from provider_adapters import (
    compile_gpt_image_2_payload,
    compile_minimax_music_payload,
    compile_seedance_payload,
)

MINIMUM_PYTHON = (3, 10)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("selftest.py requires Python 3.9 or newer")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        project = base / "project"
        project.mkdir()
        (project / "short-drama.json").write_text("{}\n", encoding="utf-8")
        production_metadata = project / ".short-drama" / "production"
        production_metadata.mkdir(parents=True)
        external_lock = base / "external-lock-target"
        external_lock.write_bytes(b"")
        os.link(external_lock, production_metadata / "lock")
        try:
            with _project_lock(project):
                pass
        except ValueError:
            pass
        else:
            raise AssertionError("production accepted a hard-linked lock file")
        require(
            external_lock.read_bytes() == b"",
            "production lock initialization modified an external hard-linked file",
        )
        (production_metadata / "lock").unlink()
        job_path = project / "music-job.json"
        job: dict[str, Any] = {
            "job_id": "SELFTEST-MUSIC-001",
            "modality": "music",
            "adapter": "fixture",
            "prompt": "restrained instrumental tension",
            "references": [],
            "outputs": ["dist/EP001/music/cue.wav"],
            "parameters": {"is_instrumental": True},
            "overwrite": False,
        }
        job_path.write_text(json.dumps(job), encoding="utf-8")
        config = base / "adapters.json"
        fixture = Path(__file__).with_name("fixture_adapter.py")
        config.write_text(
            json.dumps(
                {
                    "adapters": {
                        "fixture": {
                            "command": [sys.executable, str(fixture)],
                            "timeout_seconds": 30,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        preview = prepare_job(project, job_path)
        stored = json.loads(
            _job_path(project, job["job_id"]).read_text(encoding="utf-8")
        )
        for key in ("start_frame_source", "parent_shot_id", "head_frame"):
            stored.pop(key)
        stored["fingerprint"] = sha256_bytes(
            _canonical(
                {
                    key: value
                    for key, value in stored.items()
                    if key not in {"fingerprint", "prepared_at"}
                }
            )
        )
        legacy_job = _validate_stored_job(
            project, stored, expected_job_id=job["job_id"]
        )
        require(
            legacy_job["start_frame_source"] == "ASSET"
            and legacy_job["head_frame"] is None,
            "pre-chain stored job was not upgraded to the ASSET defaults",
        )
        oldest_job = dict(stored)
        oldest_job.pop("source_entry")
        oldest_job.pop("reference_bindings")
        oldest_job["fingerprint"] = sha256_bytes(
            _canonical(
                {
                    key: value
                    for key, value in oldest_job.items()
                    if key not in {"fingerprint", "prepared_at"}
                }
            )
        )
        _validate_stored_job(project, oldest_job, expected_job_id=job["job_id"])
        try:
            confirm_job(project, job_id=job["job_id"], confirmation="CONFIRM wrong")
        except ConfirmationRequiredError:
            pass
        else:
            raise AssertionError("a mismatched production confirmation was accepted")
        confirm_job(
            project,
            job_id=job["job_id"],
            confirmation=preview["confirmation"],
        )
        result = run_job(project, job_id=job["job_id"], adapter_config=config)
        require(result["state"] == "succeeded", "fixture production did not succeed")
        require(
            (project / job["outputs"][0]).read_bytes().startswith(b"RIFF"),
            "fixture production did not write WAV media",
        )

        head_frame = project / "dist" / "EP001" / "video" / "parent-tail.png"
        head_frame.parent.mkdir(parents=True, exist_ok=True)
        png_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        head_frame.write_bytes(png_header + b"parent-frame-v1")
        bad_frame = head_frame.with_name("invalid.png")
        bad_frame.write_bytes(b"not a PNG")
        chain_job: dict[str, Any] = {
            "job_id": "SELFTEST-CHAIN-002",
            "modality": "video",
            "adapter": "fixture",
            "prompt": "continue the accepted shot",
            "references": [],
            "outputs": ["dist/EP001/video/child.mp4"],
            "parameters": {"duration": 5},
            "overwrite": False,
            "start_frame_source": "CHAIN_PREV",
            "parent_shot_id": "SHOT-EP001-001",
            "head_frame": "dist/EP001/video/parent-tail.png",
        }
        for invalid_job in (
            {**chain_job, "parent_shot_id": None},
            {**chain_job, "modality": "music"},
            {**job, "head_frame": chain_job["head_frame"]},
            {**chain_job, "head_frame": "dist/EP001/video/invalid.png"},
        ):
            try:
                _normalize_job(project, invalid_job)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid frame-source contract was accepted")
        chain_path = project / "chain-job.json"
        chain_path.write_text(json.dumps(chain_job), encoding="utf-8")
        chain_preview = prepare_job(project, chain_path)
        require(
            chain_preview["head_frame"] == chain_job["head_frame"]
            and chain_preview["parent_shot_id"] == chain_job["parent_shot_id"],
            "chain input was omitted from the confirmation preview",
        )
        confirm_job(
            project,
            job_id=chain_job["job_id"],
            confirmation=chain_preview["confirmation"],
        )
        head_frame.write_bytes(png_header + b"parent-frame-v2")
        try:
            run_job(project, job_id=chain_job["job_id"], adapter_config=config)
        except ConfirmationRequiredError:
            pass
        else:
            raise AssertionError("changed head frame did not invalidate confirmation")

        chain_preview = prepare_job(project, chain_path)
        confirm_job(
            project,
            job_id=chain_job["job_id"],
            confirmation=chain_preview["confirmation"],
        )
        chain_result = run_job(
            project, job_id=chain_job["job_id"], adapter_config=config
        )
        chain_output = (project / chain_job["outputs"][0]).read_bytes()
        require(
            chain_result["state"] == "succeeded"
            and chain_output.startswith(b"\x00\x00\x00\x18ftyp"),
            "fixture did not execute the confirmed CHAIN_PREV job",
        )

    require(
        compile_gpt_image_2_payload(
        {
            "modality": "image",
            "prompt": "portrait",
            "references": [],
            "outputs": ["dist/EP001/portrait.png"],
            "parameters": {"size": "1024x1024"},
        }
        )["model"]
        == "gpt-image-2",
        "GPT Image 2 profile compiled the wrong model",
    )
    require(
        compile_seedance_payload(
        {
            "modality": "video",
            "prompt": "slow push in",
            "references": [],
            "outputs": ["dist/EP001/shot.mp4"],
            "parameters": {"duration": 5, "ratio": "9:16"},
        },
        model="configured-endpoint",
        allowed_ratios={"9:16"},
        duration_range=(5, 10),
        )["content"][0]["text"].endswith("--ratio 9:16 --dur 5"),
        "Seedance profile did not compile explicit prompt switches",
    )
    require(
        compile_minimax_music_payload(
        {
            "modality": "music",
            "prompt": "restrained score",
            "references": [],
            "outputs": ["dist/EP001/cue.mp3"],
            "parameters": {"is_instrumental": True},
        }
        )["model"]
        == "music-3.0",
        "MiniMax Music profile compiled the wrong model",
    )

    print("16 self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
