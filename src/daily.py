from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np

from .color import OCIOProcessor
from .config import DailyConfig, resolve_ffmpeg, resolve_ocio_path
from .encode import FFmpegEncoder
from .processor import FrameContext, FrameProcessor, FrameReadError, build_ops, get_source_resolution
from .sequence import SequenceInfo, discover_sequences
from .slate import SlateGenerator
from .text import TextContentResolver, TextRenderer
from .timecode import TimecodeHelper

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (completed_frames, total_frames)


def _black_frame(width: int, height: int) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.float32)


def _output_path(seq: SequenceInfo, config: DailyConfig) -> Path:
    if config.output_path_override is not None:
        override = config.output_path_override
        # If override looks like a directory (no video extension), treat as dir
        if override.suffix.lower() not in {".mov", ".mp4", ".mxf", ".mkv"}:
            return override / f"{seq.name}_{config.output.codec}.mov"
        return override
    return config.output.directory / f"{seq.name}_{config.output.codec}.mov"


def run(
    config: DailyConfig,
    progress_cb: ProgressCallback | None = None,
    verbose: bool = False,
) -> None:
    """Encode all sequences described by config.

    progress_cb receives (completed_frames, total_frames) after each frame.
    """
    if config.input_path is None:
        raise ValueError("config.input_path must be set before calling run()")

    ffmpeg_bin = resolve_ffmpeg(config)
    ocio_path = resolve_ocio_path(config)
    ocio = OCIOProcessor(config.ocio.transform, ocio_path)

    tc_helper = TimecodeHelper(config.output.framerate)
    resolver = TextContentResolver(tc_helper, config.cli_text)
    slate_gen = SlateGenerator() if config.slate.enable else None
    codec_preset = config.codecs[config.output.codec]
    trim_overscan = config.output.trim_overscan

    sequences = discover_sequences(config.input_path)
    log.info(f"Found {len(sequences)} sequence(s) at {config.input_path}")

    for seq in sequences:
        out = _output_path(seq, config)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Resolve output resolution: explicit config value or source EXR size
        if config.output.resolution is not None:
            width, height = config.output.resolution
        else:
            width, height = get_source_resolution(seq.frames[0].path)
            log.info(f"  resolution: {width}x{height} (from source)")

        renderer = TextRenderer((width, height), default_font=config.text_font)

        start_tc = tc_helper.tc_from_frame(seq.start)
        end_tc = tc_helper.tc_from_frame(seq.end)

        seq_ctx = FrameContext(
            frame_path=seq.frames[0].path,
            frame_number=seq.start,
            frame_index=0,
            seq_start=seq.start,
            seq_end=seq.end,
            sequence_name=seq.name,
            exr_metadata={},
            filename=seq.frames[0].path.name,
        )
        ops = build_ops(config, ocio, renderer, resolver, (width, height), seq_ctx=seq_ctx)
        processor = FrameProcessor(ops, trim_overscan=trim_overscan)
        log.info(
            f"  {seq.name}  frames {seq.start}-{seq.end} "
            f"({len(seq)} frames)  {start_tc} - {end_tc}"
        )
        log.info(f"  -> {out}")

        with FFmpegEncoder(
            codec_preset, out, width, height, config.output.framerate,
            ffmpeg_bin=ffmpeg_bin, start_timecode=start_tc, verbose=verbose,
        ) as enc:
            if slate_gen and config.slate.frame_path:
                slate_frame = slate_gen.generate(config.slate, (width, height))
                for _ in range(config.slate.duration_frames):
                    enc.write_frame(slate_frame)

            for frame in seq.frames:
                ctx = FrameContext(
                    frame_path=frame.path,
                    frame_number=frame.number,
                    frame_index=frame.index,
                    seq_start=seq.start,
                    seq_end=seq.end,
                    sequence_name=seq.name,
                    exr_metadata={},   # filled in by FrameProcessor.process()
                    filename=frame.path.name,
                )
                try:
                    buf = processor.process(frame.path, ctx)
                    enc.write_frame(buf)
                except FrameReadError as e:
                    log.warning(str(e))
                    enc.write_frame(_black_frame(width, height))

                if progress_cb:
                    progress_cb(frame.index + 1, len(seq))

        log.info(f"  done")
