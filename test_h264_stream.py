"""Unit tests for H.264 NAL helpers."""

from __future__ import annotations

import unittest

from h264_stream import (
    build_transition_stream,
    count_idr_frames,
    duplicate_p_frames,
    extract_header_nals,
    idr_vcl_indices,
    splice_streams,
    split_stream_before_vcl,
    strip_idr_frames,
    transition_bridge_limits,
)


def _nal(nal_type: int, payload: bytes = b"\x42") -> bytes:
    return b"\x00\x00\x01" + bytes([nal_type & 0x1F]) + payload


class H264StreamTests(unittest.TestCase):
    def test_strip_idr_frames_keeps_first(self) -> None:
        stream = _nal(7) + _nal(8) + _nal(5, b"\xaa") + _nal(1, b"\xbb") + _nal(5, b"\xcc")
        stripped = strip_idr_frames(stream, keep_first=1)
        self.assertEqual(count_idr_frames(stream), 2)
        self.assertEqual(count_idr_frames(stripped), 1)
        self.assertIn(b"\xaa", stripped)
        self.assertNotIn(b"\xcc", stripped)

    def test_duplicate_p_frames(self) -> None:
        stream = _nal(5, b"\x01") + _nal(1, b"\x02") + _nal(1, b"\x03")
        duplicated = duplicate_p_frames(stream, copies=2, probability=1.0, seed=1)
        self.assertGreater(len(duplicated), len(stream))

    def test_extract_header_nals(self) -> None:
        stream = _nal(7) + _nal(8) + _nal(5, b"\x01") + _nal(1, b"\x02")
        header = extract_header_nals(stream)
        self.assertIn(b"\x01", header)
        self.assertNotIn(b"\x02", header)

    def test_splice_streams(self) -> None:
        reference = _nal(7) + _nal(8) + _nal(5, b"\x01") + _nal(1, b"\xa2")
        target = _nal(7) + _nal(8) + _nal(5, b"\xaa") + _nal(1, b"\xbb") + _nal(5, b"\xcc")
        spliced = splice_streams(reference, target, keep_target_idr=0)
        self.assertIn(b"\x01", spliced)
        self.assertIn(b"\xbb", spliced)
        self.assertNotIn(b"\xaa", spliced)
        self.assertNotIn(b"\xcc", spliced)
        self.assertEqual(count_idr_frames(spliced), 1)

    def test_build_transition_stream(self) -> None:
        clip_a = (
            _nal(7)
            + _nal(8)
            + _nal(5, b"\xa1")
            + _nal(1, b"\xa2")
            + _nal(1, b"\xa3")
            + _nal(5, b"\xa4")
            + _nal(1, b"\xa5")
        )
        clip_b = (
            _nal(7)
            + _nal(8)
            + _nal(5, b"\xb1")
            + _nal(1, b"\xb2")
            + _nal(1, b"\xb3")
            + _nal(5, b"\xb4")
            + _nal(1, b"\xb5")
        )
        prefix = split_stream_before_vcl(clip_a, 3)
        self.assertIn(b"\xa2", prefix)
        self.assertNotIn(b"\xa4", prefix)

        transition = build_transition_stream(
            clip_a,
            clip_b,
            transition_start_vcl=3,
            transition_vcl_count=2,
        )
        self.assertIn(b"\xa4", transition)
        self.assertIn(b"\xb2", transition)
        self.assertIn(b"\xb4", transition)
        self.assertIn(b"\xb5", transition)
        self.assertNotIn(b"\xb1", transition)
        self.assertGreaterEqual(count_idr_frames(transition), 2)

    def test_transition_bridge_limits_with_mid_keyframe(self) -> None:
        clip_b = (
            _nal(7)
            + _nal(8)
            + _nal(5, b"\xb1")
            + _nal(1, b"\xb2")
            + _nal(1, b"\xb3")
            + _nal(5, b"\xb4")
            + _nal(1, b"\xb5")
        )
        self.assertEqual(idr_vcl_indices(clip_b), [0, 3])
        max_vcl, max_seconds, suffix_idr = transition_bridge_limits(clip_b, fps=1.0)
        self.assertEqual(max_vcl, 3)
        self.assertEqual(max_seconds, 3.0)
        self.assertEqual(suffix_idr, 3)

    def test_transition_bridge_limits_opening_keyframe_only(self) -> None:
        clip_b = _nal(7) + _nal(8) + _nal(5, b"\xb1") + _nal(1, b"\xb2") + _nal(1, b"\xb3")
        max_vcl, _, suffix_idr = transition_bridge_limits(clip_b, fps=1.0)
        self.assertEqual(max_vcl, 3)
        self.assertIsNone(suffix_idr)


if __name__ == "__main__":
    unittest.main()
