"""Thin ffmpeg wrappers used by the datamosh CLI."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence


class FfmpegError(RuntimeError):
    pass


def require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FfmpegError(
            "ffmpeg was not found on PATH. Install ffmpeg and try again."
        )
    return ffmpeg


def run_ffmpeg(args: Sequence[str], *, cwd: Optional[Path] = None) -> None:
    ffmpeg = require_ffmpeg()
    command: List[str] = [ffmpeg, *args]
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise FfmpegError(f"ffmpeg failed ({completed.returncode}): {stderr}")


def prep_video(
    source: Path,
    destination: Path,
    *,
    width: Optional[int] = None,
    crf: int = 18,
    gop: int = 250,
) -> None:
    """Re-encode to long-GOP H.264 with no B-frames for predictable moshing."""
    if width:
        scale_filter = f"scale={width}:-2:flags=lanczos,format=yuv420p,setsar=1"
    else:
        scale_filter = "format=yuv420p,setsar=1"
    video_args = [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-bf",
        "0",
    ]
    command = ["-y", "-i", str(source), "-an", "-vf", scale_filter]
    command.extend(video_args)
    command.extend(["-f", "h264", str(destination)])
    run_ffmpeg(command)


def extract_h264(source: Path, destination: Path) -> None:
    run_ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_mp4toannexb",
            "-f",
            "h264",
            str(destination),
        ]
    )


def remux_h264(
    video: Path,
    destination: Path,
    *,
    audio_source: Optional[Path] = None,
) -> None:
    command = ["-y", "-f", "h264", "-i", str(video)]
    if audio_source is not None:
        command.extend(["-i", str(audio_source), "-map", "0:v:0", "-map", "1:a?"])
    else:
        command.append("-an")
    command.extend(["-c", "copy", "-movflags", "+faststart", str(destination)])
    run_ffmpeg(command)
    if not _output_has_video(destination):
        raise FfmpegError(
            "Remux produced an audio-only MP4. Keep at least one IDR frame "
            "(set Keep first IDR frames to 1 or higher)."
        )


def _output_has_video(path: Path) -> bool:
    ffmpeg = require_ffmpeg()
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
    if not Path(ffprobe).exists() and shutil.which("ffprobe") is None:
        return True
    probe_bin = shutil.which("ffprobe") or ffprobe
    completed = subprocess.run(
        [
            probe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() == "video"


def _parse_ffprobe_number(raw: str) -> float:
    value = raw.strip().strip(",").split(",")[0].strip()
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    return float(value)


def probe_fps(source: Path) -> float:
    probe_bin = shutil.which("ffprobe")
    if probe_bin is None:
        raise FfmpegError("ffprobe was not found on PATH.")

    completed = subprocess.run(
        [
            probe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "csv=p=0",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise FfmpegError(completed.stderr.strip() or "Could not read video frame rate.")

    return _parse_ffprobe_number(completed.stdout)


def probe_duration(source: Path) -> float:
    probe_bin = shutil.which("ffprobe")
    if probe_bin is None:
        raise FfmpegError("ffprobe was not found on PATH.")

    completed = subprocess.run(
        [
            probe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise FfmpegError(completed.stderr.strip() or "Could not read video duration.")

    return _parse_ffprobe_number(completed.stdout)
