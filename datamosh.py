#!/usr/bin/env python3
"""CLI for H.264 datamoshing via bitstream manipulation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ffmpeg_util import FfmpegError, require_ffmpeg
from mosh import MoshOptions, mosh_single, mosh_transition, mosh_two_clip
from presets import PresetError, get_preset, load_presets, options_from_preset_id
from util import cleanup_temp_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datamosh",
        description=(
            "Create datamosh effects by stripping H.264 IDR frames and "
            "splicing compressed bitstreams. Requires ffmpeg on PATH."
        ),
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="Source video (MP4, MOV, AVI, etc.)",
    )
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Destination MP4",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        help="Second clip (anchor for two-clip mode, video 2 for transition mode)",
    )
    parser.add_argument(
        "--transition",
        action="store_true",
        help="Transition mode: video 1 -> datamosh bridge -> video 2 (requires --reference)",
    )
    parser.add_argument(
        "--transition-duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Length of the datamosh bridge in transition mode (default: 2)",
    )
    parser.add_argument(
        "--keep-first-idr",
        type=int,
        default=1,
        metavar="N",
        help="Keep the first N IDR frames (default: 1; required for MP4 playback)",
    )
    parser.add_argument(
        "--remove-sps-pps",
        action="store_true",
        help="Strip SPS/PPS NAL units (single-clip mode only)",
    )
    parser.add_argument(
        "--duplicate-copies",
        type=int,
        default=0,
        metavar="N",
        help="Insert N extra copies of each P-frame to stretch smear (default: 0)",
    )
    parser.add_argument(
        "--duplicate-probability",
        type=float,
        default=1.0,
        metavar="P",
        help="Probability [0-1] of duplicating a given P-frame (default: 1.0)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Random seed for probabilistic P-frame duplication",
    )
    parser.add_argument(
        "--no-prep",
        action="store_true",
        help="Skip re-encode prep and copy the existing H.264 stream",
    )
    parser.add_argument(
        "--width",
        type=int,
        help="Scale output width during prep (height keeps aspect ratio)",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=18,
        help="CRF used during prep re-encode (default: 18)",
    )
    parser.add_argument(
        "--gop",
        type=int,
        default=250,
        help="Keyframe interval during prep; higher = stronger mosh (default: 250)",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Drop audio from the output",
    )
    parser.add_argument(
        "--mosh-start",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Start datamoshing at this timestamp; earlier footage stays normal (default: 0)",
    )
    parser.add_argument(
        "--mosh-end",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop datamoshing at this timestamp; later footage stays normal (default: end)",
    )
    parser.add_argument(
        "--preset",
        metavar="ID",
        help="Apply a named preset from config/presets.json (e.g. classic, heavy)",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="List available presets and exit",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep intermediate .h264 files next to the output",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.keep_first_idr < 0:
        raise SystemExit("--keep-first-idr must be >= 0")
    if args.duplicate_copies < 0:
        raise SystemExit("--duplicate-copies must be >= 0")
    if not 0.0 <= args.duplicate_probability <= 1.0:
        raise SystemExit("--duplicate-probability must be between 0 and 1")
    if args.crf < 0:
        raise SystemExit("--crf must be >= 0")
    if args.gop < 1:
        raise SystemExit("--gop must be >= 1")
    if args.mosh_start is not None and args.mosh_start < 0:
        raise SystemExit("--mosh-start must be >= 0")
    if args.mosh_end is not None and args.mosh_end < 0:
        raise SystemExit("--mosh-end must be >= 0")
    if not args.input.exists():
        raise SystemExit(f"Input not found: {args.input}")
    if args.reference is not None and not args.reference.exists():
        raise SystemExit(f"Reference not found: {args.reference}")
    if args.transition and args.reference is None:
        raise SystemExit("--transition requires --reference (video 2)")
    if args.remove_sps_pps and args.reference is not None and not args.transition:
        raise SystemExit("--remove-sps-pps only applies to single-clip mode")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        for preset in load_presets():
            print(f"{preset['id']}: {preset['name']} — {preset['description']}")
        return 0

    if args.input is None or args.output is None:
        raise SystemExit("input and output are required unless using --list-presets")

    _validate_args(args)

    try:
        require_ffmpeg()
    except FfmpegError as exc:
        print(exc, file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)

    try:
        if args.preset:
            options = options_from_preset_id(args.preset)
            preset = get_preset(args.preset)
            preset_mode = preset.get("mode", "single")
        else:
            options = MoshOptions(
                keep_first_idr=args.keep_first_idr,
                remove_sps_pps=args.remove_sps_pps,
                duplicate_copies=args.duplicate_copies,
                duplicate_probability=args.duplicate_probability,
                seed=args.seed,
                prep=not args.no_prep,
                width=args.width,
                crf=args.crf,
                gop=args.gop,
                keep_audio=not args.no_audio,
                mosh_start_seconds=args.mosh_start,
                mosh_end_seconds=args.mosh_end,
                transition_duration_seconds=args.transition_duration,
            )
            if args.transition:
                preset_mode = "transition"
            else:
                preset_mode = "two" if args.reference is not None else "single"
    except PresetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    use_transition = args.transition or preset_mode == "transition"
    use_two_clip = (args.reference is not None or preset_mode == "two") and not use_transition
    if preset_mode == "two" and args.reference is None:
        print("error: two-clip preset requires --reference", file=sys.stderr)
        return 1
    if preset_mode == "transition" and args.reference is None:
        print("error: transition preset requires --reference", file=sys.stderr)
        return 1

    try:
        if use_transition:
            result = mosh_transition(
                args.input,
                args.reference,
                args.output,
                options=options,
            )
            mode = "transition"
        elif not use_two_clip:
            result = mosh_single(args.input, args.output, options=options)
            mode = "single-clip"
        else:
            result = mosh_two_clip(
                args.reference,
                args.input,
                args.output,
                options=options,
            )
            mode = "two-clip"
    except (FfmpegError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.keep_temp:
        cleanup_temp_files(args.output)

    if args.preset:
        print(f"preset: {args.preset}")
    print(f"mode: {mode}")
    print(f"output: {result.output}")
    print(f"idr frames removed: {result.idr_before - result.idr_after} ({result.idr_before} -> {result.idr_after})")
    if result.mosh_start_seconds is not None or result.mosh_end_seconds is not None:
        start = "0.00s" if result.mosh_start_seconds is None else f"{result.mosh_start_seconds:.2f}s"
        end = "end" if result.mosh_end_seconds is None else f"{result.mosh_end_seconds:.2f}s"
        print(f"mosh window: {start} → {end}")
    if result.idr_auto_kept:
        print("note: kept 1 IDR frame automatically so the MP4 can play")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
