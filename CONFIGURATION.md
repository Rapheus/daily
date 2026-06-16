# Configuration

Three YAML files control every aspect of `daily`. Copy the files from `config/` into your project
directory to customise them, or point at them explicitly with `--config` / `--codecs` /
`--text-overlays`. Resolution order for each file: **explicit flag → `./<file>` in the current
directory → bundled default in `config/`**. Every scalar key can also be overridden from the command
line — see the **[CLI reference](CLI.md)**.

| File | Purpose |
|------|---------|
| `daily.yaml` | Main settings: OCIO transform, output codec / resolution / frame rate, cropmask, slate |
| `codecs.yaml` | FFmpeg codec presets |
| `text_overlays.yaml` | Text overlay elements: anchor, offset, font, size, colour, shadow |

Relative paths inside the YAML (OCIO config, slate frame, fonts) are resolved **relative to the
config file's location**, not the working directory, so they work regardless of where you launch from.

---

## `daily.yaml`

```yaml
ffmpeg: null          # null = search PATH; or "C:/ffmpeg/bin/ffmpeg.exe"

ocio:
  config: "$OCIO"     # path to .ocio file, or an env var reference like "$OCIO"
  transform:
    type: display     # colorconvert | display | look
    src: "ACES - ACEScg"
    display: "sRGB - Display"
    view: "ACES 2.0 - SDR 100 nits (Rec.709)"   # must match a view in your OCIO config
    looks: []

output:
  codec: h264_hq      # key in codecs.yaml
  resolution: null    # [1920, 1080] or null to use source EXR resolution
  framerate: "24"
  directory: "."
  trim_overscan: true

cropmask:
  enable: false
  aspect: 1.85        # target aspect ratio; bars appear only when wider than the source
  opacity: 0.7

slate:
  enable: false
  frame_path: null    # path to slate image (relative paths are resolved against this file)
  duration_frames: 1
  fit: horizontal     # horizontal (match width) | vertical (match height)
  ocio_transform: false   # set true when frame_path is a linear EXR
```

**OCIO transform types** — `display` (ACEScg → display/view, the canonical dailies path) needs
`src`, `display`, `view`; `colorconvert` needs `src`, `dst`; `look` needs `src`, `dst`, `looks`.

---

## `codecs.yaml`

Named codec presets. `codec` maps to an FFmpeg encoder, with optional `pix_fmt`, `crf`, and free-form
`ffmpeg_args`.

```yaml
h264_hq:
  codec: libx264
  pix_fmt: yuv420p
  crf: 18
  ffmpeg_args: "-preset slower -tune film"

prores_hq:
  codec: prores_ks
  pix_fmt: yuv422p10le
  ffmpeg_args: "-profile:v 3 -vendor ap10 -qscale:v 7"

prores_4444:
  codec: prores_ks
  pix_fmt: yuv444p12le
  ffmpeg_args: "-profile:v 4 -vendor ap10 -qscale:v 5"
```

Bundled presets: `h264_hq`, `h264_lq`, `prores_hq`, `prores_4444`, `prores_proxy`, `dnxhr_hqx`,
`dnxhr_sq`, `hevc_hq`.

---

## `text_overlays.yaml`

Each element in `elements` is rendered independently with its own anchor, offset, font, size, colour,
and shadow. `font_size` and `offset` values are authored at 1080p and scale automatically to the
output resolution.

```yaml
font: Vera.ttf   # default font for all elements (null = bundled Vera); bare names use the bundled fonts dir
enable: true

elements:
  - content: sequence_name   # static: same value every frame
    anchor: top-left
    offset: [28, 28]
    font_size: 28
    color: [1.0, 1.0, 1.0, 1.0]
    shadow: true

  - content: timecode        # dynamic: recalculated every frame
    anchor: bottom-center
    offset: [0, 28]
    font_size: 28
    color: [1.0, 1.0, 1.0, 1.0]
    shadow: true

  - content: user            # custom CLI key: --text user="Jane Smith"
    anchor: bottom-left
    offset: [28, 28]
    default: ""
    font_size: 28
    color: [1.0, 1.0, 1.0, 1.0]
    shadow: true
```

**Anchor positions** (7): `top-left`, `top-center`, `top-right`, `bottom-left`, `bottom-center`,
`bottom-right`, `center`. Elements sharing an anchor stack outward from the edge in YAML order.

**Content types:**

| Type | Description |
|------|-------------|
| `timecode` | SMPTE timecode, recalculated per frame |
| `framecounter` | Absolute frame number |
| `framerange` | First–last frame of the sequence |
| `date` | Encoding date (supports `format:` strftime string) |
| `time_of_day` | Encoding time (supports `format:`) |
| `filename` | Source EXR filename |
| `sequence_name` | Sequence name |
| `metadata.<key>` | Value from the EXR header |
| `<custom-key>` | Any key passed via `--text key=value` |
