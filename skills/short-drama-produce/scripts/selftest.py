#!/usr/bin/env python3
"""Offline self-test for confirmation-gated production and provider profiles."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from production_tool import (
    AdapterError,
    ConfirmationRequiredError,
    _project_lock,
    confirm_job,
    prepare_job,
    run_job,
    _canonical,
    _job_path,
    _normalize_job,
    _publish_outputs,
    _project_file,
    _validate_stored_job,
    sha256_bytes,
)
from provider_adapters import (
    compile_gpt_image_2_payload,
    compile_minimax_music_payload,
    compile_seedance_payload,
)
from dag_scheduler import DagError, run_production_dag

MINIMUM_PYTHON = (3, 10)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("selftest.py requires Python 3.10 or newer")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        project = base / "project"
        project.mkdir()
        (project / "short-drama.json").write_text("{}\n", encoding="utf-8")
        (project / "root-alias").mkdir()
        aliased_root = project / "root-alias" / ".."
        alias_target = _project_file(
            aliased_root, "dist/EP001/path-probe.bin", create_parent=True
        )
        require(alias_target.parent.is_dir(), "project path alias was not canonicalized")
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

        episode = project / "episodes" / "EP001"
        episode.mkdir(parents=True)
        screenplay = "# EP001 测试\n\n## EP001-SC001 内 · 站台 · 深夜\n\n沈青：你好。\n"
        screenplay_bytes = screenplay.encode("utf-8")
        spoken = "沈青：你好。".encode("utf-8")
        span_start = screenplay_bytes.index(spoken)
        span_end = span_start + len(spoken)
        (episode / "剧本.md").write_bytes(screenplay_bytes)
        index_rows = [
            {"record_type": "screenplay_index_meta", "source_byte_length": len(screenplay_bytes)},
            {
                "record_type": "block", "block_id": "BLK-EP001-SC001-D01",
                "kind": "dialogue", "tag": None, "byte_start": span_start,
                "byte_end": span_end, "speaker": "沈青",
            },
        ]
        (episode / "screenplay-index.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in index_rows),
            encoding="utf-8",
        )
        voice_sheet = [
            {
                "record_type": "sources", "schema_version": "1.0.0",
                "sources": {"screenplay-index": {
                    "owner": "short-drama-write",
                    "artifact": "episodes/EP001/screenplay-index.jsonl",
                }},
            },
            {
                "line_id": "VLINE-EP001-SC001-001", "episode_id": "EP001",
                "scene_id": "EP001-SC001", "speaker": "CHAR-SHENQING",
                "speaker_display": "沈青", "line_text": "你好。",
                "source_ref": {"src": "screenplay-index", "record_id": "BLK-EP001-SC001-D01"},
                "channel": "sync",
            },
        ]
        voice_sheet_path = episode / "voice-record-sheet.jsonl"
        voice_sheet_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in voice_sheet),
            encoding="utf-8",
        )
        tts_job: dict[str, Any] = {
            "job_id": "SELFTEST-TTS-001", "modality": "tts", "adapter": "fixture",
            "prompt": "你好。", "source": "episodes/EP001/voice-record-sheet.jsonl",
            "source_entry": "VLINE-EP001-SC001-001", "references": [],
            "outputs": ["dist/EP001/audio/VLINE-EP001-SC001-001.wav"],
            "parameters": {"voice_profile": "fixture-voice"}, "overwrite": False,
        }
        for invalid_tts in (
            {**tts_job, "prompt": "您好。"},
            {**tts_job, "source_entry": "VLINE-EP002-SC001-001"},
            {key: value for key, value in tts_job.items() if key != "source_entry"},
        ):
            try:
                _normalize_job(project, invalid_tts)
            except ValueError:
                pass
            else:
                raise AssertionError("unbound or divergent TTS line was accepted")
        tts_job_path = project / "tts-job.json"
        tts_job_path.write_text(json.dumps(tts_job, ensure_ascii=False), encoding="utf-8")
        tts_preview = prepare_job(project, tts_job_path)
        require(
            tts_preview["source_entry"] == "VLINE-EP001-SC001-001",
            "selected voice line was omitted from the confirmation preview",
        )
        confirm_job(project, job_id=tts_job["job_id"], confirmation=tts_preview["confirmation"])
        tts_result = run_job(project, job_id=tts_job["job_id"], adapter_config=config)
        require(tts_result["state"] == "succeeded", "fixture TTS job did not run")

        changed_tts_job = {**tts_job, "job_id": "SELFTEST-TTS-002",
                           "outputs": ["dist/EP001/audio/VLINE-EP001-SC001-002.wav"]}
        changed_tts_path = project / "tts-job-changed.json"
        changed_tts_path.write_text(
            json.dumps(changed_tts_job, ensure_ascii=False), encoding="utf-8"
        )
        changed_preview = prepare_job(project, changed_tts_path)
        stored_tts = json.loads(
            _job_path(project, changed_tts_job["job_id"]).read_text(encoding="utf-8")
        )
        require(
            set(stored_tts["inputs"]) == {
                "episodes/EP001/voice-record-sheet.jsonl",
                "episodes/EP001/screenplay-index.jsonl",
                "episodes/EP001/剧本.md",
            },
            "TTS confirmation did not pin the voice sheet and its screenplay projection inputs",
        )
        confirm_job(
            project, job_id=changed_tts_job["job_id"],
            confirmation=changed_preview["confirmation"],
        )
        screenplay_path = episode / "剧本.md"
        original_screenplay = screenplay_path.read_bytes()
        screenplay_path.write_bytes(original_screenplay + b"\n")
        try:
            run_job(project, job_id=changed_tts_job["job_id"], adapter_config=config)
        except (ValueError, ConfirmationRequiredError):
            pass
        else:
            raise AssertionError("TTS run accepted a screenplay changed after confirmation")
        finally:
            screenplay_path.write_bytes(original_screenplay)

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

        partial_adapter = base / "partial_adapter.py"
        partial_adapter.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "job = json.load(sys.stdin)\n"
            "root = Path(job['output_root'])\n"
            "first = root / 'first.png'\n"
            "first.write_bytes(b'new-first')\n"
            "outputs = [{'target': job['outputs'][0], 'source': str(first)}]\n"
            "if job['parameters'].get('race_second_output'):\n"
            "    second = root / 'second.png'\n"
            "    second.write_bytes(b'new-second')\n"
            "    appeared = Path(job['outputs'][1])\n"
            "    appeared.parent.mkdir(parents=True, exist_ok=True)\n"
            "    appeared.write_bytes(b'concurrent-version')\n"
            "    outputs.append({'target': job['outputs'][1], 'source': str(second)})\n"
            "else:\n"
            "    outputs.append({'target': job['outputs'][1], 'source': str(root / 'missing.png')})\n"
            "json.dump({'outputs': outputs}, sys.stdout)\n",
            encoding="utf-8",
        )
        partial_config = base / "partial-adapter.json"
        partial_config.write_text(
            json.dumps({"adapters": {"partial": {
                "command": [sys.executable, str(partial_adapter)],
                "timeout_seconds": 30,
            }}}),
            encoding="utf-8",
        )
        for overwrite in (False, True):
            outputs = [
                f"dist/EP001/partial/{'overwrite' if overwrite else 'new'}-{index}.png"
                for index in (1, 2)
            ]
            if overwrite:
                for output in outputs:
                    target = project / output
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"previous-version")
            partial_job = {
                "job_id": f"SELFTEST-PARTIAL-{int(overwrite)}",
                "modality": "image",
                "adapter": "partial",
                "prompt": "two output fixture",
                "references": [],
                "outputs": outputs,
                "parameters": {},
                "overwrite": overwrite,
            }
            partial_job_file = base / f"partial-{int(overwrite)}.json"
            partial_job_file.write_text(json.dumps(partial_job), encoding="utf-8")
            partial_preview = prepare_job(project, partial_job_file)
            confirm_job(
                project,
                job_id=partial_job["job_id"],
                confirmation=partial_preview["confirmation"],
            )
            try:
                run_job(
                    project,
                    job_id=partial_job["job_id"],
                    adapter_config=partial_config,
                )
            except AdapterError:
                pass
            else:
                raise AssertionError("incomplete multi-output adapter run succeeded")
            expected = b"previous-version" if overwrite else None
            require(
                all(
                    (project / output).read_bytes() == expected
                    if expected is not None
                    else not (project / output).exists()
                    for output in outputs
                ),
                "failed multi-output run left partial or overwritten production outputs",
            )

        race_outputs = [
            "dist/EP001/partial/race-1.png",
            "dist/EP001/partial/race-2.png",
        ]
        race_job = {
            "job_id": "SELFTEST-PARTIAL-RACE",
            "modality": "image",
            "adapter": "partial",
            "prompt": "publish race fixture",
            "references": [],
            "outputs": race_outputs,
            "parameters": {"race_second_output": True},
            "overwrite": False,
        }
        race_job_file = base / "partial-race.json"
        race_job_file.write_text(json.dumps(race_job), encoding="utf-8")
        race_preview = prepare_job(project, race_job_file)
        confirm_job(
            project,
            job_id=race_job["job_id"],
            confirmation=race_preview["confirmation"],
        )
        try:
            run_job(
                project,
                job_id=race_job["job_id"],
                adapter_config=partial_config,
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("output appearing during publish was overwritten")
        require(
            not (project / race_outputs[0]).exists()
            and (project / race_outputs[1]).read_bytes() == b"concurrent-version",
            "multi-output rollback removed or retained the wrong files",
        )

        rollback_outputs = [
            "dist/EP001/partial/rollback-1.png",
            "dist/EP001/partial/rollback-2.png",
        ]
        rollback_sources = [base / "rollback-1.png", base / "rollback-2.png"]
        for output, source in zip(rollback_outputs, rollback_sources):
            target = project / output
            target.write_bytes(b"previous-version")
            source.write_bytes(b"replacement-version")
        real_replace = os.replace
        second_target = project / rollback_outputs[1]

        def fail_second_publish(source: str | Path, target: str | Path) -> None:
            if Path(target) == second_target and Path(source).suffix == ".tmp":
                raise PermissionError("simulated second output publish failure")
            real_replace(source, target)

        try:
            with patch("production_tool.os.replace", side_effect=fail_second_publish):
                _publish_outputs(
                    project,
                    list(zip(rollback_outputs, rollback_sources)),
                    overwrite=True,
                )
        except PermissionError:
            pass
        else:
            raise AssertionError("failed multi-output publish was accepted")
        require(
            all((project / output).read_bytes() == b"previous-version" for output in rollback_outputs),
            "failed overwrite publish did not restore the previous output set",
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

        dag_project = base / "dag-project"
        dag_project.mkdir()
        (dag_project / "short-drama.json").write_text("{}\n", encoding="utf-8")
        (dag_project / "episodes").mkdir()
        dag_config = base / "dag-adapters.json"
        dag_config.write_text(
            json.dumps({"adapters": {"fixture": {"command": [sys.executable, str(fixture)]}}}),
            encoding="utf-8",
        )
        graph = {
            "nodes": [
                {"shot_id": "SHOT-EP001-002", "start_frame_source": "CHAIN_PREV", "parent_shot_id": "SHOT-EP001-001"},
                {"shot_id": "SHOT-EP001-001", "start_frame_source": "ASSET", "parent_shot_id": None},
            ],
            "dependencies": {"SHOT-EP001-002": "SHOT-EP001-001"},
            "topological_order": ["SHOT-EP001-001", "SHOT-EP001-002"],
        }
        def plan(job_id: str, shot_id: str, *, chained: bool = False) -> dict[str, Any]:
            job = {
                "job_id": job_id,
                "modality": "video",
                "adapter": "fixture",
                "prompt": "offline DAG self-test",
                "references": [],
                "outputs": [f"dist/EP001/video/{shot_id}.mp4"],
                "parameters": {"duration": 5},
                "overwrite": False,
            }
            if chained:
                job.update({"start_frame_source": "CHAIN_PREV", "parent_shot_id": "SHOT-EP001-001"})
            return {"job": job, "estimated_cost": {"amount": "0", "currency": "USD", "basis": "offline fixture"}}
        plans = {
            "SHOT-EP001-001": plan("DAG-PARENT", "SHOT-EP001-001"),
            "SHOT-EP001-002": plan("DAG-CHILD", "SHOT-EP001-002", chained=True),
        }
        accepted: list[str] = []
        result = run_production_dag(
            dag_project,
            graph,
            plans,
            dag_config,
            request_confirmation=lambda preview: accepted.append(preview["job_id"]) or preview["confirmation"],
            extract_frame=lambda video, output: output.parent.mkdir(parents=True, exist_ok=True) or output.write_bytes(png_header + b"tail"),
        )
        require(result["completed"] == graph["topological_order"], "DAG did not respect parent-first order")
        require(accepted == ["DAG-PARENT", "DAG-CHILD"], "DAG did not request confirmation per shot")
        require(result["results"]["SHOT-EP001-002"]["state"] == "succeeded", "chained fixture shot failed")
        cancelled: list[str] = []
        def cancel_child(preview: dict[str, Any]) -> str:
            cancelled.append(preview["job_id"])
            return preview["confirmation"] if preview["job_id"] == "DAG-CANCEL" else "cancel"
        try:
            run_production_dag(
                dag_project,
                graph,
                {
                    "SHOT-EP001-001": plan("DAG-CANCEL", "SHOT-EP001-CANCEL"),
                    "SHOT-EP001-002": plan(
                        "DAG-CANCEL-CHILD", "SHOT-EP001-CANCEL-CHILD", chained=True
                    ),
                },
                dag_config,
                request_confirmation=cancel_child,
                extract_frame=lambda video, output: output.parent.mkdir(parents=True, exist_ok=True) or output.write_bytes(png_header + b"cancelled-tail"),
            )
        except DagError:
            pass
        else:
            raise AssertionError("DAG accepted a non-matching confirmation")
        require(cancelled == ["DAG-CANCEL", "DAG-CANCEL-CHILD"], "DAG did not stop at the cancelled child")
        retry_result = run_production_dag(
            dag_project,
            graph,
            {"SHOT-EP001-001": plan("DAG-RETRY", "SHOT-EP001-RETRY"), "SHOT-EP001-002": plan("DAG-RETRY-CHILD", "SHOT-EP001-RETRY-CHILD", chained=True)},
            dag_config,
            request_confirmation=lambda preview: preview["confirmation"],
            extract_frame=lambda video, output: output.parent.mkdir(parents=True, exist_ok=True) or output.write_bytes(png_header + b"retry-tail"),
        )
        require(retry_result["state"] == "succeeded", "DAG retry failed after a cancelled child")
        failing_config = base / "dag-failing-adapters.json"
        failing_config.write_text(
            json.dumps({"adapters": {"fixture": {"command": [sys.executable, str(fixture), "--fail"]}}}),
            encoding="utf-8",
        )
        attempted: list[str] = []
        try:
            run_production_dag(
                dag_project,
                graph,
                {"SHOT-EP001-001": plan("DAG-FAIL", "SHOT-EP001-FAIL"), "SHOT-EP001-002": plan("DAG-FAIL-CHILD", "SHOT-EP001-CHILD", chained=True)},
                failing_config,
                request_confirmation=lambda preview: attempted.append(preview["job_id"]) or preview["confirmation"],
                extract_frame=lambda video, output: output,
            )
        except AdapterError:
            pass
        else:
            raise AssertionError("DAG continued after a failed parent shot")
        require(attempted == ["DAG-FAIL"], "child shot was prepared after its parent failed")

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

    print("production and DAG self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
