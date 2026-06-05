from __future__ import annotations

import argparse
import logging
import sys
import types as _builtins_types
from pathlib import Path
from typing import Any, Union, get_args, get_origin

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal  # type: ignore

from pydantic import BaseModel


def _is_union(origin: Any) -> bool:
    """True for both typing.Union and Python 3.10+ X | Y union types."""
    if origin is Union:
        return True
    if hasattr(_builtins_types, "UnionType") and origin is _builtins_types.UnionType:
        return True
    return False

# Ensure UTF-8 output on Windows regardless of terminal code page
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from .config import DailyConfig, _coerce, build_config, load_codecs, load_text_elements, load_user_config

# Fields that exist on DailyConfig but are not exposed as CLI flags
_SKIP_FIELDS = {
    "codecs", "cli_text", "input_path", "output_path_override", "text_elements",
    "model_config",
}


def _is_simple(annotation: Any) -> bool:
    """True for str, int, float, bool, Path, Literal, and Optional of these."""
    origin = get_origin(annotation)
    if _is_union(origin):
        inner = [a for a in get_args(annotation) if a is not type(None)]
        return len(inner) == 1 and _is_simple(inner[0])
    if origin is Literal:
        return True
    return annotation in (str, int, float, bool) or (
        isinstance(annotation, type) and issubclass(annotation, Path)
    )


def _add_leaf(
    parser: argparse.ArgumentParser,
    flag: str,
    dot_path: str,
    annotation: Any,
    mapping: list[tuple[str, str]],
) -> None:
    dest = flag.replace("-", "_")
    base = annotation
    origin = get_origin(annotation)
    if _is_union(origin):
        base = next(a for a in get_args(annotation) if a is not type(None))
    if base is bool:
        parser.add_argument(
            f"--{flag}", dest=dest,
            action=argparse.BooleanOptionalAction, default=None,
        )
    else:
        parser.add_argument(f"--{flag}", dest=dest, default=None, metavar="VALUE")
    mapping.append((dest, dot_path))


def _register_config_flags(
    parser: argparse.ArgumentParser,
    model_class: type[BaseModel],
    prefix: str = "",
    dot_prefix: str = "",
) -> list[tuple[str, str]]:
    """Walk model_class fields and register argparse flags for scalar leaves.

    Returns list of (argparse_dest, dot_path) pairs for value extraction later.
    """
    mapping: list[tuple[str, str]] = []
    for name, field in model_class.model_fields.items():
        if name in _SKIP_FIELDS:
            continue
        ann = field.annotation
        flag = (prefix + name).replace("_", "-")
        dot = (dot_prefix + "." + name).lstrip(".")
        origin = get_origin(ann)

        if isinstance(ann, type) and issubclass(ann, BaseModel):
            mapping += _register_config_flags(parser, ann, prefix=flag + "-", dot_prefix=dot)
        elif origin is Union:
            inner = [a for a in get_args(ann) if a is not type(None)]
            if (
                len(inner) == 1
                and isinstance(inner[0], type)
                and issubclass(inner[0], BaseModel)
            ):
                mapping += _register_config_flags(
                    parser, inner[0], prefix=flag + "-", dot_prefix=dot
                )
            elif _is_simple(ann):
                _add_leaf(parser, flag, dot, ann, mapping)
        elif _is_simple(ann):
            _add_leaf(parser, flag, dot, ann, mapping)

    return mapping


def _parse_text_args(raw: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw or []:
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                f"--text expects KEY=VALUE, got: {item!r}"
            )
        k, _, v = item.partition("=")
        result[k.strip()] = v
    return result


def cmd_encode(args: argparse.Namespace) -> None:
    from . import daily

    # ── Load + merge config ──────────────────────────────────────────────────
    user_yaml = load_user_config(Path(args.config) if args.config else None)
    codecs = load_codecs(Path(args.codecs) if args.codecs else None)
    text_font, text_enable, text_elements = load_text_elements(Path(args.text_elements) if args.text_elements else None)

    cli_overrides: dict = {
        "input_path": Path(args.input),
        "cli_text": _parse_text_args(args.text),
    }

    if args.codec:
        cli_overrides["codec"] = args.codec

    # Output path / directory
    if args.output:
        out = Path(args.output)
        if out.suffix.lower() in {".mov", ".mp4", ".mxf", ".mkv"}:
            cli_overrides["output_path_override"] = out
        else:
            cli_overrides["output_dir"] = out

    # Collect auto-generated config flag overrides
    set_overrides: dict[str, Any] = {}
    for attr, dot_path in args.config_flag_map:
        val = getattr(args, attr, None)
        if val is not None:
            set_overrides[dot_path] = val if isinstance(val, bool) else _coerce(str(val))
    cli_overrides["set_overrides"] = set_overrides

    try:
        config = build_config(user_yaml, codecs, text_elements, text_font, text_enable, cli_overrides=cli_overrides)
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Progress UI ──────────────────────────────────────────────────────────
    with Progress(
        SpinnerColumn(spinner_name="line"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        transient=False,
    ) as progress:
        task = progress.add_task("Encoding", total=None)

        def on_progress(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        try:
            daily.run(config, progress_cb=on_progress, verbose=args.verbose)
        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="daily",
        description="Convert EXR sequences to display-referred QuickTime movies.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── encode subcommand ────────────────────────────────────────────────────
    enc = sub.add_parser("encode", help="Encode one or more EXR sequences")
    enc.add_argument(
        "-i", "--input", required=True, metavar="PATH",
        help="Input EXR sequence: directory, single file, or pyseq pattern",
    )
    enc.add_argument(
        "-c", "--codec", metavar="NAME",
        help="Codec preset name (shorthand for --output-codec)",
    )
    enc.add_argument(
        "-o", "--output", metavar="PATH",
        help="Output file (*.mov) or directory for named outputs",
    )
    enc.add_argument(
        "--config", metavar="FILE",
        help="Path to daily.yaml (default: ./daily.yaml or bundled template)",
    )
    enc.add_argument(
        "--codecs", metavar="FILE",
        help="Path to codecs.yaml (default: bundled)",
    )
    enc.add_argument(
        "--text-elements", metavar="FILE",
        help="Path to text_elements.yaml (default: ./text_elements.yaml or bundled)",
    )
    enc.add_argument(
        "--text", action="append", metavar="KEY=VALUE",
        help="Custom text overlay value, repeatable (e.g. --text artist=Jane)",
    )
    enc.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )

    # Auto-generate flags for every scalar field in DailyConfig
    flag_map = _register_config_flags(enc, DailyConfig)
    enc.set_defaults(func=cmd_encode, config_flag_map=flag_map)

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)

    args.func(args)


if __name__ == "__main__":
    main()
