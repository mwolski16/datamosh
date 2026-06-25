#!/usr/bin/env python3
"""Local web UI for datamoshing."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from ffmpeg_util import FfmpegError, require_ffmpeg
from jobs import JOB_STORE, JobStatus, start_job
from mosh import MoshOptions, _resolve_transition_window
from presets import load_presets

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "workspace"
UPLOAD_DIR = WORK_DIR / "uploads"
OUTPUT_DIR = WORK_DIR / "outputs"
TOOLTIPS_PATH = BASE_DIR / "config" / "tooltips.json"
PRESETS_PATH = BASE_DIR / "config" / "presets.json"
MAX_UPLOAD_MB = 512


def _blog(msg: str) -> None:
    print(f"[backend] {msg}")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def _ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TOOLTIPS_PATH.parent.mkdir(parents=True, exist_ok=True)


def _parse_bool(value: Optional[str]) -> bool:
    return value in {"1", "true", "True", "on", "yes"}


def _parse_int(name: str, default: int, *, minimum: Optional[int] = None) -> int:
    raw = request.form.get(name, str(default))
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return parsed


def _parse_float(name: str, default: float) -> float:
    raw = request.form.get(name, str(default))
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return parsed


def _save_upload(job_id: str, field_name: str, fallback_name: str) -> Optional[Path]:
    upload = request.files.get(field_name)
    if upload is None or not upload.filename:
        return None

    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(upload.filename) or fallback_name
    destination = job_dir / filename
    upload.save(destination)
    return destination


def _parse_optional_float(name: str) -> Optional[float]:
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be >= 0")
    return parsed


def _build_options() -> MoshOptions:
    width_raw = request.form.get("width", "").strip()
    width = int(width_raw) if width_raw else None
    seed_raw = request.form.get("seed", "").strip()
    seed = int(seed_raw) if seed_raw else None

    return MoshOptions(
        keep_first_idr=_parse_int("keep_first_idr", 1, minimum=0),
        remove_sps_pps=_parse_bool(request.form.get("remove_sps_pps")),
        duplicate_copies=_parse_int("duplicate_copies", 0, minimum=0),
        duplicate_probability=_parse_float("duplicate_probability", 1.0),
        seed=seed,
        prep=not _parse_bool(request.form.get("no_prep")),
        width=width,
        crf=_parse_int("crf", 18, minimum=0),
        gop=_parse_int("gop", 250, minimum=1),
        keep_audio=not _parse_bool(request.form.get("no_audio")),
        mosh_start_seconds=_parse_optional_float("mosh_start_seconds"),
        mosh_end_seconds=_parse_optional_float("mosh_end_seconds"),
        transition_duration_seconds=_parse_optional_float("transition_duration_seconds"),
    )


def _load_tooltips() -> dict[str, Any]:
    if not TOOLTIPS_PATH.exists():
        return {}
    return json.loads(TOOLTIPS_PATH.read_text(encoding="utf-8"))


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/presets")
def api_presets() -> Any:
    presets = load_presets()
    _blog(f"GET /api/presets -> {len(presets)} presets")
    return jsonify({"presets": presets})


@app.get("/api/tooltips")
def api_tooltips() -> Any:
    tooltips = _load_tooltips()
    _blog(f"GET /api/tooltips -> {len(tooltips)} entries")
    return jsonify(tooltips)


@app.post("/api/mosh")
def api_mosh() -> Any:
    _blog(f"POST /api/mosh mode={request.form.get('mode', 'single')}")
    try:
        require_ffmpeg()
    except FfmpegError as exc:
        return jsonify({"error": str(exc)}), 500

    source = request.files.get("source")
    if source is None or not source.filename:
        return jsonify({"error": "Source video is required."}), 400

    mode = request.form.get("mode", "single")
    if mode not in {"single", "two", "transition"}:
        return jsonify({"error": "Unknown mode."}), 400

    upload_id = uuid.uuid4().hex
    try:
        options = _build_options()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if mode == "single" and options.remove_sps_pps and request.files.get("reference"):
        return jsonify({"error": "Remove SPS/PPS only applies to single-clip mode."}), 400

    source_path = _save_upload(upload_id, "source", "source.mp4")
    if source_path is None:
        return jsonify({"error": "Could not save source upload."}), 400

    reference_path = _save_upload(upload_id, "reference", "reference.mp4")
    if mode in {"two", "transition"} and reference_path is None:
        label = "Video 2" if mode == "transition" else "Reference video"
        return jsonify({"error": f"{label} is required for this mode."}), 400

    if mode == "transition":
        try:
            _resolve_transition_window(
                source_path,
                reference_path,
                options.mosh_start_seconds,
                options.transition_duration_seconds,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    output_name = f"{upload_id}.mp4"
    output_path = OUTPUT_DIR / output_name

    job = start_job(
        mode=mode,
        source_path=source_path,
        output_path=output_path,
        options=options,
        reference_path=reference_path,
    )

    _blog(f"POST /api/mosh -> job_id={job.job_id}")
    return jsonify({"job_id": job.job_id}), 202


@app.get("/api/jobs/<job_id>")
def api_job_status(job_id: str) -> Any:
    job = JOB_STORE.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404

    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status.value,
        "progress": job.progress,
        "stage": job.stage,
    }
    if job.status == JobStatus.ERROR:
        payload["error"] = job.error
    if job.status == JobStatus.COMPLETE and job.result is not None:
        payload["result"] = job.result
    return jsonify(payload)


@app.get("/outputs/<path:filename>")
def serve_output(filename: str) -> Any:
    return send_from_directory(OUTPUT_DIR, filename, mimetype="video/mp4")


@app.errorhandler(Exception)
def handle_api_errors(exc: Exception) -> Any:
    if not request.path.startswith("/api/"):
        return exc
    return jsonify({"error": str(exc)}), 500


def main() -> None:
    _ensure_dirs()
    require_ffmpeg()
    print("Datamosh Studio running at http://127.0.0.1:5050")
    print(f"Edit tooltips in: {TOOLTIPS_PATH}")
    print(f"Edit presets in: {PRESETS_PATH}")
    app.run(host="127.0.0.1", port=5050, debug=False)


if __name__ == "__main__":
    main()
