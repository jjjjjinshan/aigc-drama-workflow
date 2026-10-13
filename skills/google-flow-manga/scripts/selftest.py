#!/usr/bin/env python3
"""Small offline regression test for validate_flow_package.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

from validate_flow_package import validate_package


def valid_package(asset_path: str) -> dict:
    state = {
        "who": ["CHAR-001"],
        "where": "LOC-001",
        "what": [],
        "when": "night-1",
        "screen_geography": "character screen-left, looking right",
        "story_phase": "before revelation",
    }
    return {
        "schema_version": "1.0",
        "project_id": "demo",
        "episode_id": "EP001",
        "package_status": "ready_for_flow",
        "aspect_ratio": "9:16",
        "source_documents": ["storyboard.md"],
        "style_lock": {"summary": "inked motion comic", "palette": [], "texture": [], "negative_rules": []},
        "assets": [
            {
                "asset_id": "ASSET-CHAR-001",
                "kind": "character",
                "approved": True,
                "path": asset_path,
                "flow_asset_name": None,
                "usage": "ingredient",
            }
        ],
        "characters": [
            {
                "character_id": "CHAR-001",
                "display_name": "Lin Xia",
                "appearance_lock": {"hair": "black bob", "costume": "dark coat"},
                "avoid": [],
                "reference_asset_ids": ["ASSET-CHAR-001"],
            }
        ],
        "locations": [
            {
                "location_id": "LOC-001",
                "display_name": "office",
                "state_lock": {"time": "night"},
                "reference_asset_ids": [],
            }
        ],
        "shots": [
            {
                "shot_id": "EP001-S001-SH001",
                "source_scene_id": "EP001-S001",
                "purpose": "reveal unease",
                "duration_seconds": 6,
                "generation_mode": "ingredients",
                "character_ids": ["CHAR-001"],
                "location_id": "LOC-001",
                "reference_asset_ids": ["ASSET-CHAR-001"],
                "start_frame_asset_id": None,
                "end_frame_asset_id": None,
                "input_video_asset_id": None,
                "voice_reference_asset_id": None,
                "prompt": "Lin Xia pauses; slow push-in; fluorescent hum.",
                "negative_rules": [],
                "start_state": dict(state),
                "end_state": dict(state),
                "continuity_delta": {"changed": [], "why_changed": ""},
                "continuity_bridge": "",
                "camera": {"shot_size": "medium", "height": "eye", "movement": "push-in", "axis": "hold", "focus_order": []},
                "dialogue": {"speaker": "", "text": "", "performance": ""},
                "output": {"clip_path": None, "flow_asset_name": None, "status": "not_generated", "review_status": "pending"},
            }
        ],
        "approval": {"batch_id": "BATCH-001", "creator_confirmed": True, "confirmed_at": "2026-08-26T10:00:00+08:00", "notes": "test"},
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="google-flow-manga-") as temp_dir:
        root = Path(temp_dir)
        image = root / "character.png"
        image.write_bytes(b"test")
        package_path = root / "flow-package.json"

        payload = valid_package(image.name)
        report = validate_package(payload, package_path, check_paths=True)
        assert report["status"] == "ok", report

        payload["shots"][0]["generation_mode"] = "frames"
        broken = validate_package(payload, package_path, check_paths=True)
        codes = {item["code"] for item in broken["errors"]}
        assert "frames_without_start" in codes, broken

        payload = valid_package(image.name)
        payload["shots"][0]["continuity_delta"] = {"changed": ["what"], "why_changed": ""}
        broken = validate_package(payload, package_path, check_paths=True)
        codes = {item["code"] for item in broken["errors"]}
        assert "unexplained_state_change" in codes, broken

    print("PASS: valid package accepted; invalid Flow mode and continuity delta rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
