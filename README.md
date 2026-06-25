# Datamosh Studio

H.264 video datamoshing toolkit with a CLI and an interactive web UI. Manipulates raw H.264 bitstreams to create glitch-art effects: melting, smearing, and surreal motion-driven distortion.

## What is datamoshing?

Datamoshing removes **I-frames (keyframes/IDR slices)** from a compressed H.264 bitstream. The decoder is left applying P-frame motion updates to a stale reference frame, producing the signature melt/smear effect.

## Features

- **Three mosh modes** — single-clip IDR strip, two-clip splice, and datamosh transitions
- **Web UI** — drag-and-drop interface with presets, tooltips, and real-time progress
- **CLI** — full argparse interface for scripting and automation
- **11 built-in presets** — Classic, Heavy, Stutter, iPhone/HDR, Extreme, and more
- **Mosh windows** — limit the effect to a time range within a clip
- **P-frame duplication** — configurable copies and probability for stutter/glitch
- **MP4-safe** — auto-keeps at least one keyframe for playable output
- **Audio passthrough** — optionally keeps original audio track

## Prerequisites

- **Python 3.8+**
- **ffmpeg** and **ffprobe** on `PATH`

```bash
# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt install ffmpeg
```

## Installation

```bash
git clone <repo-url> datamosh
cd datamosh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Web UI

```bash
./run_ui.sh
```

Opens at **http://127.0.0.1:5050** — drop videos, pick a preset, tweak sliders, and download the result.

### CLI

```bash
source .venv/bin/activate
python datamosh.py <input> <output> [options]
```

#### Single-clip mosh

```bash
python datamosh.py source.mp4 output.mp4 --gop 250
```

#### Two-clip splice

```bash
python datamosh.py motion.mp4 output.mp4 --reference anchor.mp4
```

The anchor clip's first frame becomes the frozen base image, deformed by the motion clip's P-frames.

#### Datamosh transition

```bash
python datamosh.py clip1.mp4 output.mp4 --reference clip2.mp4 --transition --transition-duration 2
```

Clip A plays cleanly, then a datamosh bridge morphs into Clip B over the specified duration.

#### Using presets

```bash
python datamosh.py input.mp4 output.mp4 --preset classic
```

#### Listing available presets

```bash
python datamosh.py --list-presets
```

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--preset` | — | Load settings from a named preset |
| `--gop` | 250 | GOP size for prep re-encode |
| `--crf` | 18 | CRF quality (lower = better, 18-28 sensible) |
| `--keep-first-idr` | — | Number of leading IDR frames to preserve |
| `--duplicate-copies` | 0 | Extra copies of each P-frame (stutter) |
| `--duplicate-probability` | 1.0 | Chance each P-frame gets duplicated (0-1) |
| `--seed` | — | Random seed for deterministic duplicates |
| `--no-prep` | — | Skip re-encode (use source as-is) |
| `--width` | — | Resize width during prep (keeps aspect ratio) |
| `--no-audio` | — | Strip audio from output |
| `--mosh-start` | 0 | Start mosh effect at this time (seconds) |
| `--mosh-end` | 0 | End mosh effect at this time (0 = to end) |
| `--remove-sps-pps` | — | Strip SPS/PPS (breaks some players) |
| `--reference` | — | Second clip for splice/transition modes |
| `--transition` | — | Enable transition mode |
| `--transition-duration` | 2.0 | Length of the datamosh bridge (seconds) |
| `--list-presets` | — | Print preset catalog and exit |
| `--keep-temp` | — | Don't delete intermediate `.h264` files |

## Presets

| Preset | Mode | Description |
|--------|------|-------------|
| `classic` | Single | Standard melt, 300-frame GOP |
| `light` | Single | Subtle glitch, 120-frame GOP |
| `heavy` | Single | Deep melt, 600-frame GOP, 1 duplicate |
| `stutter` | Single | Rhythmic stutter, probabilistic duplicates |
| `delayed` | Single | Clean opening, mosh hits at 3s |
| `iphone` | Single | iPhone/HDR-friendly settings |
| `fast` | Single | Low-res quick preview |
| `transition` | Transition | 2.5s datamosh bridge with duplicates |
| `two_clip` | Two-clip | Cross-clip splice with duplicates |
| `window` | Single | Glitch confined to 2s–8s window |
| `extreme` | Single | Max destruction: 600 GOP, 4 duplicates, CRF 26 |

Presets are defined in `config/presets.json`. Add your own by following the existing format.

## Running tests

```bash
source .venv/bin/activate
python -m pytest test_*.py
```

## Project structure

```
datamosh/
├── app.py              # Flask web UI server
├── datamosh.py         # CLI entry point
├── mosh.py             # Core orchestration (single/two-clip/transition)
├── h264_stream.py      # H.264 Annex B bitstream parser & manipulator
├── ffmpeg_util.py      # ffmpeg/ffprobe subprocess wrappers
├── presets.py          # Preset loading & conversion
├── jobs.py             # Async job tracking for web UI
├── util.py             # Temp file cleanup
├── config/
│   ├── presets.json    # 11 named presets
│   └── tooltips.json   # Web UI tooltip content
├── templates/
│   └── index.html      # Single-page web UI
├── static/
│   ├── app.js          # Vanilla JS frontend logic
│   └── styles.css      # Dark cyberpunk theme
├── tests/
│   ├── test_h264_stream.py
│   ├── test_mosh_safety.py
│   ├── test_mosh_start.py
│   ├── test_presets.py
│   └── test_transition.py
└── workspace/           # Runtime uploads & outputs (gitignored)
```

## How it works

1. **Prep** — optionally re-encodes source through `libx264` with a long GOP and no B-frames for optimal datamoshing
2. **Extract** — pulls the raw H.264 Annex B bitstream from the MP4 container
3. **Manipulate** — parses NAL units, strips IDR slices, optionally duplicates P-frames
4. **Remux** — packages the modified bitstream back into an MP4 container, optionally preserving audio

## License

This project does not currently have a license. All rights reserved by the author.
