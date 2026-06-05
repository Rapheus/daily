from __future__ import annotations

from timecode import Timecode


class TimecodeHelper:
    """Frame number ↔ SMPTE timecode string conversion.

    Frame numbers are treated as absolute (e.g. 1001 → "00:00:41:16" at 24fps),
    meaning frame N is the Nth frame from the clock origin 00:00:00:00.
    """

    def __init__(self, framerate: str):
        self.framerate = framerate

    def tc_from_frame(self, frame: int) -> str:
        """Absolute frame number → "HH:MM:SS:FF" string."""
        tc = Timecode(self.framerate, frames=frame)
        return str(tc)

    def frame_from_tc(self, timecode_str: str) -> int:
        """Timecode string → absolute frame number (1-indexed)."""
        tc = Timecode(self.framerate, timecode_str)
        return tc.frames

    def range_string(self, start: int, end: int) -> str:
        """Human-readable range for CLI output, e.g. '00:00:41:16 – 00:00:51:16'."""
        return f"{self.tc_from_frame(start)} - {self.tc_from_frame(end)}"
