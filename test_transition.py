"""Tests for datamosh transition timing validation."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mosh import TransitionWindow, _resolve_transition_window, _validate_transition_against_stream


class TransitionWindowTests(unittest.TestCase):
    @patch("mosh.probe_fps", return_value=30.0)
    @patch("mosh.probe_duration")
    def test_rejects_start_beyond_video_1(self, mock_duration, _mock_fps) -> None:
        mock_duration.side_effect = [10.0, 8.0]
        with self.assertRaisesRegex(ValueError, "before the end of video 1"):
            _resolve_transition_window(
                Path("a.mp4"),
                Path("b.mp4"),
                transition_start_seconds=9.95,
                transition_duration_seconds=2.0,
            )

    @patch("mosh.probe_fps", return_value=30.0)
    @patch("mosh.probe_duration")
    def test_rejects_duration_longer_than_video_2(self, mock_duration, _mock_fps) -> None:
        mock_duration.side_effect = [10.0, 3.0]
        with self.assertRaisesRegex(ValueError, "leave at least 0.5s of video 2"):
            _resolve_transition_window(
                Path("a.mp4"),
                Path("b.mp4"),
                transition_start_seconds=5.0,
                transition_duration_seconds=3.0,
            )

    @patch("mosh.probe_fps", return_value=30.0)
    @patch("mosh.probe_duration")
    def test_accepts_valid_window(self, mock_duration, _mock_fps) -> None:
        mock_duration.side_effect = [10.0, 8.0]
        window = _resolve_transition_window(
            Path("a.mp4"),
            Path("b.mp4"),
            transition_start_seconds=6.0,
            transition_duration_seconds=2.0,
        )
        self.assertEqual(window.start_seconds, 6.0)
        self.assertEqual(window.duration_seconds, 2.0)

    @patch("mosh.probe_fps", return_value=1.0)
    @patch("mosh.probe_duration")
    def test_rejects_bridge_past_next_keyframe(self, mock_duration, _mock_fps) -> None:
        mock_duration.side_effect = [10.0, 8.0]
        clip_b = (
            b"\x00\x00\x01\x07B"
            + b"\x00\x00\x01\x08B"
            + b"\x00\x00\x01\x05\xb1"
            + b"\x00\x00\x01\x01\xb2"
            + b"\x00\x00\x01\x01\xb3"
            + b"\x00\x00\x01\x05\xb4"
            + b"\x00\x00\x01\x01\xb5"
        )
        transition = TransitionWindow(
            start_seconds=5.0,
            start_vcl_index=5,
            duration_seconds=4.0,
            duration_vcl_count=4,
        )
        with self.assertRaisesRegex(ValueError, "next keyframe is at"):
            _validate_transition_against_stream(transition, clip_b, fps_b=1.0)


if __name__ == "__main__":
    unittest.main()
