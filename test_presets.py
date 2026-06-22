"""Tests for preset loading."""

from __future__ import annotations

import unittest

from presets import get_preset, load_presets, options_from_preset_id


class PresetTests(unittest.TestCase):
    def test_load_presets_not_empty(self) -> None:
        presets = load_presets()
        self.assertGreaterEqual(len(presets), 5)

    def test_classic_preset_maps_to_options(self) -> None:
        preset = get_preset("classic")
        options = options_from_preset_id("classic")
        self.assertEqual(preset["name"], "Classic smear")
        self.assertEqual(options.gop, 300)
        self.assertEqual(options.width, 1280)
        self.assertIsNone(options.mosh_start_seconds)


if __name__ == "__main__":
    unittest.main()
