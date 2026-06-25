"""Core datamosh operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ffmpeg_util import extract_h264, prep_video, probe_duration, probe_fps, remux_h264
from h264_stream import (
    build_transition_stream,
    count_idr_frames,
    count_vcl_frames,
    duplicate_p_frames,
    extract_header_nals,
    idr_vcl_indices,
    strip_idr_frames,
    transition_bridge_limits,
)

ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class MoshOptions:
    keep_first_idr: int = 1
    remove_sps_pps: bool = False
    duplicate_copies: int = 0
    duplicate_probability: float = 1.0
    seed: Optional[int] = None
    prep: bool = True
    width: Optional[int] = None
    crf: int = 18
    gop: int = 250
    keep_audio: bool = True
    mosh_start_seconds: Optional[float] = None
    mosh_end_seconds: Optional[float] = None
    transition_duration_seconds: Optional[float] = None


@dataclass(frozen=True)
class TransitionWindow:
    start_seconds: float
    start_vcl_index: int
    duration_seconds: float
    duration_vcl_count: int
    motion_start_vcl: int
    suffix_start_vcl: int


@dataclass(frozen=True)
class MoshWindow:
    start_seconds: Optional[float]
    start_vcl_index: Optional[int]
    end_seconds: Optional[float]
    end_vcl_index: Optional[int]


@dataclass(frozen=True)
class MoshResult:
    output: Path
    idr_before: int
    idr_after: int
    used_reference: bool
    idr_auto_kept: bool = False
    mosh_start_seconds: Optional[float] = None
    mosh_start_vcl_index: Optional[int] = None
    mosh_end_seconds: Optional[float] = None
    mosh_end_vcl_index: Optional[int] = None
    transition_duration_seconds: Optional[float] = None
    transition_duration_vcl_count: Optional[int] = None


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _write_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def _remux_output(
    raw_output: Path,
    output: Path,
    *,
    audio_source: Optional[Path],
    timing_source: Path,
    options: MoshOptions,
    duration_seconds: Optional[float] = None,
) -> None:
    if duration_seconds is None and options.duplicate_copies > 0 and audio_source is not None:
        duration_seconds = probe_duration(timing_source)

    remux_h264(
        raw_output,
        output,
        audio_source=audio_source,
        fps=probe_fps(timing_source),
        duration_seconds=duration_seconds,
        reencode=options.duplicate_copies > 0,
        crf=options.crf,
    )


def _transition_output_duration(
    transition: TransitionWindow,
    clip_b_duration: float,
    fps_b: float,
) -> float:
    """Total muxed duration: V1 prefix + bridge + clean V2 tail."""
    v2_tail = max(clip_b_duration - transition.suffix_start_vcl / fps_b, 0.0)
    return transition.start_seconds + transition.duration_seconds + v2_tail


def _strip_with_mp4_safety(
    payload: bytes,
    *,
    keep_first: int,
    remove_sps_pps: bool,
    start_vcl_index: Optional[int] = None,
    end_vcl_index: Optional[int] = None,
) -> tuple[bytes, bool]:
    """
    Strip IDR frames, but ensure at least one remains for MP4 remux/playback.

    ffmpeg cannot mux a raw H.264 stream with zero keyframes into MP4 — the result
    is audio-only/black video even though the .h264 file looks valid.
    """
    moshed = strip_idr_frames(
        payload,
        keep_first=keep_first,
        remove_sps_pps=remove_sps_pps,
        start_vcl_index=start_vcl_index,
        end_vcl_index=end_vcl_index,
    )
    if count_idr_frames(moshed) > 0:
        return moshed, False

    moshed = strip_idr_frames(
        payload,
        keep_first=1,
        remove_sps_pps=remove_sps_pps,
        start_vcl_index=start_vcl_index,
        end_vcl_index=end_vcl_index,
    )
    return moshed, True


def _resolve_mosh_window(
    source: Path,
    start_seconds: Optional[float],
    end_seconds: Optional[float],
) -> MoshWindow:
    duration = probe_duration(source)
    fps = probe_fps(source)
    max_vcl_index = max(int(duration * fps) - 1, 0)

    resolved_start: Optional[float] = None
    start_vcl_index: Optional[int] = None
    if start_seconds is not None and start_seconds > 0:
        if start_seconds >= duration:
            raise ValueError(
                f"mosh start ({start_seconds}s) must be less than video duration ({duration:.2f}s)."
            )
        resolved_start = start_seconds
        start_vcl_index = min(int(start_seconds * fps), max_vcl_index)

    resolved_end: Optional[float] = None
    end_vcl_index: Optional[int] = None
    if end_seconds is not None and end_seconds > 0:
        if end_seconds > duration:
            raise ValueError(
                f"mosh end ({end_seconds}s) must be at most video duration ({duration:.2f}s)."
            )
        minimum_end = (resolved_start or 0.0) + 0.1
        if end_seconds <= minimum_end:
            raise ValueError(
                f"mosh end ({end_seconds}s) must be after mosh start ({minimum_end:.2f}s)."
            )
        resolved_end = end_seconds
        end_vcl_index = min(int(end_seconds * fps), max_vcl_index + 1)

    if (
        start_vcl_index is not None
        and end_vcl_index is not None
        and end_vcl_index <= start_vcl_index
    ):
        raise ValueError("mosh end must be after mosh start.")

    return MoshWindow(
        start_seconds=resolved_start,
        start_vcl_index=start_vcl_index,
        end_seconds=resolved_end,
        end_vcl_index=end_vcl_index,
    )


def _format_mosh_window(window: MoshWindow) -> str:
    start = "0s" if window.start_seconds is None else f"{window.start_seconds:.2f}s"
    end = "end" if window.end_seconds is None else f"{window.end_seconds:.2f}s"
    return f"{start} → {end}"


def _resolve_transition_window(
    clip_a: Path,
    clip_b: Path,
    transition_start_seconds: Optional[float],
    transition_duration_seconds: Optional[float],
) -> TransitionWindow:
    duration_a = probe_duration(clip_a)
    duration_b = probe_duration(clip_b)
    fps_a = probe_fps(clip_a)
    fps_b = probe_fps(clip_b)
    max_vcl_a = max(int(duration_a * fps_a) - 1, 0)
    max_vcl_b = max(int(duration_b * fps_b), 1)

    duration = transition_duration_seconds if transition_duration_seconds else 2.0
    min_suffix = 0.5
    min_start_margin = 0.1
    if duration <= 0:
        raise ValueError("transition duration must be > 0.")
    if duration_b <= min_suffix:
        raise ValueError(
            f"video 2 must be longer than {min_suffix:.1f}s for a transition (got {duration_b:.2f}s)."
        )
    max_duration = duration_b - min_suffix
    if duration > max_duration:
        raise ValueError(
            f"transition duration ({duration:.2f}s) must leave at least {min_suffix:.1f}s "
            f"of video 2 for the clean ending (video 2 is {duration_b:.2f}s, max bridge {max_duration:.2f}s)."
        )

    if transition_start_seconds is None or transition_start_seconds <= 0:
        resolved_start = max(duration_a * 0.7, duration_a - duration - 0.5)
        resolved_start = min(max(resolved_start, 0.0), max(duration_a - min_start_margin, 0.0))
    else:
        resolved_start = transition_start_seconds

    if duration_a <= min_start_margin:
        raise ValueError(
            f"video 1 must be longer than {min_start_margin:.1f}s for a transition (got {duration_a:.2f}s)."
        )
    max_start = duration_a - min_start_margin
    if resolved_start > max_start:
        raise ValueError(
            f"transition start ({resolved_start:.2f}s) must be at least {min_start_margin:.1f}s "
            f"before the end of video 1 ({duration_a:.2f}s, max start {max_start:.2f}s)."
        )

    start_vcl = min(int(resolved_start * fps_a), max_vcl_a)
    motion_start_vcl = 0
    duration_vcl = min(max(int(duration * fps_b), 1), max_vcl_b)
    suffix_start_vcl = duration_vcl

    if suffix_start_vcl > max_vcl_b:
        raise ValueError(
            f"Transition length extends past the end of video 2 ({duration_b:.2f}s)."
        )

    return TransitionWindow(
        start_seconds=resolved_start,
        start_vcl_index=start_vcl,
        duration_seconds=duration,
        duration_vcl_count=duration_vcl,
        motion_start_vcl=motion_start_vcl,
        suffix_start_vcl=suffix_start_vcl,
    )


def _format_transition_window(window: TransitionWindow) -> str:
    return (
        f"{window.start_seconds:.2f}s (f{window.start_vcl_index}) + "
        f"{window.duration_seconds:.2f}s bridge (B f{window.motion_start_vcl}→f{window.suffix_start_vcl})"
    )


def _transition_gop_for_clip_a(options: MoshOptions) -> int:
    """Short GOP on video 1 so the cut can land on a nearby keyframe."""
    return min(options.gop, 30)


def _transition_gop_for_clip_b(options: MoshOptions) -> int:
    """Short GOP on video 2 so keyframes exist for the clean tail after the bridge."""
    return min(options.gop, 30)


def _recalculate_transition_vcl_indices(
    transition: TransitionWindow,
    clip_a_payload: bytes,
    clip_b_payload: bytes,
    fps_a: float,
    fps_b: float,
) -> TransitionWindow:
    """Map the user-facing transition window onto prepared bitstreams."""
    max_vcl_a = max(count_vcl_frames(clip_a_payload) - 1, 0)
    max_vcl_b = count_vcl_frames(clip_b_payload)
    start_vcl = min(int(transition.start_seconds * fps_a), max_vcl_a)
    motion_start = 0
    duration_vcl = min(
        max(int(transition.duration_seconds * fps_b), 1),
        max_vcl_b,
    )
    suffix_start = duration_vcl
    return TransitionWindow(
        start_seconds=transition.start_seconds,
        start_vcl_index=start_vcl,
        duration_seconds=transition.duration_seconds,
        duration_vcl_count=duration_vcl,
        motion_start_vcl=motion_start,
        suffix_start_vcl=suffix_start,
    )


def _align_transition_start_to_clip_a(
    transition: TransitionWindow,
    clip_a_payload: bytes,
    fps_a: float,
) -> TransitionWindow:
    """Snap the cut to the nearest prepared keyframe on video 1."""
    idrs = idr_vcl_indices(clip_a_payload)
    if not idrs:
        raise ValueError("Video 1 has no keyframes after prep.")

    intended = transition.start_vcl_index
    snap = min(idrs, key=lambda index: abs(index - intended))
    max_snap_frames = max(int(fps_a * 1.0), 30)
    if abs(snap - intended) > max_snap_frames:
        raise ValueError(
            f"Video 1 has no keyframe near the transition cut ({transition.start_seconds:.2f}s). "
            f"The nearest keyframe is {snap / fps_a:.2f}s — move the transition start or "
            "keep prep re-encode enabled."
        )

    return TransitionWindow(
        start_seconds=snap / fps_a,
        start_vcl_index=snap,
        duration_seconds=transition.duration_seconds,
        duration_vcl_count=transition.duration_vcl_count,
        motion_start_vcl=transition.motion_start_vcl,
        suffix_start_vcl=transition.suffix_start_vcl,
    )


def _validate_transition_clip_a_keyframe(
    clip_a_payload: bytes,
    start_vcl: int,
    fps_a: float,
) -> None:
    """The bridge needs video 1's pixels at the cut, not an earlier keyframe."""
    idrs = idr_vcl_indices(clip_a_payload)
    anchor = max((index for index in idrs if index <= start_vcl), default=None)
    if anchor is None:
        raise ValueError(
            "Video 1 has no keyframe before the transition cut. "
            "Enable prep re-encode or move the transition start to a keyframe."
        )

    max_frame_skew = max(int(fps_a * 0.05), 1)
    if start_vcl - anchor > max_frame_skew:
        raise ValueError(
            f"Video 1 has no keyframe at the transition cut ({start_vcl / fps_a:.2f}s). "
            f"The nearest keyframe is {anchor / fps_a:.2f}s — enable prep re-encode so an "
            "I-frame is forced at the cut, or move the transition start to a keyframe."
        )


def _align_transition_to_stream(
    transition: TransitionWindow,
    clip_b_payload: bytes,
    fps_b: float,
) -> TransitionWindow:
    """Snap the clean tail to the next keyframe on video 2's timeline."""
    idrs = idr_vcl_indices(clip_b_payload)
    total = count_vcl_frames(clip_b_payload)
    if total == 0:
        raise ValueError("Video 2 has no frames.")

    suffix_start = next((index for index in idrs if index >= transition.suffix_start_vcl), None)
    if suffix_start is None:
        max_vcl, max_seconds, _ = transition_bridge_limits(clip_b_payload, fps_b)
        raise ValueError(
            f"Video 2 has no keyframe after {transition.duration_seconds:.1f}s for a clean handoff. "
            f"Shorten the transition to {max(max_seconds - transition.start_seconds, 0.5):.1f}s or less, "
            "or keep prep re-encode enabled."
        )

    duration_vcl = suffix_start - transition.motion_start_vcl
    if duration_vcl < 1:
        raise ValueError("Transition length is too short for a datamosh bridge.")

    return TransitionWindow(
        start_seconds=transition.start_seconds,
        start_vcl_index=transition.start_vcl_index,
        duration_seconds=duration_vcl / fps_b,
        duration_vcl_count=duration_vcl,
        motion_start_vcl=transition.motion_start_vcl,
        suffix_start_vcl=suffix_start,
    )


def _validate_transition_against_stream(
    transition: TransitionWindow,
    clip_b_payload: bytes,
    fps_b: float,
) -> None:
    total = count_vcl_frames(clip_b_payload)
    if transition.suffix_start_vcl > total:
        raise ValueError("Transition length exceeds video 2 frame count.")

    if transition.suffix_start_vcl not in idr_vcl_indices(clip_b_payload):
        raise ValueError(
            f"Video 2 needs a keyframe at {transition.suffix_start_vcl / fps_b:.1f}s "
            "to resume cleanly after the bridge. Keep prep re-encode enabled."
        )

    if transition.suffix_start_vcl >= total:
        raise ValueError(
            "Transition length must leave at least one frame of clean video 2 after the bridge."
        )


def mosh_single(
    source: Path,
    output: Path,
    *,
    options: MoshOptions,
    on_progress: Optional[ProgressCallback] = None,
) -> MoshResult:
    def report(percent: int, stage: str) -> None:
        if on_progress is not None:
            on_progress(percent, stage)

    working = output.parent
    prepared = working / f"{output.stem}.prepped.h264"

    if options.prep:
        report(10, "Preparing video (re-encode to long GOP H.264)")
        prep_video(
            source,
            prepared,
            width=options.width,
            crf=options.crf,
            gop=options.gop,
        )
        stream_path = prepared
    else:
        report(15, "Extracting H.264 stream")
        stream_path = working / f"{output.stem}.extracted.h264"
        extract_h264(source, stream_path)

    report(45, "Stripping IDR (I-frame) slices")
    payload = _read_bytes(stream_path)
    idr_before = count_idr_frames(payload)
    mosh_window = _resolve_mosh_window(
        source,
        options.mosh_start_seconds,
        options.mosh_end_seconds,
    )
    if mosh_window.start_vcl_index is not None or mosh_window.end_vcl_index is not None:
        report(48, f"Mosh window: {_format_mosh_window(mosh_window)}")

    moshed, idr_auto_kept = _strip_with_mp4_safety(
        payload,
        keep_first=options.keep_first_idr,
        remove_sps_pps=options.remove_sps_pps,
        start_vcl_index=mosh_window.start_vcl_index,
        end_vcl_index=mosh_window.end_vcl_index,
    )

    if options.duplicate_copies > 0:
        report(65, "Duplicating P-frames")
        moshed = duplicate_p_frames(
            moshed,
            copies=options.duplicate_copies,
            probability=options.duplicate_probability,
            seed=options.seed,
            start_vcl_index=mosh_window.start_vcl_index,
            end_vcl_index=mosh_window.end_vcl_index,
        )

    report(80, "Writing moshed bitstream")
    raw_output = working / f"{output.stem}.moshed.h264"
    _write_bytes(raw_output, moshed)

    report(90, "Remuxing MP4")
    audio_source = source if options.keep_audio else None
    _remux_output(
        raw_output,
        output,
        audio_source=audio_source,
        timing_source=source,
        options=options,
    )
    report(100, "Complete")

    return MoshResult(
        output=output,
        idr_before=idr_before,
        idr_after=count_idr_frames(moshed),
        used_reference=False,
        idr_auto_kept=idr_auto_kept,
        mosh_start_seconds=mosh_window.start_seconds,
        mosh_start_vcl_index=mosh_window.start_vcl_index,
        mosh_end_seconds=mosh_window.end_seconds,
        mosh_end_vcl_index=mosh_window.end_vcl_index,
    )


def mosh_two_clip(
    reference: Path,
    target: Path,
    output: Path,
    *,
    options: MoshOptions,
    on_progress: Optional[ProgressCallback] = None,
) -> MoshResult:
    def report(percent: int, stage: str) -> None:
        if on_progress is not None:
            on_progress(percent, stage)

    working = output.parent
    ref_path = working / f"{output.stem}.ref.h264"
    target_path = working / f"{output.stem}.target.h264"

    if options.prep:
        report(10, "Preparing reference clip")
        prep_video(
            reference,
            ref_path,
            width=options.width,
            crf=options.crf,
            gop=options.gop,
        )
        report(30, "Preparing source clip")
        prep_video(
            target,
            target_path,
            width=options.width,
            crf=options.crf,
            gop=options.gop,
        )
    else:
        report(15, "Extracting reference H.264 stream")
        extract_h264(reference, ref_path)
        report(30, "Extracting source H.264 stream")
        extract_h264(target, target_path)

    report(50, "Splicing reference header with source P-frames")
    ref_payload = _read_bytes(ref_path)
    target_payload = _read_bytes(target_path)
    idr_before = count_idr_frames(target_payload)
    mosh_window = _resolve_mosh_window(
        target,
        options.mosh_start_seconds,
        options.mosh_end_seconds,
    )
    if mosh_window.start_vcl_index is not None or mosh_window.end_vcl_index is not None:
        report(52, f"Mosh window: {_format_mosh_window(mosh_window)}")

    header = extract_header_nals(ref_payload)
    body, idr_auto_kept = _strip_with_mp4_safety(
        target_payload,
        keep_first=0,
        remove_sps_pps=True,
        start_vcl_index=mosh_window.start_vcl_index,
        end_vcl_index=mosh_window.end_vcl_index,
    )

    if options.duplicate_copies > 0:
        report(68, "Duplicating P-frames")
        body = duplicate_p_frames(
            body,
            copies=options.duplicate_copies,
            probability=options.duplicate_probability,
            seed=options.seed,
            start_vcl_index=mosh_window.start_vcl_index,
            end_vcl_index=mosh_window.end_vcl_index,
        )

    moshed = header + body

    report(82, "Writing moshed bitstream")
    raw_output = working / f"{output.stem}.moshed.h264"
    _write_bytes(raw_output, moshed)

    report(92, "Remuxing MP4")
    audio_source = target if options.keep_audio else None
    _remux_output(
        raw_output,
        output,
        audio_source=audio_source,
        timing_source=target,
        options=options,
    )
    report(100, "Complete")

    return MoshResult(
        output=output,
        idr_before=idr_before,
        idr_after=count_idr_frames(moshed),
        used_reference=True,
        idr_auto_kept=idr_auto_kept,
        mosh_start_seconds=mosh_window.start_seconds,
        mosh_start_vcl_index=mosh_window.start_vcl_index,
        mosh_end_seconds=mosh_window.end_seconds,
        mosh_end_vcl_index=mosh_window.end_vcl_index,
    )


def mosh_transition(
    clip_a: Path,
    clip_b: Path,
    output: Path,
    *,
    options: MoshOptions,
    on_progress: Optional[ProgressCallback] = None,
) -> MoshResult:
    """Video 1 (clean) -> datamosh bridge -> video 2 (clean)."""
    def report(percent: int, stage: str) -> None:
        if on_progress is not None:
            on_progress(percent, stage)

    working = output.parent
    clip_a_path = working / f"{output.stem}.a.h264"
    clip_b_path = working / f"{output.stem}.b.h264"

    transition = _resolve_transition_window(
        clip_a,
        clip_b,
        options.mosh_start_seconds,
        options.transition_duration_seconds,
    )
    bridge_end_seconds = transition.duration_seconds

    if options.prep:
        report(10, "Preparing video 1 (keyframe at transition cut)")
        gop_a = _transition_gop_for_clip_a(options)
        prep_video(
            clip_a,
            clip_a_path,
            width=options.width,
            crf=options.crf,
            gop=gop_a,
            force_key_frames=[transition.start_seconds],
        )
        report(30, "Preparing video 2 (keyframes for bridge + clean tail)")
        gop_b = _transition_gop_for_clip_b(options)
        prep_video(
            clip_b,
            clip_b_path,
            width=options.width,
            crf=options.crf,
            gop=gop_b,
            force_key_frames=[0.0, bridge_end_seconds],
        )
    else:
        report(15, "Extracting video 1 H.264 stream")
        extract_h264(clip_a, clip_a_path)
        report(30, "Extracting video 2 H.264 stream")
        extract_h264(clip_b, clip_b_path)

    report(50, "Building datamosh transition")
    clip_a_payload = _read_bytes(clip_a_path)
    clip_b_payload = _read_bytes(clip_b_path)
    idr_before = count_idr_frames(clip_a_payload) + count_idr_frames(clip_b_payload)

    fps_a = probe_fps(clip_a_path)
    fps_b = probe_fps(clip_b_path)
    transition = _recalculate_transition_vcl_indices(
        transition,
        clip_a_payload,
        clip_b_payload,
        fps_a,
        fps_b,
    )
    if options.prep:
        transition = _align_transition_start_to_clip_a(
            transition, clip_a_payload, fps_a
        )
    _validate_transition_clip_a_keyframe(
        clip_a_payload,
        transition.start_vcl_index,
        fps_a,
    )
    transition = _align_transition_to_stream(transition, clip_b_payload, fps_b)
    _validate_transition_against_stream(transition, clip_b_payload, fps_b)
    report(52, f"Transition: {_format_transition_window(transition)}")

    moshed = build_transition_stream(
        clip_a_payload,
        clip_b_payload,
        transition_start_vcl=transition.start_vcl_index,
        motion_start_vcl=transition.motion_start_vcl,
        motion_end_vcl=transition.suffix_start_vcl,
        suffix_start_vcl=transition.suffix_start_vcl,
        duplicate_copies=options.duplicate_copies,
        duplicate_probability=options.duplicate_probability,
        seed=options.seed,
    )

    idr_after = count_idr_frames(moshed)

    report(82, "Writing transition bitstream")
    raw_output = working / f"{output.stem}.moshed.h264"
    _write_bytes(raw_output, moshed)

    report(92, "Remuxing MP4")
    audio_source = clip_a if options.keep_audio else None
    clip_b_duration = probe_duration(clip_b)
    remux_duration = None
    if options.duplicate_copies > 0 and audio_source is not None:
        remux_duration = _transition_output_duration(transition, clip_b_duration, fps_b)
    _remux_output(
        raw_output,
        output,
        audio_source=audio_source,
        timing_source=clip_a,
        options=options,
        duration_seconds=remux_duration,
    )
    report(100, "Complete")

    return MoshResult(
        output=output,
        idr_before=idr_before,
        idr_after=idr_after,
        used_reference=True,
        idr_auto_kept=False,
        mosh_start_seconds=transition.start_seconds,
        mosh_start_vcl_index=transition.start_vcl_index,
        mosh_end_seconds=transition.start_seconds + transition.duration_seconds,
        mosh_end_vcl_index=transition.start_vcl_index + transition.duration_vcl_count,
        transition_duration_seconds=transition.duration_seconds,
        transition_duration_vcl_count=transition.duration_vcl_count,
    )
