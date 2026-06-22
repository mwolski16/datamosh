"""H.264 Annex B bitstream helpers for datamoshing."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, List, Optional


class NalUnitType(IntEnum):
    CODED_SLICE_NON_IDR = 1
    CODED_SLICE_PARTITION_A = 2
    CODED_SLICE_PARTITION_B = 3
    CODED_SLICE_PARTITION_C = 4
    CODED_SLICE_IDR = 5
    SEI = 6
    SPS = 7
    PPS = 8
    AUD = 9


START_CODE_PATTERN = re.compile(rb"\x00\x00\x01|\x00\x00\x00\x01")


@dataclass(frozen=True)
class NalUnit:
    offset: int
    data: bytes

    @property
    def nal_type(self) -> int:
        start = 3 if self.data.startswith(b"\x00\x00\x01") else 4
        return self.data[start] & 0x1F

    @property
    def is_idr(self) -> bool:
        return self.nal_type == NalUnitType.CODED_SLICE_IDR

    @property
    def is_vcl(self) -> bool:
        nal_type = self.nal_type
        return 1 <= nal_type <= 5

    @property
    def is_sps(self) -> bool:
        return self.nal_type == NalUnitType.SPS

    @property
    def is_pps(self) -> bool:
        return self.nal_type == NalUnitType.PPS


def iter_nal_units(data: bytes) -> Iterator[NalUnit]:
    matches = list(START_CODE_PATTERN.finditer(data))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(data)
        yield NalUnit(offset=start, data=data[start:end])


def join_nal_units(units: List[NalUnit]) -> bytes:
    return b"".join(unit.data for unit in units)


def count_idr_frames(data: bytes) -> int:
    return sum(1 for unit in iter_nal_units(data) if unit.is_idr)


def count_vcl_frames(data: bytes) -> int:
    return sum(1 for unit in iter_nal_units(data) if unit.is_vcl)


def idr_vcl_indices(data: bytes) -> List[int]:
    """Return VCL indices of every IDR slice in order."""
    indices: List[int] = []
    vcl_index = 0
    for unit in iter_nal_units(data):
        if unit.is_vcl:
            if unit.is_idr:
                indices.append(vcl_index)
            vcl_index += 1
    return indices


def next_idr_vcl_index(data: bytes, start_vcl: int) -> Optional[int]:
    """First IDR VCL index at or after ``start_vcl``, or None."""
    for index in idr_vcl_indices(data):
        if index >= start_vcl:
            return index
    return None


def transition_bridge_limits(data: bytes, fps: float) -> tuple[int, float, Optional[int]]:
    """
    Return (max_bridge_vcl, max_bridge_seconds, suffix_idr_vcl).

    The bridge may end at any IDR index > 0; the clean tail starts on that keyframe.
    """
    if fps <= 0:
        raise ValueError("fps must be > 0")

    idrs = idr_vcl_indices(data)
    total = count_vcl_frames(data)
    suffix_starts = [index for index in idrs if index > 0]

    if suffix_starts:
        max_vcl = max(suffix_starts)
        suffix_idr = max_vcl
    else:
        max_vcl = total
        suffix_idr = None

    return max_vcl, max_vcl / fps, suffix_idr


def extract_clean_suffix(data: bytes, start_vcl: int) -> bytes:
    """
    Clean tail from ``start_vcl`` through EOF, keeping every slice and IDR.

    ``start_vcl`` must land on an IDR so the decoder resets before video 2 resumes.
    """
    if start_vcl < 0:
        raise ValueError("start_vcl must be >= 0")

    sps_pps: List[NalUnit] = []
    for unit in iter_nal_units(data):
        if unit.is_sps or unit.is_pps:
            sps_pps.append(unit)
        else:
            break

    output: List[NalUnit] = []
    current = 0
    started = False

    for unit in iter_nal_units(data):
        if unit.is_sps or unit.is_pps:
            continue
        if unit.is_vcl:
            if not started:
                if current < start_vcl:
                    current += 1
                    continue
                if not unit.is_idr:
                    raise ValueError(
                        f"Clean suffix must start on a keyframe (VCL {start_vcl} is not IDR)."
                    )
                output = sps_pps + [unit]
                started = True
                current += 1
                continue
            output.append(unit)
            current += 1
        elif started:
            output.append(unit)

    return join_nal_units(output)


def _in_mosh_region(
    vcl_index: int,
    *,
    start_vcl_index: Optional[int],
    end_vcl_index: Optional[int],
) -> bool:
    if start_vcl_index is not None and vcl_index < start_vcl_index:
        return False
    if end_vcl_index is not None and vcl_index >= end_vcl_index:
        return False
    return True


def strip_idr_frames(
    data: bytes,
    *,
    keep_first: int = 0,
    remove_sps_pps: bool = False,
    start_vcl_index: Optional[int] = None,
    end_vcl_index: Optional[int] = None,
) -> bytes:
    """
    Remove IDR (I-frame) NAL units inside the mosh window, keeping the first N there.

    When start_vcl_index / end_vcl_index are set, only VCL slices in
    [start, end) are moshed. Footage outside the window keeps all IDR frames.
    """
    if keep_first < 0:
        raise ValueError("keep_first must be >= 0")
    if start_vcl_index is not None and start_vcl_index < 0:
        raise ValueError("start_vcl_index must be >= 0")
    if end_vcl_index is not None and end_vcl_index < 0:
        raise ValueError("end_vcl_index must be >= 0")
    if (
        start_vcl_index is not None
        and end_vcl_index is not None
        and end_vcl_index <= start_vcl_index
    ):
        raise ValueError("end_vcl_index must be greater than start_vcl_index")

    kept: List[NalUnit] = []
    vcl_index = 0
    idr_seen_in_mosh_region = 0

    for unit in iter_nal_units(data):
        if remove_sps_pps and (unit.is_sps or unit.is_pps):
            continue

        if unit.is_vcl:
            in_mosh_region = _in_mosh_region(
                vcl_index,
                start_vcl_index=start_vcl_index,
                end_vcl_index=end_vcl_index,
            )
            if unit.is_idr:
                if not in_mosh_region:
                    kept.append(unit)
                elif idr_seen_in_mosh_region < keep_first:
                    kept.append(unit)
                    idr_seen_in_mosh_region += 1
                else:
                    idr_seen_in_mosh_region += 1
            else:
                kept.append(unit)
            vcl_index += 1
            continue

        kept.append(unit)

    return join_nal_units(kept)


def extract_header_nals(data: bytes) -> bytes:
    """Return SPS, PPS, and the first IDR slice from a stream."""
    header: List[NalUnit] = []
    seen_idr = False

    for unit in iter_nal_units(data):
        if unit.is_sps or unit.is_pps:
            header.append(unit)
            continue
        if unit.is_idr and not seen_idr:
            header.append(unit)
            seen_idr = True
            break

    return join_nal_units(header)


def duplicate_p_frames(
    data: bytes,
    *,
    copies: int = 1,
    probability: float = 1.0,
    seed: Optional[int] = None,
    start_vcl_index: Optional[int] = None,
    end_vcl_index: Optional[int] = None,
) -> bytes:
    """Duplicate non-IDR VCL slices inside the mosh window."""
    if copies < 1:
        raise ValueError("copies must be >= 1")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between 0 and 1")
    if start_vcl_index is not None and start_vcl_index < 0:
        raise ValueError("start_vcl_index must be >= 0")
    if end_vcl_index is not None and end_vcl_index < 0:
        raise ValueError("end_vcl_index must be >= 0")

    rng = random.Random(seed)
    output: List[NalUnit] = []
    vcl_index = 0

    for unit in iter_nal_units(data):
        output.append(unit)
        if unit.is_vcl:
            in_mosh_region = _in_mosh_region(
                vcl_index,
                start_vcl_index=start_vcl_index,
                end_vcl_index=end_vcl_index,
            )
            if in_mosh_region and not unit.is_idr and rng.random() <= probability:
                for _ in range(copies):
                    output.append(unit)
            vcl_index += 1

    return join_nal_units(output)


def splice_streams(reference: bytes, target: bytes, *, keep_target_idr: int = 0) -> bytes:
    """
    Classic two-clip datamosh: reference header + target without its own IDR frames.
    """
    header = extract_header_nals(reference)
    body = strip_idr_frames(target, keep_first=keep_target_idr, remove_sps_pps=True)
    return header + body


def split_stream_before_vcl(data: bytes, vcl_index: int) -> bytes:
    """Keep all NAL units whose VCL index is strictly before ``vcl_index``."""
    if vcl_index <= 0:
        return b""

    kept: List[NalUnit] = []
    current = 0
    for unit in iter_nal_units(data):
        if unit.is_vcl:
            if current >= vcl_index:
                break
            kept.append(unit)
            current += 1
        elif current < vcl_index:
            kept.append(unit)

    return join_nal_units(kept)


def extract_header_at_vcl(data: bytes, vcl_index: int) -> bytes:
    """Return SPS/PPS plus the IDR at or immediately before ``vcl_index``."""
    if vcl_index < 0:
        raise ValueError("vcl_index must be >= 0")

    sps_pps: List[NalUnit] = []
    anchor_idr: Optional[NalUnit] = None
    last_idr: Optional[NalUnit] = None
    current = 0

    for unit in iter_nal_units(data):
        if unit.is_sps or unit.is_pps:
            sps_pps.append(unit)
            continue
        if not unit.is_vcl:
            continue

        if unit.is_idr:
            last_idr = unit
            if current <= vcl_index:
                anchor_idr = unit

        if current == vcl_index:
            if unit.is_idr:
                anchor_idr = unit
            elif last_idr is not None:
                anchor_idr = last_idr
            break

        current += 1

    if anchor_idr is None:
        if last_idr is not None:
            anchor_idr = last_idr
        else:
            raise ValueError("No IDR frame found at or before the transition point.")

    return join_nal_units(sps_pps + [anchor_idr])


def extract_vcl_range(
    data: bytes,
    start_vcl: int,
    end_vcl: int,
    *,
    strip_idr: bool = True,
    keep_first_idr: int = 0,
) -> bytes:
    """Return VCL NAL units in ``[start_vcl, end_vcl)``, optionally stripping IDRs."""
    if start_vcl < 0 or end_vcl < 0:
        raise ValueError("vcl indices must be >= 0")
    if end_vcl <= start_vcl:
        return b""

    output: List[NalUnit] = []
    current = 0
    idr_seen = 0

    for unit in iter_nal_units(data):
        if not unit.is_vcl:
            continue
        if current < start_vcl:
            current += 1
            continue
        if current >= end_vcl:
            break

        if unit.is_idr and strip_idr:
            if idr_seen < keep_first_idr:
                output.append(unit)
            idr_seen += 1
        else:
            output.append(unit)
        current += 1

    return join_nal_units(output)


def extract_stream_from_vcl_keyframe(data: bytes, start_vcl: int) -> bytes:
    """
    Return SPS/PPS plus the stream from the first IDR at or after ``start_vcl``.
    """
    if start_vcl < 0:
        raise ValueError("start_vcl must be >= 0")

    sps_pps: List[NalUnit] = []
    for unit in iter_nal_units(data):
        if unit.is_sps or unit.is_pps:
            sps_pps.append(unit)
        else:
            break

    output: List[NalUnit] = []
    current = 0
    started = False

    for unit in iter_nal_units(data):
        if unit.is_sps or unit.is_pps:
            continue
        if unit.is_vcl:
            if not started:
                if current >= start_vcl and unit.is_idr:
                    output = sps_pps + [unit]
                    started = True
                current += 1
                continue
            output.append(unit)
            current += 1
        elif started:
            output.append(unit)

    if not started:
        return b""

    return join_nal_units(output)


def build_transition_stream(
    clip_a: bytes,
    clip_b: bytes,
    *,
    transition_start_vcl: int,
    motion_start_vcl: int,
    motion_end_vcl: int,
    suffix_start_vcl: int,
    duplicate_copies: int = 0,
    duplicate_probability: float = 1.0,
    seed: Optional[int] = None,
) -> bytes:
    """
    Video 1 (clean prefix) -> datamosh bridge -> video 2 (clean suffix).

    The bridge splices video 1's frame at the cut (decode anchor) with video 2's
    P-frames only — every IDR is stripped from the bridge so motion bleeds through
    video 1's pixels. Video 2 then resumes cleanly from ``suffix_start_vcl``.
    """
    if motion_end_vcl <= motion_start_vcl:
        raise ValueError("Transition bridge must include at least one frame from video 2.")

    prefix = split_stream_before_vcl(clip_a, transition_start_vcl)
    header = extract_header_at_vcl(clip_a, transition_start_vcl)
    transition_body = extract_vcl_range(
        clip_b,
        motion_start_vcl,
        motion_end_vcl,
        strip_idr=True,
        keep_first_idr=0,
    )
    if not transition_body:
        raise ValueError("Transition segment is empty — video 2 may be too short.")

    if duplicate_copies > 0:
        transition_body = duplicate_p_frames(
            transition_body,
            copies=duplicate_copies,
            probability=duplicate_probability,
            seed=seed,
        )

    transition_segment = header + transition_body
    suffix = extract_clean_suffix(clip_b, suffix_start_vcl)
    if not suffix:
        raise ValueError(
            f"No clean video 2 tail from frame {suffix_start_vcl}. "
            "Enable prep re-encode so a keyframe lands at the end of the transition."
        )

    return prefix + transition_segment + suffix
