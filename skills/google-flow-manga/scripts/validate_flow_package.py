#!/usr/bin/env python3
"""Validate a local Google Flow manga production package.

This validator is intentionally deterministic and offline. It does not call
Google Flow, inspect account capabilities, or spend generation credits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_PACKAGE_STATUSES = {
    "draft",
    "blocked",
    "ready_for_flow",
    "generated",
    "reviewed",
    "accepted",
}
ALLOWED_MODES = {"ingredients", "frames", "text", "video-edit"}
ALLOWED_ASSET_KINDS = {
    "character",
    "location",
    "prop",
    "start-frame",
    "end-frame",
    "video",
    "voice",
    "other",
}
STATE_FIELDS = ("who", "where", "what", "when", "screen_geography", "story_phase")
PLACEHOLDER_MARKERS = ("<", ">")


def issue(
    severity: str,
    code: str,
    message: str,
    *,
    shot_ids: list[str] | None = None,
    asset_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "shot_ids": shot_ids or [],
        "asset_ids": asset_ids or [],
    }


def has_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return all(marker in value for marker in PLACEHOLDER_MARKERS)
    if isinstance(value, list):
        return any(has_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(has_placeholder(item) for item in value.values())
    return False


def normalized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_package(
    data: dict[str, Any], package_path: Path, *, check_paths: bool = True
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for field in ("schema_version", "project_id", "episode_id", "package_status", "aspect_ratio"):
        if not data.get(field):
            errors.append(issue("error", "missing_top_level_field", f"缺少顶层字段：{field}"))

    if data.get("schema_version") != "1.0":
        errors.append(issue("error", "unsupported_schema", "schema_version 必须为 1.0"))

    package_status = data.get("package_status")
    if package_status not in ALLOWED_PACKAGE_STATUSES:
        errors.append(issue("error", "invalid_package_status", f"不支持的 package_status：{package_status}"))

    if has_placeholder(data):
        errors.append(issue("error", "template_placeholder", "生产包仍含有 <...> 模板占位符"))

    if not data.get("source_documents"):
        warnings.append(issue("warning", "missing_source_documents", "未记录上游来源文档"))

    style_lock = data.get("style_lock")
    if not isinstance(style_lock, dict) or not style_lock.get("summary"):
        warnings.append(issue("warning", "missing_style_lock", "缺少可复用的 style_lock.summary"))

    assets = data.get("assets", [])
    if not isinstance(assets, list):
        errors.append(issue("error", "invalid_assets", "assets 必须是数组"))
        assets = []

    asset_map: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            errors.append(issue("error", "invalid_asset", "资产条目必须是对象"))
            continue
        asset_id = asset.get("asset_id")
        if not asset_id:
            errors.append(issue("error", "missing_asset_id", "资产缺少 asset_id"))
            continue
        if asset_id in asset_map:
            errors.append(issue("error", "duplicate_asset_id", f"重复资产 ID：{asset_id}", asset_ids=[asset_id]))
            continue
        asset_map[asset_id] = asset
        if asset.get("kind") not in ALLOWED_ASSET_KINDS:
            warnings.append(issue("warning", "unknown_asset_kind", f"资产 {asset_id} 的 kind 未知：{asset.get('kind')}", asset_ids=[asset_id]))
        path_value = asset.get("path")
        flow_name = asset.get("flow_asset_name")
        if not path_value and not flow_name:
            errors.append(issue("error", "asset_has_no_locator", f"资产 {asset_id} 同时缺少 path 与 flow_asset_name", asset_ids=[asset_id]))
        if check_paths and path_value:
            local_path = Path(path_value)
            if not local_path.is_absolute():
                local_path = package_path.parent / local_path
            if not local_path.exists():
                errors.append(issue("error", "missing_asset_file", f"资产文件不存在：{local_path}", asset_ids=[asset_id]))

    characters = data.get("characters", [])
    locations = data.get("locations", [])
    if not isinstance(characters, list):
        errors.append(issue("error", "invalid_characters", "characters 必须是数组"))
        characters = []
    if not isinstance(locations, list):
        errors.append(issue("error", "invalid_locations", "locations 必须是数组"))
        locations = []

    character_map = {item.get("character_id"): item for item in characters if isinstance(item, dict) and item.get("character_id")}
    location_map = {item.get("location_id"): item for item in locations if isinstance(item, dict) and item.get("location_id")}
    if len(character_map) != len([item for item in characters if isinstance(item, dict) and item.get("character_id")]):
        errors.append(issue("error", "duplicate_character_id", "characters 中存在重复 character_id"))
    if len(location_map) != len([item for item in locations if isinstance(item, dict) and item.get("location_id")]):
        errors.append(issue("error", "duplicate_location_id", "locations 中存在重复 location_id"))

    def check_asset_ref(asset_id: Any, shot_id: str, role: str) -> None:
        if not asset_id:
            return
        if asset_id not in asset_map:
            errors.append(issue("error", "unknown_asset_reference", f"镜头 {shot_id} 的 {role} 引用了未知资产：{asset_id}", shot_ids=[shot_id], asset_ids=[str(asset_id)]))
        elif not asset_map[asset_id].get("approved", False):
            errors.append(issue("error", "unapproved_asset_reference", f"镜头 {shot_id} 的 {role} 使用了未批准资产：{asset_id}", shot_ids=[shot_id], asset_ids=[asset_id]))

    shots = data.get("shots", [])
    if not isinstance(shots, list) or not shots:
        errors.append(issue("error", "missing_shots", "shots 必须是非空数组"))
        shots = []

    shot_ids: set[str] = set()
    prior_shot: dict[str, Any] | None = None
    for shot in shots:
        if not isinstance(shot, dict):
            errors.append(issue("error", "invalid_shot", "镜头条目必须是对象"))
            continue
        shot_id = shot.get("shot_id") or "<missing-shot-id>"
        if shot_id in shot_ids:
            errors.append(issue("error", "duplicate_shot_id", f"重复镜头 ID：{shot_id}", shot_ids=[shot_id]))
        shot_ids.add(shot_id)

        for field in ("shot_id", "source_scene_id", "purpose", "prompt"):
            if not shot.get(field):
                errors.append(issue("error", "missing_shot_field", f"镜头 {shot_id} 缺少字段：{field}", shot_ids=[shot_id]))
        duration = shot.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            errors.append(issue("error", "invalid_duration", f"镜头 {shot_id} 的 duration_seconds 必须大于 0", shot_ids=[shot_id]))

        mode = shot.get("generation_mode")
        if mode not in ALLOWED_MODES:
            errors.append(issue("error", "invalid_generation_mode", f"镜头 {shot_id} 的 generation_mode 不受支持：{mode}", shot_ids=[shot_id]))

        ref_ids = shot.get("reference_asset_ids", [])
        if not isinstance(ref_ids, list):
            errors.append(issue("error", "invalid_reference_list", f"镜头 {shot_id} 的 reference_asset_ids 必须是数组", shot_ids=[shot_id]))
            ref_ids = []
        for asset_id in ref_ids:
            check_asset_ref(asset_id, shot_id, "reference_asset_ids")
        for field in ("start_frame_asset_id", "end_frame_asset_id", "input_video_asset_id", "voice_reference_asset_id"):
            check_asset_ref(shot.get(field), shot_id, field)

        if mode == "ingredients" and not ref_ids:
            errors.append(issue("error", "ingredients_without_reference", f"镜头 {shot_id} 使用 ingredients 模式但没有引用资产", shot_ids=[shot_id]))
        if mode == "frames" and not shot.get("start_frame_asset_id"):
            errors.append(issue("error", "frames_without_start", f"镜头 {shot_id} 使用 frames 模式但没有 start_frame_asset_id", shot_ids=[shot_id]))
        if mode == "video-edit" and not shot.get("input_video_asset_id"):
            errors.append(issue("error", "video_edit_without_input", f"镜头 {shot_id} 使用 video-edit 模式但没有 input_video_asset_id", shot_ids=[shot_id]))

        for character_id in shot.get("character_ids", []):
            if character_id not in character_map:
                errors.append(issue("error", "unknown_character", f"镜头 {shot_id} 引用了未知角色：{character_id}", shot_ids=[shot_id]))
        location_id = shot.get("location_id")
        if location_id and location_id not in location_map:
            errors.append(issue("error", "unknown_location", f"镜头 {shot_id} 引用了未知地点：{location_id}", shot_ids=[shot_id]))

        for state_name in ("start_state", "end_state"):
            state = shot.get(state_name)
            if not isinstance(state, dict):
                errors.append(issue("error", "missing_state", f"镜头 {shot_id} 缺少 {state_name}", shot_ids=[shot_id]))
                continue
            missing = [field for field in STATE_FIELDS if field not in state]
            if missing:
                errors.append(issue("error", "incomplete_state", f"镜头 {shot_id} 的 {state_name} 缺少：{', '.join(missing)}", shot_ids=[shot_id]))

        delta = shot.get("continuity_delta")
        if not isinstance(delta, dict) or not isinstance(delta.get("changed"), list):
            errors.append(issue("error", "invalid_continuity_delta", f"镜头 {shot_id} 缺少有效 continuity_delta.changed", shot_ids=[shot_id]))
        elif delta.get("changed") and not str(delta.get("why_changed", "")).strip():
            errors.append(issue("error", "unexplained_state_change", f"镜头 {shot_id} 有状态变化但缺少 why_changed", shot_ids=[shot_id]))

        dialogue = shot.get("dialogue", {})
        if isinstance(dialogue, dict) and dialogue.get("text") and not dialogue.get("performance"):
            warnings.append(issue("warning", "dialogue_without_performance", f"镜头 {shot_id} 有对白但缺少表演说明", shot_ids=[shot_id]))

        if prior_shot and isinstance(prior_shot.get("end_state"), dict) and isinstance(shot.get("start_state"), dict):
            mismatched = [field for field in STATE_FIELDS if normalized(prior_shot["end_state"].get(field)) != normalized(shot["start_state"].get(field))]
            if mismatched and not str(shot.get("continuity_bridge", "")).strip():
                warnings.append(issue("warning", "adjacent_state_mismatch", f"镜头 {prior_shot.get('shot_id')} → {shot_id} 的状态不一致且未写 continuity_bridge：{', '.join(mismatched)}", shot_ids=[str(prior_shot.get("shot_id")), shot_id]))
        prior_shot = shot

    approval = data.get("approval")
    if not isinstance(approval, dict):
        approval = {}
    if package_status == "ready_for_flow" and not approval.get("creator_confirmed"):
        errors.append(issue("error", "ready_without_confirmation", "ready_for_flow 必须有 approval.creator_confirmed=true"))
    if package_status == "ready_for_flow" and not approval.get("batch_id"):
        errors.append(issue("error", "ready_without_batch", "ready_for_flow 必须记录 approval.batch_id"))

    if package_status == "accepted":
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            output = shot.get("output") if isinstance(shot.get("output"), dict) else {}
            shot_id = str(shot.get("shot_id", "<missing-shot-id>"))
            if output.get("review_status") != "accepted":
                errors.append(issue("error", "accepted_package_has_unaccepted_shot", f"镜头 {shot_id} 尚未通过成片回审", shot_ids=[shot_id]))
            if not output.get("clip_path") and not output.get("flow_asset_name"):
                errors.append(issue("error", "accepted_shot_has_no_output", f"镜头 {shot_id} 缺少输出文件或 Flow 资产名", shot_ids=[shot_id]))

    result_status = "fail" if errors else "warn" if warnings else "ok"
    return {
        "schema_version": "1.0",
        "status": result_status,
        "package": str(package_path),
        "metrics": {
            "assets": len(asset_map),
            "characters": len(character_map),
            "locations": len(location_map),
            "shots": len(shots),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Google Flow manga package without network access.")
    parser.add_argument("package", type=Path, help="Path to flow-package.json")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument("--skip-path-checks", action="store_true", help="Skip local asset existence checks")
    args = parser.parse_args()

    try:
        data = json.loads(args.package.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        print(f"ERROR: package not found: {args.package}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: cannot read package: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("ERROR: package root must be a JSON object", file=sys.stderr)
        return 2

    report = validate_package(data, args.package.resolve(), check_paths=not args.skip_path_checks)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(f"{report['status'].upper()}: {report['metrics']['errors']} error(s), {report['metrics']['warnings']} warning(s), {report['metrics']['shots']} shot(s)")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
