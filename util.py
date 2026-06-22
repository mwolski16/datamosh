"""Shared helpers for CLI and web UI."""

from __future__ import annotations

from pathlib import Path

TEMP_SUFFIXES = (
    ".prepped.h264",
    ".extracted.h264",
    ".ref.h264",
    ".target.h264",
    ".moshed.h264",
)


def cleanup_temp_files(output: Path) -> None:
    for suffix in TEMP_SUFFIXES:
        temp = output.parent / f"{output.stem}{suffix}"
        if temp.exists():
            temp.unlink()
