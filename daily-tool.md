# Daily — Rewrite Design Document

Based on deep analysis of [jedypod/generate-dailies](https://github.com/jedypod/generate-dailies).

> **Requires Python 3.10+** | **ffmpeg** (system binary)

---

## 1. What the Tool Does

Converts scene-linear OpenEXR image sequences into display-referred QuickTime movies. Core pipeline per frame:

1. Load EXR via `OpenEXR` into float32 numpy array
2. Apply OCIO color transform via `PyOpenColorIO`
3. Crop / resize / fit to output resolution
4. Composite text overlays and cropmask
5. Pipe raw pixel data to ffmpeg for encoding

No temp files — everything streams through a subprocess pipe.

**Bit depth**: All internal operations happen on **float32 numpy arrays** — maximum precision preserved throughout. Bit depth reduction happens only at the final step when piping to ffmpeg, controlled by the codec preset's `pix_fmt`:

| Codec preset | pix_fmt | Output depth |
|---|---|---|
| H.264 standard | `yuv420p` | 8-bit |
| ProRes 422 HQ | `yuv422p10le` | 10-bit |
| ProRes 4444 | `yuv444p12le` | 12-bit |
| DNxHR HQX | `yuv422p10le` | 10-bit |

---

## 2. What's Wrong With the Original

### Architecture
- Single-file monolith (~900 lines), entire logic including processing triggered from `__init__`
- No separation of concerns — config loading, validation, arg parsing, frame processing, encoding all entangled
- No pipeline abstraction for frame ops — adding/reordering steps requires editing core loop
- Untestable as written — instantiating the class immediately starts encoding

### OCIO / Color
- Only `colorconvert` (colorspace-to-colorspace) is implemented
- `ociodisplay`, `ocioview`, and `ociolook` are all **commented out dead code** — this is the canonical display-referred path for a dailies tool and it's absent
- No OCIO v2 features: no built-in transforms, no dynamic properties, no viewing rules, no looks
- Silent failure on color transform error — logs warning, continues, outputs wrong-colored movie

### Text System — 5 Confirmed Bugs

| # | Bug | Effect |
|---|-----|--------|
| 1 | `self.text` never pre-populated from config | All elements except `datetime` silently produce no text unless passed via `-t` |
| 2 | `justify` condition uses `or` instead of `and` | Center alignment never works — always falls back to `"left"` |
| 3 | `-ct` flag overwritten unconditionally by `--ocio` | CLI arg parsing unreliable |
| 4 | `static_text_buf` composited even when empty | Potential subtle image darkening |
| 5 | `enable` flag in yaml never checked | Config misleading — toggling `enable: false` does nothing |

**Root cause:** The system was designed assuming all text content comes via `-t` CLI flag. The yaml controls layout only, was never wired to supply content. The `delivery` profile (default) has `text_elements: null` — text can never render on it regardless of `-t`.

### Other Issues
- `yaml.load()` without `Loader` — security warning in modern PyYAML, should be `yaml.safe_load()`
- ffmpeg `stderr` is not captured — pipe failures produce corrupt files with no error output
- No input validation — bad font paths, invalid colorspace names, resolution mismatches all either crash unhandled or produce silent wrong output
- No resume on interrupt — full re-encode from scratch
- Single-threaded frame loop — no prefetching, no parallelism
- Log file silently overwritten each run
- No `requirements.txt` or environment file in repo

---

## 3. What to Keep

- Pipe-to-ffmpeg approach — no temp files, low memory footprint, correct design
- Codec preset system in yaml — well-structured, comprehensive (now in separate `codecs.yaml`)
- Sequence discovery via `pyseq`
- `Timecode` arithmetic (replace `tc.py` with `timecode` PyPI package)
- Container-level timecode injection via ffmpeg `-timecode` flag
- Overall pipeline concept: EXR → color → crop/resize → text → encode

---

## 4. Proposed Architecture

### Module Structure

```
daily/
├── cli.py               # argparse: encode + ui subcommands
├── config.py            # pydantic models, loads codecs.yaml + daily.yaml
├── sequence.py          # input discovery, frame iteration
├── color.py             # PyOpenColorIO wrapper (colorconvert + display + look)
├── processor.py         # frame pipeline (ordered ops on numpy arrays)
├── text.py              # modular text engine — content resolver + Pillow renderer
├── slate.py             # slate/logo frame generator
├── encode.py            # ffmpeg pipe with stderr capture + container timecode
├── timecode.py          # frame <-> timecode conversion
├── daily.py             # orchestrator — UI-agnostic
└── ui.py                # Textual TUI (optional, calls daily.py)
```

---

### 4.1 Config Layer (`config.py`)

**Why Pydantic?** — Loading YAML without validation gives you a raw `dict`. Typos in key names, wrong types, missing fields all silently pass through and blow up mid-encode — 200 frames in, you discover your font path was wrong. Pydantic uses Python type hints to define data schemas and validates automatically at load time:

- `font_size: "big"` instead of a number → clear type error with field name and value
- Missing required field → error listing exactly what's missing
- Font path doesn't exist → caught via `Path` validator before any processing
- Replaces ~100 lines of manual `if key not in config` / `isinstance()` checks with declarative models

**Two separate config files** — codecs are studio infrastructure, the user config is per-project/per-artist:

```
config/
├── codecs.yaml     # codec presets — studio-wide, rarely changed, shared
└── daily.yaml      # user config — OCIO, output, text layout, slate
```

**Codec config (`codecs.yaml`):**
```python
class CodecPreset(BaseModel):
    name: str
    codec: str                     # e.g. "libx264", "prores_ks"
    pix_fmt: str                   # e.g. "yuv420p", "yuv422p10le"
    quality: int | str             # e.g. 18 (crf) or "hq" (prores profile)
    ffmpeg_args: list[str] = []    # extra ffmpeg flags
```

**OCIO config:**
```python
class OCIOTransformConfig(BaseModel):
    type: Literal["colorconvert", "display", "look"]
    src: str
    dst: str | None = None        # colorconvert
    display: str | None = None    # display transform
    view: str | None = None
    looks: list[str] = []
```

**OCIO config path resolution priority:**
```
CLI --ocio flag  →  daily.yaml ocio_config key  →  $OCIO env var  →  error
```

**Default user config (`daily.yaml`):**
```yaml
# ── OCIO ─────────────────────────────────────────────────
ocio:
  config: "$OCIO"                   # path or env var
  transform:
    type: display
    src: "ACES - ACEScg"
    display: "sRGB"
    view: "ACES 1.0 - SDR Video"
    looks: []

# ── Output ───────────────────────────────────────────────
output:
  codec: prores_hq                  # must match a name in codecs.yaml
  resolution: [1920, 1080]          # output resolution (width, height)
  framerate: "24"

# ── Slate ────────────────────────────────────────────────
slate:
  enable: false
  logo: ""
  duration_frames: 24
  background_color: [0.0, 0.0, 0.0]
  logo_scale: 0.5
  logo_position: center

# ── Text Elements ────────────────────────────────────────
# Each element: content type + position + style.
# Any content type can go in any position.
text_elements:
  - content: timecode
    anchor: bottom-center
    font_size: 18
    color: [1.0, 1.0, 1.0, 0.8]
    shadow: true

  - content: metadata.artist
    anchor: top-left
    offset: [20, 20]
    prefix: "Artist: "
    font_size: 20
    color: [1.0, 1.0, 1.0, 1.0]
    shadow: true

  - content: framerange
    anchor: bottom-left
    offset: [20, 20]
    font_size: 16
    color: [1.0, 1.0, 1.0, 0.6]

  - content: date
    anchor: top-right
    offset: [20, 20]
    format: "%Y-%m-%d"              # strftime format string
    font_size: 16
    color: [1.0, 1.0, 1.0, 0.6]

  - content: framecounter
    anchor: bottom-right
    offset: [20, 20]
    font_size: 18
    color: [1.0, 1.0, 1.0, 0.8]
    shadow: true

  - content: sequence_name
    anchor: top-center
    font_size: 20
    color: [1.0, 1.0, 1.0, 1.0]
```

---

### 4.2 Color Layer (`color.py`)

Supports all three OCIO transform types via `PyOpenColorIO`. Operates directly on numpy arrays. Raises on failure instead of silently continuing.

```python
import PyOpenColorIO as ocio

class OCIOProcessor:
    def __init__(self, transform_config: OCIOTransformConfig, config_path: Path):
        self.config = ocio.Config.CreateFromFile(str(config_path))
        self.transform_config = transform_config

    def apply(self, buf: np.ndarray) -> np.ndarray:
        match self.transform_config.type:
            case "colorconvert": return self._colorconvert(buf)
            case "display":      return self._display(buf)
            case "look":         return self._look(buf)

    def _display(self, buf: np.ndarray) -> np.ndarray:
        processor = self.config.getProcessor(
            ocio.DisplayViewTransform(
                src=self.transform_config.src,
                display=self.transform_config.display,
                view=self.transform_config.view
            )
        )
        cpu = processor.getDefaultCPUProcessor()
        img = buf.copy()
        cpu.applyRGB(img)  # in-place on numpy array
        return img
```

---

### 4.3 Modular Text Engine (`text.py`)

The text engine is fully user-configurable. Each text element is a **content type** + **position** + **style**, wired together in the `text_elements` section of `daily.yaml`. Any content type can go in any position.

**Built-in content types:**

| Content type | Source | Per-frame? |
|---|---|---|
| `timecode` | Computed from frame number + framerate | Yes |
| `framecounter` | Raw frame number | Yes |
| `framerange` | Full range from pyseq (e.g. `1001-1240`) | No (static) |
| `time_of_day` | Wall clock at encode time | No (static) |
| `date` | Current date, user-defined `format` (strftime) | No (static) |
| `filename` | Source EXR filename | Yes |
| `sequence_name` | Sequence name from pyseq | No (static) |
| `metadata.<key>` | Any EXR header field (e.g. `metadata.artist`) | Yes |
| `<custom>` | Fed from CLI flag `--text name=value` | No (static) |

The `format` field uses Python strftime syntax and applies to `date` and `time_of_day` content types. Examples: `"%Y-%m-%d"` → `2026-05-21`, `"%d/%m/%Y"` → `21/05/2026`, `"%B %d, %Y"` → `May 21, 2026`.

**Pydantic model:**
```python
class TextElementConfig(BaseModel):
    content: str                    # "timecode", "metadata.artist", etc.
    enable: bool = True
    anchor: Literal["top-left", "top-center", "top-right",
                    "bottom-left", "bottom-center", "bottom-right",
                    "center"] = "bottom-left"
    offset: tuple[int, int] = (20, 20)
    font: Path | None = None        # None = use default font
    font_size: float = 24.0
    color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    shadow: bool = False
    shadow_offset: tuple[int, int] = (2, 2)
    shadow_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.7)
    prefix: str = ""
    format: str | None = None       # strftime format for date/time_of_day
```

**Content resolver** — resolves each element's `content` field per frame. The optional `format` field (strftime syntax) controls how `date` and `time_of_day` are formatted:
```python
class TextContentResolver:
    def resolve(self, content_type: str, frame_ctx: FrameContext,
                fmt: str | None = None) -> str:
        match content_type:
            case "timecode":
                return self.tc_helper.tc_from_frame(frame_ctx.frame_number)
            case "framecounter":
                return str(frame_ctx.frame_number)
            case "framerange":
                return f"{frame_ctx.seq_start}-{frame_ctx.seq_end}"
            case "time_of_day":
                return datetime.now().strftime(fmt or "%H:%M:%S")
            case "date":
                return datetime.now().strftime(fmt or "%Y-%m-%d")
            case "filename":
                return frame_ctx.filename
            case "sequence_name":
                return frame_ctx.sequence_name
            case _ if content_type.startswith("metadata."):
                key = content_type.removeprefix("metadata.")
                return frame_ctx.exr_metadata.get(key, "")
            case _:
                return self.cli_text.get(content_type, "")
```

**Rendering** — Pillow-based (`ImageDraw.text()`):
- **Custom fonts**: TTF/OTF via `ImageFont.truetype()`
- **Anchor positioning**: Pillow 8.0+ anchor parameter maps to our config anchors
- **Text shadows**: draw text twice — once offset in shadow color, then in main color
- **Static elements** (`date`, `time_of_day`, `sequence_name`, `framerange`, custom) are pre-rendered once before the encode loop
- **Per-frame elements** (`timecode`, `framecounter`, `filename`, `metadata.*`) are rendered each frame
- **`enable` flag** is checked — `enable: false` skips the element entirely

---

### 4.4 Frame Processing Pipeline (`processor.py`)

A **fixed-order list of stateless operations** applied to each frame. Each op takes a numpy array + context, returns a numpy array.

```
EXR Read → RemoveAlpha → OCIO Color → Crop → Resize → Cropmask → StaticText → FrameText
```

- **Order is fixed by the orchestrator** — correctness concern: color transform must happen before resize (avoids banding), text must happen after resize (avoids scaling artifacts)
- **Each op is individually skippable** — if its config section is absent or `enable: false`, the op is a no-op
- **Not user-configurable** — the pipeline order is an implementation detail. Users control *what* each op does (via `codecs.yaml` and `daily.yaml`), not the ordering
- Adding a new operation = one class + one line in the orchestrator's `build_ops()`

```python
class FrameProcessor:
    def __init__(self, ops: list[ImageOp]):
        self.ops = ops  # each: (np.ndarray, FrameContext) -> np.ndarray

    def process(self, frame_path: Path, context: FrameContext) -> np.ndarray:
        buf = read_exr_as_numpy(frame_path)  # float32 numpy array
        for op in self.ops:
            buf = op(buf, context)
        return buf
```

Default ops list assembled by orchestrator:
```python
ops = [
    RemoveAlphaOp(),
    OCIOOp(ocio_processor),
    CropOp(config),
    ResizeOp(config),
    CropmaskOp(config),
    StaticTextOp(static_layer),       # pre-built, composited each frame
    FrameTextOp(renderer, resolver),  # per-frame: timecode, framecounter
]
```

---

### 4.5 Encode Layer (`encode.py`)

Context manager, ffmpeg stderr always captured and surfaced. Container-level timecode is set via ffmpeg `-timecode` flag so NLEs (Premiere, Resolve, Avid) read it natively.

```python
class FFmpegEncoder:
    def __enter__(self): ...   # spawn subprocess with -timecode flag

    def write_frame(self, buf: np.ndarray):
        try:
            self.proc.stdin.write(buf.tobytes())
        except BrokenPipeError:
            raise EncoderError(self.proc.stderr.read().decode())

    def __exit__(self, *args):
        stdout, stderr = self.proc.communicate()
        if self.proc.returncode != 0:
            raise EncoderError(stderr.decode())
```

---

### 4.6 Timecode Layer (`timecode.py`)

Deduce timecode from frame number and framerate, or reverse (timecode string to frame number). Thin wrapper around the `timecode` PyPI package.

```python
from timecode import Timecode

class TimecodeHelper:
    def __init__(self, framerate: str, start_frame: int = 1001):
        self.framerate = framerate
        self.start_frame = start_frame

    def tc_from_frame(self, frame: int) -> str:
        """Deduce timecode from absolute frame number and framerate."""
        tc = Timecode(self.framerate, frames=frame)
        return str(tc)  # e.g. "00:00:41:17"

    def frame_from_tc(self, timecode_str: str) -> int:
        """Reverse: timecode string to frame number."""
        tc = Timecode(self.framerate, timecode_str)
        return tc.frames
```

**Where timecode is applied:**
1. **Text burn-in** — per-frame `HH:MM:SS:FF` rendered by the text engine's `timecode` content type
2. **File container metadata** — ffmpeg `-timecode` flag written into QuickTime/MOV container
3. **CLI output** — prints sequence timecode range (e.g. `01:02:03:04 – 01:02:13:04`)

```python
# In FFmpegEncoder, container timecode is set from start frame:
tc_helper = TimecodeHelper(framerate, start_frame)
ffmpeg_cmd += ["-timecode", tc_helper.tc_from_frame(start_frame)]
```

Drop-frame timecode supported for 29.97 / 59.94 fps.

---

### 4.7 Slate Frame (`slate.py`)

An optional frame (or N frames) prepended to the video showing a company logo. Since the logo is already sRGB, it bypasses the OCIO pipeline entirely.

**Config in `daily.yaml`:**
```yaml
slate:
  enable: true
  logo: "/path/to/company_logo.png"
  duration_frames: 24             # 1 second at 24fps
  background_color: [0.0, 0.0, 0.0]
  logo_scale: 0.5                 # relative to output resolution
  logo_position: center
```

```python
class SlateGenerator:
    def generate(self, config: SlateConfig, output_size: tuple[int, int]) -> np.ndarray:
        """Build slate frame: logo composited on solid background."""
        bg = np.full((*output_size[::-1], 3), config.background_color, dtype=np.float32)
        logo = Image.open(config.logo).convert("RGBA")
        # scale and center logo, composite onto bg
        return frame  # numpy float32, sRGB
```

In the orchestrator, slate frames are piped to ffmpeg **before** the main sequence loop.

---

### 4.8 Orchestrator (`daily.py`)

Thin, UI-agnostic — wires all layers together. Called by both CLI and TUI (and future web UI). Accepts a validated config, reports progress via callback.

```python
def run(config: DailyConfig, progress_cb=None):
    sequences = discover_sequences(config)
    ocio = OCIOProcessor(config.ocio)
    tc_helper = TimecodeHelper(config.framerate, config.start_frame)
    resolver = TextContentResolver(tc_helper, config.cli_text)
    renderer = TextRenderer(config.output_size)
    slate_gen = SlateGenerator() if config.slate.enable else None
    ops = build_ops(config, ocio, renderer, resolver)
    processor = FrameProcessor(ops)

    for sequence in sequences:
        with FFmpegEncoder(config.codec, output_path(sequence, config),
                           timecode=tc_helper.tc_from_frame(config.start_frame)) as enc:
            # Slate frames first (if enabled)
            if slate_gen:
                slate = slate_gen.generate(config.slate, config.output_size)
                for _ in range(config.slate.duration_frames):
                    enc.write_frame(slate)

            # Main sequence
            for frame in sequence:
                try:
                    buf = processor.process(frame.path, frame.context)
                    enc.write_frame(buf)
                except FrameReadError as e:
                    log.warning(f"Skipping unreadable frame: {e}")
                    enc.write_frame(black_frame(config))
                if progress_cb:
                    progress_cb(frame.index, len(sequence))
```

---

## 5. Multithreading Strategy

The pipe write to ffmpeg must stay on the main thread in frame order. Everything else is parallelizable.

**Phase 1 — ship with prefetch model** (hides EXR decode latency, ~50 lines):

```
I/O Thread:    decode frame N+1, N+2 ... into queue
Main Thread:   apply ops → write to pipe
```

**Phase 2 — full producer-consumer** if profiling shows CPU ops are bottleneck:

```
Thread pool:   process frames out of order
Reorder buffer: ensure in-order pipe writes
Main thread:   drain buffer → write to pipe
```

> Note: `OpenEXR` and `Pillow` are C extensions that release the GIL, so `ThreadPoolExecutor` gives real parallelism for I/O-bound work. `multiprocessing` not recommended — IPC cost of passing ~100MB 4.5K frames between processes negates gains.

RAM budget per frame at 4.5K RGBA float32: ~100MB. Keep prefetch buffer ≤ 4–8 frames.

---

## 6. Interface — CLI + TUI

Two execution modes. The orchestrator is UI-agnostic — both modes call the same `daily.run(config)`.

### 6.1 Pure CLI (`cli.py` — `daily encode`)

Non-interactive mode for pipeline integration, scripting, batch processing, render farms, and cron jobs.

```bash
daily encode -i /path/to/seq.%04d.exr -c prores_hq \
    --ocio /path/to/config.ocio --ocio-display sRGB --ocio-view Film \
    --text comment="Final grade" --text artist="John" \
    -o /output.mov
```

Progress via `rich` progress bar in the terminal.

### 6.2 TUI (`ui.py` — `daily ui`)

Interactive mode via **Textual** (`pip install textual`). Works over SSH, no browser/JS needed.

```
┌──────────────────────────────────────────────────────────────┐
│  Daily                                                       │
├──────────────────────────────────────────────────────────────┤
│  Input Sequence  [/path/to/shots/M02-0014/      ] [Browse]  │
│  Codec           [avchq                        ▼]           │
│  OCIO Profile    [grade                        ▼]           │
│  Dailies Profile [internal                     ▼]           │
│  Output Path     [~/tmp/dailies                ] [Browse]   │
│                                                              │
│  ── Text Overlays ──────────────────────────────────────    │
│  Artist          [                             ]            │
│  Comment         [                             ]            │
│                                                              │
│  ── Auto-detected ──────────────────────────────────────    │
│  Frames          1001 – 1240  (240 frames)                  │
│  Resolution      4096 x 3072                                │
│  Timecode        00:00:41:17 – 00:00:51:17                  │
│  Format          EXR / DWAB                                 │
│                                                             │
│                                        [  Launch  ]         │
├──────────────────────────────────────────────────────────────┤
│  M02-0014  ━━━━━━━━━━━━━━  187/240  78%  12.3fps  ETA 0:00:26 │
│  ✓ M02-0013  complete                                       │
│  ✗ M02-0012  failed — OCIO colorspace not found            │
└──────────────────────────────────────────────────────────────┘
```

| UI element | Textual widget |
|---|---|
| Dropdowns (codec, profile) | `Select` |
| Text inputs | `Input` |
| File browser | `DirectoryTree` in `ModalScreen` |
| Progress bars | `ProgressBar` (rich integration) |
| Launch button | `Button` |

Custom widget: `DirectoryTree` subclass to detect EXR sequences via `pyseq` and show as single selectable items. Check `textual-fspicker` on PyPI first.

### 6.3 Architecture — UI-agnostic core

```
daily.py (orchestrator)        ← core, UI-agnostic
  ↑           ↑           ↑
cli.py      ui.py      future: web.py
(argparse)  (Textual)  (FastAPI/Flask)
```

- `cli.py` dispatches via argparse subcommands: `encode` (headless) and `ui` (launches Textual)
- `daily.py` accepts a validated `DailyConfig`, reports progress via callback
- Progress callback: CLI gets `rich` bar, TUI gets Textual widget, web UI would get SSE/websocket
- Adding a web UI later = new `web.py` that builds config from HTTP request and calls `daily.run(config)` — zero changes to core

---

## 7. Dependencies

**All pip-installable:**
```
OpenEXR>=3.2        # EXR read/write with numpy arrays
PyOpenColorIO       # OCIO color transforms on numpy arrays
numpy               # array ops (crop, composite, resize)
Pillow>=8.0         # text rendering with anchor support, image resize
pyyaml              # config loading
pyseq               # sequence discovery
rich                # CLI progress bars
pydantic            # config validation
timecode            # frame <-> timecode conversion
textual             # TUI (optional, only for `daily ui`)
```

**System dependency:**
```
ffmpeg              # brew install ffmpeg / apt install ffmpeg
```

> `pip install ffmpeg` installs a useless wrapper — the actual binary must be installed via system package manager.

No conda. No OIIO. Single code path using numpy arrays throughout.

### Environment setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# ffmpeg: brew install ffmpeg (macOS) / apt install ffmpeg (Linux)
```

### Project files to include
- `pyproject.toml` — dependencies + `daily` entry point
- `requirements.txt` — pinned versions
- `config/codecs.yaml` — codec presets
- `config/daily.yaml` — user config (OCIO, text layout, slate, output settings)

---

## 8. Things Explicitly Out of Scope (v1)

- PyAV rewrite of encode layer — pipe-to-ffmpeg is sound, not worth the complexity
- `ffmpeg-python` library — adds nothing over current pipe approach for this use case
- Web UI — architecture supports it (UI-agnostic orchestrator, see 6.3), but not in v1
- `multiprocessing` for parallelism — IPC cost too high for large frames
- User-configurable pipeline ordering — correctness risk outweighs flexibility

---

## 9. Build Order Recommendation

1. `config.py` — pydantic models, yaml loading, validation. Everything depends on this.
2. `sequence.py` — pyseq wrapper, frame iteration
3. `timecode.py` — timecode <-> frame conversion
4. `color.py` — PyOpenColorIO processor, all three transform types
5. `text.py` — modular content resolver + Pillow renderer
6. `processor.py` — ops list pipeline
7. `encode.py` — ffmpeg context manager with stderr capture + container timecode
8. `slate.py` — slate frame generator
9. `daily.py` — orchestrator wiring all layers
10. `cli.py` — argparse with `encode` subcommand, calls orchestrator
11. `ui.py` — Textual TUI, built on top of completed CLI layer
12. Prefetch threading — added to orchestrator once single-threaded version is verified correct