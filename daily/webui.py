from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import gradio as gr

from . import daily as _daily
from .color import list_ocio_options
from .config import (
    build_config,
    build_daily_config,
    load_codecs,
    load_text_overlays,
    load_user_config,
)
from .sequence import discover_sequences


def _codec_choices(codecs_path: str | None = None) -> list[str]:
    try:
        return list(load_codecs(Path(codecs_path) if codecs_path else None).keys())
    except Exception:
        return []


def _with(choices: list, value) -> list:
    """Ensure a single default value is present in a dropdown's choice list."""
    if value and value not in choices:
        return [*choices, value]
    return choices


def _with_all(choices: list, values: list) -> list:
    """Ensure every default value is present in a multiselect's choice list."""
    extra = [v for v in (values or []) if v not in choices]
    return [*choices, *extra]


def _repo_root() -> Path:
    """Repository root (the directory containing the ``daily`` package)."""
    return Path(__file__).resolve().parent.parent


def _resolve_repo_relative(raw: str) -> str:
    """Normalise a user-entered path to an absolute, forward-slash string.

    - Empty / ``$VAR`` env refs are returned unchanged.
    - Absolute paths (and ``~``) are honoured as-is.
    - A relative path (e.g. ``./config/daily.yaml`` or ``config/daily.yaml``) is
      resolved against the repository root, so ``./``-style paths work no matter
      where daily-web was launched. It falls back to the launch directory only
      when that's where the file actually exists.

    Glob patterns are anchored on the repo root (their existence can't be
    tested) but absolute globs pass straight through.
    """
    if not raw or raw.startswith("$"):
        return raw
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.as_posix()
    is_glob = any(c in raw for c in "*?[")
    repo_cand = _repo_root() / p
    cwd_cand = Path.cwd() / p
    if not is_glob and cwd_cand.exists() and not repo_cand.exists():
        return cwd_cand.resolve().as_posix()
    return repo_cand.resolve().as_posix()


def _norm(*paths: str) -> tuple[str, ...]:
    """Normalise several path fields through :func:`_resolve_repo_relative`."""
    return tuple(_resolve_repo_relative(p) for p in paths)


def _default_config_paths() -> dict[str, str]:
    """Absolute paths to the config files daily would load by default.

    Mirrors the cwd → bundled resolution in config.load_user_config /
    load_text_overlays so the form shows the real file being used.
    """
    from .config import _bundled

    def pick(local: str, bundled_name: str) -> str:
        p = Path(local)
        target = p if p.exists() else _bundled(bundled_name)
        return target.resolve().as_posix()

    return {
        "daily": pick("daily.yaml", "config/daily.yaml"),
        "codecs": _bundled("config/codecs.yaml").resolve().as_posix(),
        "text_overlays": pick("text_overlays.yaml", "config/text_overlays.yaml"),
    }


def _resolve_relative_to_yaml(raw: str, yaml_path: str | None) -> Path | None:
    """Resolve a path that may be relative *to the daily.yaml location*.

    Anchoring on the config file (rather than the process working directory)
    makes resolution independent of where daily-web was launched from. Both the
    yaml's own directory and its parent are tried, so paths stored relative to
    either the config dir (e.g. ``ocio/studio-config.ocio``) or the project root
    still resolve.
    """
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p if p.exists() else None
    if yaml_path:
        yaml_dir = Path(yaml_path).resolve().parent
        for base in (yaml_dir, yaml_dir.parent):
            cand = (base / p).resolve()
            if cand.exists():
                return cand
    # Fall back to the repository root (so ``./``-relative paths work), then the
    # launch directory.
    for base in (_repo_root(), Path.cwd()):
        cand = (base / p).resolve()
        if cand.exists():
            return cand
    return None


def _resolve_ocio(ocio_config: str, yaml_path: str | None = None) -> Path | None:
    """Resolve an OCIO config path: literal → $VAR → $OCIO, yaml-relative.

    Relative literal paths are resolved against the daily.yaml location, never
    the launch directory.
    """
    raw = (ocio_config or "").strip()
    if not raw:
        raw = os.environ.get("OCIO", "")
    elif raw.startswith("$"):
        raw = os.environ.get(raw[1:], "")
    if not raw:
        return None
    return _resolve_relative_to_yaml(raw, yaml_path)


def _compute_form_defaults(
    daily_path: str | None = None,
    codecs_path: str | None = None,
    text_overlays_path: str | None = None,
) -> dict[str, Any]:
    """Read the config files and produce every initial value for the form.

    Shared by the initial render and the Reload button so both stay in sync.
    OCIO/slate paths are resolved absolute against the daily.yaml location;
    input/output default to the launch directory.
    """
    paths = _default_config_paths()
    eff_daily = _resolve_repo_relative(daily_path) if daily_path else paths["daily"]
    eff_codecs = _resolve_repo_relative(codecs_path) if codecs_path else paths["codecs"]
    eff_text = (
        _resolve_repo_relative(text_overlays_path) if text_overlays_path else paths["text_overlays"]
    )

    codec_choices = _codec_choices(eff_codecs)

    cfg = None
    try:
        user_yaml = load_user_config(Path(eff_daily))
        codecs = load_codecs(Path(eff_codecs))
        text_font, text_enable_yaml, text_overlays = load_text_overlays(Path(eff_text))
        cfg = build_config(
            user_yaml, codecs, text_overlays, text_font, text_enable_yaml,
            config_dir=Path(eff_daily).resolve().parent,
        )
    except Exception:
        cfg = None

    # OCIO config resolved absolute (yaml-relative) + enumerated for dropdowns.
    ocio_config = cfg.ocio.config if cfg else ""
    ocio_opts = {"colorspaces": [], "displays": [], "views": {}, "looks": []}
    if cfg:
        p = _resolve_ocio(cfg.ocio.config, eff_daily)
        if p is not None:
            ocio_config = p.as_posix()
            ocio_opts = list_ocio_options(p)

    display = cfg.ocio.transform.display if cfg else ""

    # Input/output default to the launch directory (where daily-web was run).
    # The input defaults to a recursive glob so every sequence under the launch
    # directory's hierarchy is discovered.
    cwd = Path.cwd()
    out_dir = cfg.output.directory if cfg and cfg.output.directory else Path(".")
    output = (out_dir if out_dir.is_absolute() else (cwd / out_dir)).resolve().as_posix()
    input_default = cwd.as_posix().rstrip("/") + "/**"

    return {
        "codec_choices": codec_choices,
        "codec": cfg.output.codec if cfg else (codec_choices[0] if codec_choices else None),
        "resolution": (
            f"{cfg.output.resolution[0]}x{cfg.output.resolution[1]}"
            if cfg and cfg.output.resolution else ""
        ),
        "framerate": cfg.output.framerate if cfg else "24",
        "threads": cfg.output.threads if cfg else None,
        "trim": cfg.output.trim_overscan if cfg else True,
        "ocio_config": ocio_config,
        "src": cfg.ocio.transform.src if cfg else "",
        "display": display,
        "view": cfg.ocio.transform.view if cfg else "",
        "looks": cfg.ocio.transform.looks if cfg else [],
        "cs": ocio_opts["colorspaces"],
        "displays": ocio_opts["displays"],
        "views_map": ocio_opts["views"],
        "views_for_display": ocio_opts["views"].get(display, []),
        "looks_all": ocio_opts["looks"],
        "slate_enable": cfg.slate.enable if cfg else False,
        "slate_path": (
            cfg.slate.frame_path.resolve().as_posix() if cfg and cfg.slate.frame_path else ""
        ),
        "slate_dur": cfg.slate.duration_frames if cfg else 24,
        "slate_fit": cfg.slate.fit if cfg else "horizontal",
        "slate_ocio": cfg.slate.ocio_transform if cfg else False,
        "crop_enable": cfg.cropmask.enable if cfg else False,
        "crop_aspect": cfg.cropmask.aspect if cfg else 1.85,
        "crop_opacity": cfg.cropmask.opacity if cfg else 0.7,
        "text_enable": cfg.text_enable if cfg else True,
        "input": input_default,
        "output": output,
    }


def _build_set_overrides(
    ocio_config: str,
    transform_src: str,
    transform_display: str,
    transform_view: str,
    transform_looks: list[str],
    slate_enable: bool,
    slate_duration_frames: int | None,
    trim_overscan: bool,
    framerate: str,
    resolution: str,
    threads: int | None,
    cropmask_enable: bool,
    cropmask_aspect: float | None,
    cropmask_opacity: float | None,
) -> dict[str, Any]:
    ov: dict[str, Any] = {}
    if ocio_config:
        ov["ocio.config"] = _resolve_repo_relative(ocio_config)
    # Override the OCIO transform as one complete dict (rather than piecemeal
    # dot-paths) so stale fields can't leak in from daily.yaml. The web UI always
    # builds a source → display/view transform with optional look(s). Only
    # override when the user actually picked a source colourspace; otherwise fall
    # back to daily.yaml's transform.
    if transform_src:
        ov["ocio.transform"] = {
            "type": "display",
            "src": transform_src,
            "display": transform_display or None,
            "view": transform_view or None,
            "looks": list(transform_looks or []),
        }
    ov["slate.enable"] = bool(slate_enable)
    if slate_duration_frames:
        ov["slate.duration_frames"] = int(slate_duration_frames)
    ov["output.trim_overscan"] = bool(trim_overscan)
    if framerate:
        ov["output.framerate"] = framerate
    if resolution:
        w, _, h = resolution.partition("x")
        if w.strip().isdigit() and h.strip().isdigit():
            ov["output.resolution"] = (int(w.strip()), int(h.strip()))
    if threads:
        ov["output.threads"] = int(threads)
    ov["cropmask.enable"] = bool(cropmask_enable)
    if cropmask_aspect is not None:
        ov["cropmask.aspect"] = float(cropmask_aspect)
    if cropmask_opacity is not None:
        ov["cropmask.opacity"] = float(cropmask_opacity)
    return ov


def _build_config(
    input_glob: str,
    output_path: str,
    codec: str,
    config_path: str,
    codecs_path: str,
    text_overlays_path: str,
    set_ov: dict[str, Any],
    *,
    text: dict[str, str] | None = None,
) -> Any:
    """Wrap :func:`build_daily_config` with the form's path-to-arg plumbing."""
    def _opt(s: str) -> Path | None:
        return Path(s) if s else None

    return build_daily_config(
        input_path=input_glob,
        output=output_path or None,
        codec=codec or None,
        text=text or None,
        set_overrides=set_ov,
        config_path=_opt(config_path),
        codecs_path=_opt(codecs_path),
        text_overlays_path=_opt(text_overlays_path),
    )


def _preview(
    input_glob: str,
    output_path: str,
    codec: str,
    config_path: str,
    codecs_path: str,
    text_overlays_path: str,
    ocio_config: str,
    transform_src: str,
    transform_display: str,
    transform_view: str,
    transform_looks: list[str],
    slate_enable: bool,
    slate_duration_frames: int | None,
    trim_overscan: bool,
    framerate: str,
    resolution: str,
    threads: int | None,
    cropmask_enable: bool,
    cropmask_aspect: float | None,
    cropmask_opacity: float | None,
) -> str:
    if not input_glob:
        return "Enter an input path or glob pattern first."
    input_glob, output_path, config_path, codecs_path, text_overlays_path = _norm(
        input_glob, output_path, config_path, codecs_path, text_overlays_path
    )
    try:
        seqs = discover_sequences(Path(input_glob))
        set_ov = _build_set_overrides(
            ocio_config, transform_src,
            transform_display, transform_view, transform_looks,
            slate_enable, slate_duration_frames, trim_overscan,
            framerate, resolution, threads,
            cropmask_enable, cropmask_aspect, cropmask_opacity,
        )
        config = _build_config(
            input_glob, output_path, codec,
            config_path, codecs_path, text_overlays_path, set_ov,
        )
        out_paths = _daily.compute_output_paths(config, seqs)
        slate_n = config.slate.duration_frames if config.slate.enable else 0
        total_frames = sum(len(s) + slate_n for s in seqs)
        lines = [f"Found {len(seqs)} sequence(s):"]
        for seq, out in zip(seqs, out_paths):
            lines.append(f"  {seq.frames[0].path}  ({len(seq)} frames)")
            lines.append(f"    → {out}")
        lines.append(f"Total: {total_frames} frames")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def _load_ocio(config_path: str, daily_path: str = "") -> tuple:
    p = _resolve_ocio(config_path, daily_path or None)
    if p is None:
        raise gr.Error(
            "OCIO config not found. Enter a valid path (absolute, or relative to "
            "your daily.yaml), or set the $OCIO environment variable."
        )
    opts = list_ocio_options(p)
    cs = opts["colorspaces"]
    displays = opts["displays"]
    looks = opts["looks"]
    if not cs:
        raise gr.Error(f"No colourspaces found in OCIO config: {p}")
    # Raising above leaves the existing dropdowns untouched, so a failed load
    # never strands a stale value against an empty choice list.
    return (
        gr.update(value=p.as_posix()),
        gr.update(choices=cs),
        gr.update(choices=displays, value=displays[0] if displays else None),
        gr.update(choices=looks),
        opts["views"],
    )


def _browse_folder() -> Any:
    """Open a native folder picker and return the chosen dir as a recursive glob.

    The OS dialog runs on the machine hosting daily-web (the same machine, since
    it opens a local browser). On cancel — or if no display/tk is available — the
    field is left untouched.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory()
        root.destroy()
    except Exception:
        return gr.update()
    if not folder:
        return gr.update()
    return gr.update(value=Path(folder).as_posix().rstrip("/") + "/**")


def _update_views(display: str, views_state: dict) -> gr.update:
    views = (views_state or {}).get(display, [])
    return gr.update(choices=views, value=views[0] if views else None)


# Set by the Stop button, polled by run()'s should_stop hook. A single flag is
# fine because the Encode button has concurrency_limit=1 (one encode at a time).
_STOP = threading.Event()


def _stop() -> str:
    _STOP.set()
    return "Stopping… (killing ffmpeg)"


def _encode(
    input_glob: str,
    output_path: str,
    codec: str,
    text_kv: Any,
    config_path: str,
    codecs_path: str,
    text_overlays_path: str,
    text_enable: bool,
    verbose: bool,
    ocio_config: str,
    transform_src: str,
    transform_display: str,
    transform_view: str,
    transform_looks: list[str],
    slate_enable: bool,
    slate_duration_frames: int | None,
    trim_overscan: bool,
    framerate: str,
    resolution: str,
    threads: int | None,
    cropmask_enable: bool,
    cropmask_aspect: float | None,
    cropmask_opacity: float | None,
    slate_frame_path: str,
    slate_fit: str,
    slate_ocio_transform: bool,
    progress: gr.Progress = gr.Progress(),
) -> str:
    if not input_glob:
        raise gr.Error("Input path / glob is required.")

    input_glob, output_path, config_path, codecs_path, text_overlays_path = _norm(
        input_glob, output_path, config_path, codecs_path, text_overlays_path
    )

    cli_text: dict[str, str] = {}
    if text_kv is not None:
        rows = text_kv.values.tolist() if hasattr(text_kv, "values") else text_kv
        for row in rows:
            if row and len(row) >= 2 and row[0]:
                cli_text[str(row[0])] = str(row[1])

    set_ov = _build_set_overrides(
        ocio_config, transform_src,
        transform_display, transform_view, transform_looks,
        slate_enable, slate_duration_frames, trim_overscan,
        framerate, resolution, threads,
        cropmask_enable, cropmask_aspect, cropmask_opacity,
    )
    if slate_frame_path:
        set_ov["slate.frame_path"] = _resolve_repo_relative(slate_frame_path)
    if slate_fit:
        set_ov["slate.fit"] = slate_fit
    set_ov["slate.ocio_transform"] = bool(slate_ocio_transform)
    set_ov["text_enable"] = bool(text_enable)

    try:
        config = _build_config(
            input_glob, output_path, codec,
            config_path, codecs_path, text_overlays_path, set_ov,
            text=cli_text,
        )
    except Exception as e:
        raise gr.Error(f"Config error: {e}")

    def progress_cb(done: int, total: int, desc: str | None = None) -> None:
        progress(done / total, desc=desc or "Encoding")

    _STOP.clear()
    try:
        out_paths = _daily.run(
            config, progress_cb=progress_cb, verbose=verbose,
            should_stop=_STOP.is_set,
        )
    except Exception as e:
        raise gr.Error(f"Encode error: {e}")

    if _STOP.is_set():
        done = "\n".join(f"  {p}" for p in out_paths)
        return "Stopped." + (f"\nCompleted before stop:\n{done}" if out_paths else "")
    return "Encoded:\n" + "\n".join(f"  {p}" for p in out_paths)


def create_app() -> gr.Blocks:
    d = _compute_form_defaults()
    codec_choices = d["codec_choices"]
    cfg_paths = _default_config_paths()

    with gr.Blocks(title="daily") as demo:
        gr.Markdown("# daily — EXR sequence encoder")
        _views_state = gr.State(d["views_map"])

        with gr.Accordion("Config files", open=False):
            gr.Markdown(
                "These YAML files determine the form defaults below. Edit a path "
                "then click **Reload** to re-populate the form from it. Paths "
                "inside the config are resolved relative to the daily.yaml, so "
                "this works regardless of where you launched daily-web."
            )
            config_path_in = gr.Textbox(label="daily.yaml path", value=cfg_paths["daily"])
            codecs_path_in = gr.Textbox(label="codecs.yaml path", value=cfg_paths["codecs"])
            text_overlays_path_in = gr.Textbox(
                label="text_overlays.yaml path", value=cfg_paths["text_overlays"],
            )
            reload_btn = gr.Button("Reload from config files")

        with gr.Accordion("Input / Output", open=True):
            with gr.Row():
                input_glob = gr.Textbox(
                    label="Input path / glob",
                    placeholder="shots/**/*.exr",
                    value=d["input"],
                    scale=4,
                )
                browse_btn = gr.Button("Browse folder…", scale=1)
                preview_btn = gr.Button("Preview sequences", scale=1)
            codec_dd = gr.Dropdown(
                label="Codec",
                choices=codec_choices,
                value=d["codec"] if d["codec"] in codec_choices else (codec_choices[0] if codec_choices else None),
            )
            with gr.Row():
                resolution = gr.Textbox(
                    label="Resolution (WxH)", placeholder="1920x1080", value=d["resolution"],
                )
                framerate = gr.Textbox(label="Framerate", value=d["framerate"])
                threads = gr.Number(
                    label="Threads (blank = auto)", value=d["threads"], precision=0,
                )
                trim_overscan = gr.Checkbox(label="Trim overscan", value=d["trim"])

        with gr.Accordion("OCIO", open=False):
            with gr.Row():
                ocio_config_path = gr.Textbox(
                    label="OCIO config path (blank = $OCIO)",
                    value=d["ocio_config"],
                    scale=4,
                )
                load_ocio_btn = gr.Button("Load config", scale=1)
            transform_src = gr.Dropdown(
                label="Source colourspace",
                choices=_with(d["cs"], d["src"]), value=d["src"] or None,
                allow_custom_value=True,
            )
            transform_display = gr.Dropdown(
                label="Display",
                choices=_with(d["displays"], d["display"]), value=d["display"] or None,
                allow_custom_value=True,
            )
            transform_view = gr.Dropdown(
                label="View",
                choices=_with(d["views_for_display"], d["view"]), value=d["view"] or None,
                allow_custom_value=True,
            )
            transform_looks = gr.Dropdown(
                label="Look",
                choices=_with_all(d["looks_all"], d["looks"]), value=d["looks"],
                multiselect=True,
                allow_custom_value=True,
            )

        with gr.Accordion("Slate", open=False):
            slate_enable = gr.Checkbox(label="Enable slate", value=d["slate_enable"])
            slate_frame_path = gr.Textbox(label="Slate frame path", value=d["slate_path"])
            with gr.Row():
                slate_duration = gr.Number(
                    label="Duration (frames)", value=d["slate_dur"], precision=0,
                )
                slate_fit = gr.Dropdown(
                    label="Fit", choices=["horizontal", "vertical"], value=d["slate_fit"],
                )
                slate_ocio_transform = gr.Checkbox(
                    label="Apply OCIO transform", value=d["slate_ocio"],
                )

        with gr.Accordion("Cropmask", open=False):
            cropmask_enable = gr.Checkbox(label="Enable cropmask", value=d["crop_enable"])
            with gr.Row():
                cropmask_aspect = gr.Number(label="Aspect ratio", value=d["crop_aspect"])
                cropmask_opacity = gr.Slider(
                    label="Opacity", minimum=0.0, maximum=1.0, value=d["crop_opacity"],
                )

        with gr.Accordion("Text overlays", open=False):
            text_enable = gr.Checkbox(label="Enable text overlays", value=d["text_enable"])
            text_kv = gr.Dataframe(
                headers=["Key", "Value"],
                datatype=["str", "str"],
                label="Custom text values (--text KEY=VALUE)",
                interactive=True,
                row_count=(1, "dynamic"),
                col_count=(2, "fixed"),
            )

        output_path = gr.Textbox(label="Output path or directory", value=d["output"])
        verbose = gr.Checkbox(label="Verbose logging", value=False)
        with gr.Row():
            run_btn = gr.Button("Encode", variant="primary", scale=4)
            stop_btn = gr.Button("Stop", variant="stop", scale=1)
        output_box = gr.Textbox(label="Output", interactive=False, lines=6)

        # Inputs shared between preview and encode (the set_overrides block)
        _set_ov_inputs = [
            ocio_config_path,
            transform_src,
            transform_display, transform_view, transform_looks,
            slate_enable, slate_duration,
            trim_overscan, framerate, resolution, threads,
            cropmask_enable, cropmask_aspect, cropmask_opacity,
        ]

        # Components updated by Reload. Returning a {component: value} dict (rather
        # than a positional tuple) keeps each field paired with its value, so the
        # handler and this output list can't silently drift out of order.
        _reload_outputs = [
            input_glob, output_path, codec_dd, resolution, framerate, threads,
            trim_overscan, ocio_config_path, transform_src,
            transform_display, transform_view, transform_looks,
            slate_enable, slate_frame_path, slate_duration, slate_fit,
            slate_ocio_transform, cropmask_enable, cropmask_aspect,
            cropmask_opacity, text_enable, _views_state,
        ]

        def _reload(daily_path: str, codecs_path: str, text_overlays_path: str) -> dict:
            r = _compute_form_defaults(daily_path, codecs_path, text_overlays_path)
            cc = r["codec_choices"]
            return {
                input_glob: r["input"],
                output_path: r["output"],
                codec_dd: gr.update(
                    choices=cc,
                    value=r["codec"] if r["codec"] in cc else (cc[0] if cc else None),
                ),
                resolution: r["resolution"],
                framerate: r["framerate"],
                threads: r["threads"],
                trim_overscan: r["trim"],
                ocio_config_path: r["ocio_config"],
                transform_src: gr.update(choices=_with(r["cs"], r["src"]), value=r["src"] or None),
                transform_display: gr.update(
                    choices=_with(r["displays"], r["display"]), value=r["display"] or None,
                ),
                transform_view: gr.update(
                    choices=_with(r["views_for_display"], r["view"]), value=r["view"] or None,
                ),
                transform_looks: gr.update(
                    choices=_with_all(r["looks_all"], r["looks"]), value=r["looks"],
                ),
                slate_enable: r["slate_enable"],
                slate_frame_path: r["slate_path"],
                slate_duration: r["slate_dur"],
                slate_fit: r["slate_fit"],
                slate_ocio_transform: r["slate_ocio"],
                cropmask_enable: r["crop_enable"],
                cropmask_aspect: r["crop_aspect"],
                cropmask_opacity: r["crop_opacity"],
                text_enable: r["text_enable"],
                _views_state: r["views_map"],
            }

        reload_btn.click(
            fn=_reload,
            inputs=[config_path_in, codecs_path_in, text_overlays_path_in],
            outputs=_reload_outputs,
        )

        browse_btn.click(fn=_browse_folder, outputs=[input_glob])

        preview_btn.click(
            fn=_preview,
            inputs=[
                input_glob, output_path, codec_dd,
                config_path_in, codecs_path_in, text_overlays_path_in,
            ] + _set_ov_inputs,
            outputs=[output_box],
        )

        load_ocio_btn.click(
            fn=_load_ocio,
            inputs=[ocio_config_path, config_path_in],
            outputs=[
                ocio_config_path, transform_src,
                transform_display, transform_looks, _views_state,
            ],
        )

        transform_display.change(
            fn=_update_views,
            inputs=[transform_display, _views_state],
            outputs=[transform_view],
        )

        run_btn.click(
            fn=_encode,
            inputs=[
                input_glob, output_path, codec_dd, text_kv,
                config_path_in, codecs_path_in, text_overlays_path_in,
                text_enable, verbose,
            ] + _set_ov_inputs + [
                slate_frame_path, slate_fit, slate_ocio_transform,
            ],
            outputs=[output_box],
            concurrency_limit=1,
        )

        # The Stop handler must run while an encode occupies its concurrency slot,
        # so give it its own group with room to run concurrently.
        stop_btn.click(fn=_stop, outputs=[output_box], concurrency_limit=None)

    return demo


def main() -> None:
    create_app().launch(inbrowser=True)
