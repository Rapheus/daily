from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyseq


@dataclass
class FrameInfo:
    path: Path
    index: int    # 0-based position within the sequence
    number: int   # actual frame number (e.g. 1001)


@dataclass
class SequenceInfo:
    name: str
    frames: list[FrameInfo]
    start: int
    end: int

    def __len__(self) -> int:
        return len(self.frames)


_EXR_EXTS = {".exr"}


def _seq_name(seq: pyseq.Sequence) -> str:
    head: str = seq.head() or ""
    return head.rstrip("._- ") or Path(seq[0].name).stem


def _is_exr_seq(seq: pyseq.Sequence) -> bool:
    return (seq.tail() or "").lower() in _EXR_EXTS


def _build_sequence_info(seq: pyseq.Sequence) -> SequenceInfo:
    frames = [
        FrameInfo(path=Path(item.path), index=i, number=item.frame)
        for i, item in enumerate(seq)
    ]
    return SequenceInfo(
        name=_seq_name(seq),
        frames=frames,
        start=seq.start(),
        end=seq.end(),
    )


def discover_sequences(input_path: Path) -> list[SequenceInfo]:
    """Return all EXR sequences found at *input_path*.

    - Directory  → scan for all EXR sequences inside it.
    - File       → find the sequence that file belongs to.
    - Non-existent path → treat as a pyseq pattern.
    """
    if input_path.is_dir():
        raw = pyseq.get_sequences(str(input_path))
        seqs = [s for s in raw if _is_exr_seq(s) and len(s) > 0]
        if not seqs:
            raise FileNotFoundError(f"No EXR sequences found in {input_path}")
        return [_build_sequence_info(s) for s in seqs]

    if input_path.is_file():
        raw = pyseq.get_sequences(str(input_path.parent))
        target = input_path.name
        for seq in raw:
            if not _is_exr_seq(seq):
                continue
            if any(item.name == target for item in seq):
                return [_build_sequence_info(seq)]
        raise FileNotFoundError(
            f"Could not find an EXR sequence containing {input_path}"
        )

    # Treat as a pyseq glob / pattern
    raw = pyseq.get_sequences(str(input_path.parent))
    seqs = [s for s in raw if _is_exr_seq(s) and len(s) > 0]
    if not seqs:
        raise FileNotFoundError(f"No EXR sequences matched: {input_path}")
    return [_build_sequence_info(s) for s in seqs]
