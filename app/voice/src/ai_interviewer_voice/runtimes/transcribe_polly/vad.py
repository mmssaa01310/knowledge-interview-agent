from __future__ import annotations
import sys
from array import array
from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True)
class VadFrameResult:
    voiced: bool
    rms: int
    duration_ms: int


class PcmEnergyVad:
    """16-bit mono PCM用の小さなVAD境界。

    WebRTC側のechoCancellation/noiseSuppression後のPCMを対象にする。
    """

    def __init__(self, *, sample_rate_hz: int = 16000, rms_threshold: int = 600) -> None:
        self._sample_rate_hz = sample_rate_hz
        self._rms_threshold = rms_threshold

    def inspect(self, pcm: bytes) -> VadFrameResult:
        if not pcm or len(pcm) % 2:
            return VadFrameResult(voiced=False, rms=0, duration_ms=0)
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return VadFrameResult(voiced=False, rms=0, duration_ms=0)
        mean_square = sum(sample * sample for sample in samples) / len(samples)
        rms = int(sqrt(mean_square))
        duration_ms = round((len(samples) / self._sample_rate_hz) * 1000)
        return VadFrameResult(
            voiced=rms >= self._rms_threshold,
            rms=rms,
            duration_ms=duration_ms,
        )
