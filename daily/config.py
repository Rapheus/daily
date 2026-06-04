from __future__ import annotations

import os
import shlex
import shutil
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, field_validator, model_validator


# ── Codec preset ──────────────────────────────────────────────────────────────

class CodecPreset(BaseModel):
    name: str
    codec: str
    pix_fmt: str
    crf: int | None = None
    ffmpeg_args: str = ""


# ── OCIO ──────────────────────────────────────────────────────────────────────

class OCIOTransformConfig(BaseModel):
    type: Literal["colorconvert", "display", "look"] = "display"
    src: str = "ACES - ACEScg"
    dst: str | None = None
    display: str | None = "sRGB - Display"
    view: str | None = "ACES 1.0 - SDR Video"
    looks: list[str] = []

    @model_validator(mode="after")
    def check_required_fields(self) -> "OCIOTransformConfig":
        if self.type == "colorconvert" and self.dst is None:
            raise ValueError("colorconvert transform requires 'dst'")
        if self.type == "display" and (self.display is None or self.view is None):
            raise ValueError("display transform requires 'display' and 'view'")
        if self.type == "look" and self.dst is None:
            raise ValueError("look transform requires 'dst'")
        return self


class OCIOConfig(BaseModel):
    config: str = "$OCIO"
    transform: OCIOTransformConfig = OCIOTransformConfig()


# ── Output ────────────────────────────────────────────────────────────────────

class OutputConfig(BaseModel):
    codec: str = "h264_hq"
    resolution: tuple[int, int] | None = None  # None = use source EXR resolution
    framerate: str = "24"
    directory: Path = Path(".")
    trim_overscan: bool = True


# ── Cropmask ──────────────────────────────────────────────────────────────────

class CropmaskConfig(BaseModel):
    enable: bool = False
    aspect: float = 1.85
    opacity: float = 0.7


# ── Slate ─────────────────────────────────────────────────────────────────────

class SlateConfig(BaseModel):
    enable: bool = False
    frame_path: Path | None = None
    duration_frames: int = 24
    fit: Literal["horizontal", "vertical"] = "horizontal"

    @field_validator("frame_path")
    @classmethod
    def frame_path_must_exist(cls, v: Path | None) -> Path | None:
        if v is not None and not v.exists():
            raise ValueError(f"Slate frame not found: {v}")
        return v


# ── Text elements ─────────────────────────────────────────────────────────────

class TextElementConfig(BaseModel):
    content: str
    enable: bool = True
    anchor: Literal[
        "top-left", "top-center", "top-right",
        "bottom-left", "bottom-center", "bottom-right",
        "center",
    ] = "bottom-left"
    offset: tuple[int, int] = (20, 20)
    font: Path | None = None
    font_size: float = 24.0
    color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    shadow: bool = False
    shadow_offset: tuple[int, int] = (2, 2)
    shadow_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.7)
    default: str = ""
    format: str | None = None

    @field_validator("font")
    @classmethod
    def font_must_exist(cls, v: Path | None) -> Path | None:
        if v is not None and not v.exists():
            raise ValueError(f"Font not found: {v}")
        return v


# ── Top-level config ──────────────────────────────────────────────────────────

class DailyConfig(BaseModel):
    ocio: OCIOConfig = OCIOConfig()
    output: OutputConfig = OutputConfig()
    cropmask: CropmaskConfig = CropmaskConfig()
    slate: SlateConfig = SlateConfig()
    text_enable: bool = True
    text_font: Path | None = None   # None = bundled Vera.ttf
    text_elements: list[TextElementConfig] = []

    # From yaml (optional explicit path to ffmpeg binary)
    ffmpeg: str | None = None

    # Populated at load time, not from yaml
    codecs: dict[str, CodecPreset] = {}
    cli_text: dict[str, str] = {}
    input_path: Path | None = None
    output_path_override: Path | None = None

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def codec_must_exist(self) -> "DailyConfig":
        if self.codecs and self.output.codec not in self.codecs:
            available = ", ".join(self.codecs)
            raise ValueError(
                f"Codec '{self.output.codec}' not in codecs.yaml. "
                f"Available: {available}"
            )
        return self


# ── OCIO path resolution ──────────────────────────────────────────────────────

def resolve_ocio_path(config: DailyConfig) -> Path:
    """Resolve OCIO config path: literal path → $OCIO env var → error."""
    raw = config.ocio.config
    if raw.startswith("$"):
        env_var = raw.lstrip("$")
        value = os.environ.get(env_var)
        if not value:
            raise RuntimeError(
                f"OCIO config set to '{raw}' but ${env_var} is not set. "
                "Use --ocio, set $OCIO, or update ocio.config in daily.yaml."
            )
        raw = value
    path = Path(raw)
    if not path.exists():
        raise RuntimeError(f"OCIO config not found: {path}")
    return path


def resolve_ffmpeg(config: DailyConfig) -> str:
    """Return absolute path to ffmpeg binary.

    Resolution order:
      --ffmpeg CLI flag / daily.yaml ffmpeg key  →  PATH search  →  error
    """
    candidate = config.ffmpeg
    if candidate:
        p = Path(candidate)
        if not p.exists():
            raise RuntimeError(f"ffmpeg binary not found at configured path: {p}")
        return str(p)

    found = shutil.which("ffmpeg")
    if found:
        return found

    raise RuntimeError(
        "ffmpeg not found. Either:\n"
        "  • Add ffmpeg to your PATH, or\n"
        "  • Set 'ffmpeg: /path/to/ffmpeg' in daily.yaml, or\n"
        "  • Pass --ffmpeg /path/to/ffmpeg on the command line."
    )


# ── YAML loaders ──────────────────────────────────────────────────────────────

def _bundled(name: str) -> Path:
    """Return path to a file bundled inside the daily package."""
    pkg = resources.files("daily")
    return Path(str(pkg / name))


def load_codecs(path: Path | None = None) -> dict[str, CodecPreset]:
    if path is None:
        path = _bundled("config/codecs.yaml")
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {key: CodecPreset(name=key, **val) for key, val in raw.items()}


def load_user_config(path: Path | None = None) -> dict[str, Any]:
    """Load daily.yaml, preferring cwd → bundled default."""
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cwd_cfg = Path("daily.yaml")
    if cwd_cfg.exists():
        return yaml.safe_load(cwd_cfg.read_text(encoding="utf-8")) or {}
    bundled = _bundled("config/daily.yaml")
    return yaml.safe_load(bundled.read_text(encoding="utf-8")) or {}


def load_text_elements(
    path: Path | None = None,
) -> tuple[Path | None, bool | None, list[Any]]:
    """Load text_elements.yaml, preferring explicit path → cwd → bundled default.

    Returns (font_path, enable, elements).
    - font_path: resolved Path to the font file, or None if not specified.
    - enable: bool from the yaml 'enable' key, or None if not specified.
    - elements: list of raw element dicts.

    A bare font filename (e.g. "Vera.ttf") is resolved against the bundled fonts dir.
    """
    if path is not None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        cwd_cfg = Path("text_elements.yaml")
        if cwd_cfg.exists():
            raw = yaml.safe_load(cwd_cfg.read_text(encoding="utf-8")) or {}
        else:
            bundled = _bundled("config/text_elements.yaml")
            raw = yaml.safe_load(bundled.read_text(encoding="utf-8")) or {}

    if isinstance(raw, list):
        return None, None, raw

    font_raw = raw.get("font")
    font: Path | None = None
    if font_raw:
        candidate = Path(font_raw)
        if not candidate.is_absolute() and str(candidate) == candidate.name:
            # Bare filename — resolve against bundled fonts dir
            candidate = Path(str(_bundled("fonts") / candidate.name))
        font = candidate

    enable: bool | None = raw.get("enable")

    return font, enable, raw.get("elements", [])


def _coerce(value: str) -> Any:
    """Best-effort type coercion for string values from CLI flags."""
    if value.lower() in ("true", "yes"):   return True
    if value.lower() in ("false", "no"):   return False
    if value.lower() in ("null", "none"):  return None
    try: return int(value)
    except ValueError: pass
    try: return float(value)
    except ValueError: pass
    return value


def _apply_dotpath(data: dict, key: str, value: Any) -> None:
    """Set a nested dict value via a dot-separated key path."""
    parts = key.split(".")
    d = data
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def build_config(
    user_yaml: dict[str, Any],
    codecs: dict[str, CodecPreset],
    text_elements: list[Any] | None = None,
    text_font: Path | None = None,
    text_enable: bool | None = None,
    *,
    cli_overrides: dict[str, Any] | None = None,
) -> DailyConfig:
    """Merge yaml + CLI overrides into a validated DailyConfig."""
    data = dict(user_yaml)
    if text_elements is not None:
        data["text_elements"] = text_elements
    if text_font is not None:
        data["text_font"] = str(text_font)
    if text_enable is not None:
        data["text_enable"] = text_enable
    overrides = cli_overrides or {}

    # Apply generic dot-path overrides from auto-generated CLI flags
    for dot_path, value in overrides.get("set_overrides", {}).items():
        _apply_dotpath(data, dot_path, value)

    # Shorthands that don't map directly to yaml keys
    if "codec" in overrides:
        _apply_dotpath(data, "output.codec", overrides["codec"])
    if "output_dir" in overrides:
        _apply_dotpath(data, "output.directory", str(overrides["output_dir"]))

    cfg = DailyConfig.model_validate(data)
    # Inject non-yaml fields
    object.__setattr__(cfg, "codecs", codecs)
    object.__setattr__(cfg, "cli_text", overrides.get("cli_text", {}))
    object.__setattr__(cfg, "input_path", overrides.get("input_path"))
    object.__setattr__(cfg, "output_path_override", overrides.get("output_path_override"))

    # Re-validate codec now that codecs dict is populated
    if codecs and cfg.output.codec not in codecs:
        available = ", ".join(codecs)
        raise ValueError(
            f"Codec '{cfg.output.codec}' not in codecs.yaml. Available: {available}"
        )
    return cfg
