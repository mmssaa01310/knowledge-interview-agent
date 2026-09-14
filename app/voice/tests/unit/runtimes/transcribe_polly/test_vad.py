from ai_interviewer_voice.runtimes.transcribe_polly.vad import PcmEnergyVad

def _pcm(sample: int, samples: int = 320) -> bytes:
    return b"".join(sample.to_bytes(2, "little", signed=True) for _ in range(samples))


def test_pcm_energy_vad_distinguishes_voice_and_silence() -> None:
    vad = PcmEnergyVad(sample_rate_hz=16000, rms_threshold=600)

    silence = vad.inspect(_pcm(0))
    voice = vad.inspect(_pcm(1200))

    assert silence.voiced is False
    assert silence.duration_ms == 20
    assert voice.voiced is True
    assert voice.rms == 1200
