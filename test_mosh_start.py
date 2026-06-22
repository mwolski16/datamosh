"""Tests for delayed mosh start."""

from __future__ import annotations

import unittest

from h264_stream import count_idr_frames, strip_idr_frames


def _nal(nal_type: int, payload: bytes = b"\x42") -> bytes:
    return b"\x00\x00\x01" + bytes([nal_type & 0x1F]) + payload


class DelayedMoshTests(unittest.TestCase):
    def test_keeps_idr_before_start_index(self) -> None:
        stream = (
            _nal(7)
            + _nal(8)
            + _nal(5, b"\x01")
            + _nal(1, b"\x02")
            + _nal(5, b"\x03")
            + _nal(1, b"\x04")
        )
        moshed = strip_idr_frames(stream, keep_first=0, start_vcl_index=2)
        self.assertEqual(count_idr_frames(moshed), 1)
        self.assertIn(b"\x01", moshed)
        self.assertIn(b"\x02", moshed)
        self.assertNotIn(b"\x03", moshed)
        self.assertIn(b"\x04", moshed)


    def test_keeps_idr_outside_window(self) -> None:
        stream = (
            _nal(7)
            + _nal(8)
            + _nal(5, b"\x01")
            + _nal(1, b"\x02")
            + _nal(5, b"\x03")
            + _nal(1, b"\x04")
            + _nal(5, b"\x05")
        )
        moshed = strip_idr_frames(stream, keep_first=0, start_vcl_index=1, end_vcl_index=3)
        self.assertEqual(count_idr_frames(moshed), 2)
        self.assertIn(b"\x01", moshed)
        self.assertNotIn(b"\x03", moshed)
        self.assertIn(b"\x05", moshed)


if __name__ == "__main__":
    unittest.main()
