# Web UI

`daily` ships an **optional** [Gradio](https://www.gradio.app/) web UI that exposes everything the
CLI does, with **dropdowns for OCIO colourspaces, displays, views, and looks** (populated from your
`.ocio` config) instead of free-text strings.

The web UI is an optional extra — the core CLI and library do not depend on Gradio.

## Install & launch

```bash
pip install ".[web]"     # installs Gradio and the daily-web command
daily-web                # launches a local server and opens your browser
```

This starts a local server and prints a URL (e.g. `http://127.0.0.1:7860`). FFmpeg must be on your
`PATH` — unlike the CLI, the web UI has no `--ffmpeg` flag.

It runs **locally** and reads paths directly from the local filesystem; nothing is uploaded.

## Features

- **Config files at the top** — point the UI at your `daily.yaml`, `codecs.yaml`, and
  `text_overlays.yaml`. These drive every default in the form. A **Reload** button re-reads them and
  repopulates the whole form. Paths inside the config are resolved relative to the `daily.yaml`, so
  it works regardless of where you launched `daily-web` from.
- **OCIO dropdowns** — enter your OCIO config path, click **Load config**, and pick colourspaces,
  displays, views, and looks from dropdowns. The view list updates automatically when you change the
  display.
- **Preview sequences** — point it at an input path or glob and preview the matched sequences, their
  frame counts, and the expected output paths *before* encoding.
- **Live progress** — the progress bar shows which sequence is currently encoding. Encodes are
  queued and run sequentially, never concurrently.
- **Stop** — cancels an in-progress encode, kills the ffmpeg process, and removes the partial file.

## See also

- **[CLI reference](CLI.md)** — the command-line equivalent of every option
- **[Configuration](CONFIGURATION.md)** — the YAML files the form reads its defaults from
