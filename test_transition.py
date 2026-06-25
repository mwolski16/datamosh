"""Tests for datamosh transition timing validation."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mosh import (
    TransitionWindow,
    _recalculate_transition_vcl_indices,
    _resolve_transition_window,
    _transition_output_duration,
    _validate_transition_against_stream,
    _validate_transition_clip_a_keyframe,
)


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
        self.assertEqual(window.motion_start_vcl, 0)
        self.assertEqual(window.duration_vcl_count, 60)
        self.assertEqual(window.suffix_start_vcl, 60)

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
            duration_seconds=1.0,
            duration_vcl_count=1,
            motion_start_vcl=2,
            suffix_start_vcl=4,
        )
        with self.assertRaisesRegex(ValueError, "needs a keyframe"):
            _validate_transition_against_stream(transition, clip_b, fps_b=1.0)

    def test_rejects_clip_a_without_keyframe_at_cut(self) -> None:
        clip_a = (
            b"\x00\x00\x01\x07B"
            + b"\x00\x00\x01\x08B"
            + b"\x00\x00\x01\x05\xb1"
            + b"\x00\x00\x01\x01\xb2"
            + b"\x00\x00\x01\x01\xb3"
        )
        with self.assertRaisesRegex(ValueError, "no keyframe at the transition cut"):
            _validate_transition_clip_a_keyframe(clip_a, start_vcl=2, fps_a=30.0)

    def test_recalculate_transition_vcl_indices(self) -> None:
        clip_a = (
            b"\x00\x00\x01\x07B"
            + b"\x00\x00\x01\x08B"
            + b"\x00\x00\x01\x05\xb1"
            + b"\x00\x00\x01\x01\xb2"
        )
        clip_b = (
            b"\x00\x00\x01\x07B"
            + b"\x00\x00\x01\x08B"
            + b"\x00\x00\x01\x05\xc1"
            + b"\x00\x00\x01\x01\xc2"
            + b"\x00\x00\x01\x01\xc3"
            + b"\x00\x00\x01\x05\xc4"
        )
        transition = TransitionWindow(
            start_seconds=1.0,
            start_vcl_index=99,
            duration_seconds=1.0,
            duration_vcl_count=99,
            motion_start_vcl=99,
            suffix_start_vcl=199,
        )
        recalculated = _recalculate_transition_vcl_indices(
            transition,
            clip_a,
            clip_b,
            fps_a=1.0,
            fps_b=1.0,
        )
        self.assertEqual(recalculated.start_vcl_index, 1)
        self.assertEqual(recalculated.motion_start_vcl, 0)
        self.assertEqual(recalculated.duration_vcl_count, 1)
        self.assertEqual(recalculated.suffix_start_vcl, 1)

    def test_transition_output_duration(self) -> None:
        transition = TransitionWindow(
            start_seconds=6.0,
            start_vcl_index=180,
            duration_seconds=2.0,
            duration_vcl_count=60,
            motion_start_vcl=0,
            suffix_start_vcl=60,
        )
        duration = _transition_output_duration(transition, clip_b_duration=10.0, fps_b=30.0)
        self.assertAlmostEqual(duration, 6.0 + 2.0 + (10.0 - 60 / 30.0))


if __name__ == "__main__":
    unittest.main()
