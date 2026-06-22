"""Tests for MP4-safe IDR stripping."""

from __future__ import annotations

import unittest

from h264_stream import count_idr_frames, strip_idr_frames
from mosh import _strip_with_mp4_safety


def _nal(nal_type: int, payload: bytes = b"\x42") -> bytes:
    return b"\x00\x00\x01" + bytes([nal_type & 0x1F]) + payload


class Mp4SafetyTests(unittest.TestCase):
    def test_auto_keeps_idr_when_zero_requested(self) -> None:
        stream = _nal(7) + _nal(8) + _nal(5, b"\x01") + _nal(1, b"\x02")
        moshed, auto_kept = _strip_with_mp4_safety(stream, keep_first=0, remove_sps_pps=False)
        self.assertTrue(auto_kept)
        self.assertEqual(count_idr_frames(moshed), 1)

    def test_respects_positive_keep_first(self) -> None:
        stream = _nal(7) + _nal(8) + _nal(5, b"\x01") + _nal(1, b"\x02") + _nal(5, b"\x03")
        moshed, auto_kept = _strip_with_mp4_safety(stream, keep_first=1, remove_sps_pps=False)
        self.assertFalse(auto_kept)
        self.assertEqual(count_idr_frames(moshed), 1)
        self.assertNotIn(b"\x03", moshed)


if __name__ == "__main__":
    unittest.main()
