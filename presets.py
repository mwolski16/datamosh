"""Load and apply datamosh presets from config/presets.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from mosh import MoshOptions

PRESETS_PATH = Path(__file__).resolve().parent / "config" / "presets.json"


class PresetError(ValueError):
    pass


def _normalize_preset(raw: Dict[str, Any]) -> Dict[str, Any]:
    preset_id = raw.get("id")
    if not isinstance(preset_id, str) or not preset_id:
        raise PresetError("Each preset must have a non-empty string id.")

    return {
        "id": preset_id,
        "name": str(raw.get("name", preset_id)),
        "description": str(raw.get("description", "")),
        "mode": raw.get("mode", "single"),
        "gop": int(raw.get("gop", 250)),
        "mosh_start_seconds": raw.get("mosh_start_seconds"),
        "mosh_end_seconds": raw.get("mosh_end_seconds"),
        "transition_duration_seconds": raw.get("transition_duration_seconds"),
        "duplicate_copies": int(raw.get("duplicate_copies", 0)),
        "duplicate_probability": float(raw.get("duplicate_probability", 1.0)),
        "keep_first_idr": int(raw.get("keep_first_idr", 1)),
        "width": raw.get("width"),
        "crf": int(raw.get("crf", 18)),
        "remove_sps_pps": bool(raw.get("remove_sps_pps", False)),
        "no_prep": bool(raw.get("no_prep", False)),
        "no_audio": bool(raw.get("no_audio", False)),
        "seed": raw.get("seed"),
    }


def load_presets() -> List[Dict[str, Any]]:
    if not PRESETS_PATH.exists():
        return []

    data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    presets = data.get("presets", [])
    if not isinstance(presets, list):
        raise PresetError("presets.json must contain a presets array.")

    return [_normalize_preset(item) for item in presets]


def get_preset(preset_id: str) -> Dict[str, Any]:
    for preset in load_presets():
        if preset["id"] == preset_id:
            return preset
    available = ", ".join(preset["id"] for preset in load_presets()) or "(none)"
    raise PresetError(f"Unknown preset '{preset_id}'. Available: {available}")


def preset_to_options(preset: Dict[str, Any]) -> MoshOptions:
    width = preset.get("width")
    seed = preset.get("seed")
    mosh_start = preset.get("mosh_start_seconds")
    mosh_end = preset.get("mosh_end_seconds")

    return MoshOptions(
        keep_first_idr=int(preset.get("keep_first_idr", 1)),
        remove_sps_pps=bool(preset.get("remove_sps_pps", False)),
        duplicate_copies=int(preset.get("duplicate_copies", 0)),
        duplicate_probability=float(preset.get("duplicate_probability", 1.0)),
        seed=int(seed) if seed is not None else None,
        prep=not bool(preset.get("no_prep", False)),
        width=int(width) if width is not None else None,
        crf=int(preset.get("crf", 18)),
        gop=int(preset.get("gop", 250)),
        keep_audio=not bool(preset.get("no_audio", False)),
        mosh_start_seconds=float(mosh_start) if mosh_start not in (None, 0, 0.0) else None,
        mosh_end_seconds=float(mosh_end) if mosh_end not in (None, 0, 0.0) else None,
        transition_duration_seconds=(
            float(preset["transition_duration_seconds"])
            if preset.get("transition_duration_seconds") not in (None, 0, 0.0)
            else None
        ),
    )


def options_from_preset_id(preset_id: str) -> MoshOptions:
    return preset_to_options(get_preset(preset_id))
