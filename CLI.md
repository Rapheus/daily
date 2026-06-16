# CLI Reference

```
daily -i PATH [options]
```

Only `-i` / `--input` is required. All other flags are optional and fall back to the values in
`daily.yaml` (or the bundled defaults) when omitted. Every scalar field in `daily.yaml` has a
corresponding `--` flag — anything you can set in the config file can be overridden on the command
line. See **[Configuration](CONFIGURATION.md)** for the YAML files.

**Required**

| Flag | Description |
|------|-------------|
| `-i, --input PATH` | EXR directory, single file, or glob (`shots/**/*.exr`) |

**Output**

| Flag | Description |
|------|-------------|
| `-o, --output PATH` | Output `.mov` / `.mp4` file or directory; default: write next to source frames |
| `-c, --codec NAME` | Codec preset from `codecs.yaml`; shorthand for `--output-codec`; default: `h264_hq` |
| `--output-codec NAME` | Codec preset name; default: `h264_hq` |
| `--output-resolution WxH` | Output resolution (e.g. `1920x1080`); default: source EXR resolution |
| `--output-framerate VALUE` | Frame rate (e.g. `25`, `29.97`); default: `24` |
| `--output-threads N` | Parallel frame-processing threads; default: CPU core count |
| `--output-trim-overscan` / `--no-output-trim-overscan` | Crop data window to display window; default: on |

**Config files**

| Flag | Description |
|------|-------------|
| `--config FILE` | Path to `daily.yaml`; default: `./daily.yaml` or bundled |
| `--codecs FILE` | Path to `codecs.yaml`; default: bundled |
| `--text-overlays FILE` | Path to `text_overlays.yaml`; default: `./text_overlays.yaml` or bundled |
| `--ffmpeg PATH` | Explicit path to `ffmpeg` binary; default: search `PATH` |

**Colour**

| Flag | Description |
|------|-------------|
| `--ocio-config PATH` | OCIO config path; default: `$OCIO` env var |
| `--ocio-transform-type TYPE` | `colorconvert` \| `display` \| `look` |
| `--ocio-transform-src NAME` | Source colourspace |
| `--ocio-transform-dst NAME` | Destination colourspace (for `colorconvert` / `look`) |
| `--ocio-transform-display NAME` | Display (for `display` type) |
| `--ocio-transform-view NAME` | View (for `display` type) |

**Slate**

| Flag | Description |
|------|-------------|
| `--slate-enable` / `--no-slate-enable` | Toggle slate; default: off |
| `--slate-frame-path PATH` | Path to slate image |
| `--slate-duration-frames N` | Hold slate for N frames; default: `1` |
| `--slate-fit MODE` | `horizontal` \| `vertical`; default: `horizontal` |
| `--slate-ocio-transform` / `--no-slate-ocio-transform` | Apply the colour pipeline to the slate (for linear EXR slates); default: off |

**Cropmask**

| Flag | Description |
|------|-------------|
| `--cropmask-enable` / `--no-cropmask-enable` | Toggle aspect-ratio bars; default: off |
| `--cropmask-aspect FLOAT` | Target aspect ratio, e.g. `1.85`, `2.39`; default: `1.85`. Bars appear only when this is **wider** than the source aspect |
| `--cropmask-opacity FLOAT` | Bar opacity 0–1; default: `0.7` |

**Text overlays**

| Flag | Description |
|------|-------------|
| `--text KEY=VALUE` | Inject a custom overlay value (repeatable, e.g. `--text artist=Jane`) |
| `--text-enable` / `--no-text-enable` | Toggle all text overlays; default: on |
| `--text-font PATH` | Default font for overlays; default: bundled `Vera.ttf` |

**Misc**

| Flag | Description |
|------|-------------|
| `-v, --verbose` | Verbose / debug logging (also shows the full ffmpeg log) |

---

## Output to stdout

`daily` prints one produced file path per line to **stdout**; progress and logs go to **stderr**.
This lets you capture produced files without parsing logs:

```bash
OUTPUT=$(daily -i shots/ -o reviews/)            # capture into a variable
daily -i shots/ -o reviews/ > paths.txt          # paths to file, logs to terminal
daily -i shots/ -o reviews/ | xargs -I{} cp {} /archive/
```
