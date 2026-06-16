# daily

`daily` converts EXR frame sequences into review-ready video clips, applying a full ACES colour pipeline via OpenColorIO and encoding to your codec of choice through FFmpeg.

![daily output — desert sunset shot with text overlays](.github/assets/screenshot.jpg)

*Example footage © [L'Horda studio](https://lhorda-studio.com/en). Used with permission.*

Inspired by [jedypod's `generate-dailies`](https://github.com/jedypod/generate-dailies), with a focus on minimal dependencies — no OpenImageIO, just the lean `OpenEXR` Python bindings.

Use `daily` as a **command-line tool**, a **Python library**, or an **optional web UI**.

## Features

- **ACES colour pipeline** — full OpenColorIO integration (`colorconvert`, `display`, `look`); ACES 1.x and 2.0 agnostic
- **Web UI** *(optional)* — Gradio interface with dropdowns for OCIO colourspaces, displays, views, and looks — see **[WEBUI.md](WEBUI.md)**
- **Batch encoding** — pass a directory, a single file, or a glob (`shots/**/*.exr`); all EXR sequences are discovered automatically, including same-named sequences across subdirectories
- **Minimal dependencies** — EXR reading via the `OpenEXR` package; no OpenImageIO
- **Cross-platform** — Windows, macOS, Linux
- **SMPTE timecode** — burned into the image and embedded in the container header
- **Multithreaded** — parallel frame processing (defaults to CPU core count)
- **Slate** — prepend any image (PNG, JPG, EXR) for a configurable number of frames
- **Text overlays** — 7-anchor positioning; static elements (date, frame range, sequence name) and per-frame elements (timecode, frame counter, filename, EXR metadata, custom CLI keys)
- **Codec presets** — H.264 HQ/LQ, H.265, ProRes HQ/4444/Proxy, DNxHR HQX/SQ — all configurable
- **Rich progress UI**

## How it works

Each frame runs through a fixed pipeline: **read EXR → OCIO transform → resize / letterbox → cropmask → text overlays → encode**. Processed frames are piped to FFmpeg as 16-bit `rgb48le`, and SMPTE timecode is embedded in the container.

## Requirements

- **Python 3.10+**
- **FFmpeg** — on your `PATH`, or set the explicit path via `--ffmpeg` / the `ffmpeg:` key in `daily.yaml`
- **OCIO config** — set `$OCIO`, or point `ocio.config` in `daily.yaml` at your `.ocio` file
- **Gradio** *(optional)* — only for the web UI, installed via the `[web]` extra (see below)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install .                    # core CLI
pip install ".[web]"             # + the daily-web web UI
```

## Quick start

A self-contained example lives in `example/` — the EXR sequence, OCIO config, and slate frame are all included, and `config/daily.yaml` is pre-configured to point at them:

```bash
daily -i example/input/**/*.exr -o example/output/ --slate-enable --text user="Raphael" --text description="comp v003"
```

Prefer a browser? Launch the optional web UI instead:

```bash
daily-web                        # requires the [web] extra; see WEBUI.md
```

`daily` prints one output path per line to **stdout**, while the progress bar and logs go to **stderr** — so you can capture produced files cleanly:

```bash
OUTPUT=$(daily -i shots/ -o reviews/)
```

## Documentation

| Guide | What's in it |
|-------|--------------|
| 📖 **[CLI reference](CLI.md)** | Every command-line flag, grouped by topic |
| ⚙️ **[Configuration](CONFIGURATION.md)** | The `daily.yaml`, `codecs.yaml`, and `text_overlays.yaml` files |
| 🖥️ **[Web UI](WEBUI.md)** | Running and using the optional browser interface |

## Use as a library

`run()` returns a `list[Path]` of the files it produced:

```python
from daily.config import build_daily_config
from daily.daily import run

config = build_daily_config(input_path="shots/", output="reviews/")
for path in run(config):
    print(path)
```
