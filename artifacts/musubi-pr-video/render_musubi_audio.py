#!/usr/bin/env python3
"""Trim the supplied MUSUBI soundtrack to fit and mux it into promo MP4s."""

from __future__ import annotations

import ctypes
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_TRACK = ROOT / "MUSUBI_modern_bgm.mp3"
TRACK = ROOT / "MUSUBI_PR_background_music.mp3"
DURATION = 30.0


def _id3_audio_offset(data: bytes) -> int:
    """Return the first audio byte after an optional ID3v2 tag."""
    if not data.startswith(b"ID3"):
        return 0
    if len(data) < 10 or any(byte & 0x80 for byte in data[6:10]):
        raise ValueError("The supplied MP3 has an invalid ID3v2 header.")
    tag_size = sum((data[index] & 0x7F) << (7 * (9 - index)) for index in range(6, 10))
    offset = 10 + tag_size
    if offset > len(data):
        raise ValueError("The supplied MP3 has a truncated ID3v2 tag.")
    return offset


def _mp3_frame_info(header: int) -> tuple[int, int, int] | None:
    """Return (frame length, sample rate, samples per frame) for Layer III."""
    if (header >> 21) & 0x7FF != 0x7FF:
        return None
    version = (header >> 19) & 0x3
    layer = (header >> 17) & 0x3
    bitrate_index = (header >> 12) & 0xF
    sample_index = (header >> 10) & 0x3
    padding = (header >> 9) & 0x1
    if version == 1 or layer != 1 or bitrate_index in (0, 15) or sample_index == 3:
        return None

    mpeg1 = version == 3
    bitrates = (
        (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
        if mpeg1
        else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
    )
    sample_rates = (44_100, 48_000, 32_000)
    sample_rate = sample_rates[sample_index]
    if version == 2:
        sample_rate //= 2
    elif version == 0:
        sample_rate //= 4

    bitrate = bitrates[bitrate_index] * 1000
    samples_per_frame = 1152 if mpeg1 else 576
    coefficient = 144 if mpeg1 else 72
    return coefficient * bitrate // sample_rate + padding, sample_rate, samples_per_frame


def _mp3_frames(data: bytes, audio_offset: int) -> tuple[list[tuple[int, int]], int, int]:
    """Collect contiguous MPEG Layer III frames and their timing parameters."""
    offset = audio_offset
    frames: list[tuple[int, int]] = []
    sample_rate = samples_per_frame = 0
    while offset + 4 <= len(data):
        info = _mp3_frame_info(int.from_bytes(data[offset : offset + 4], "big"))
        if info is None:
            break
        frame_length, frame_sample_rate, frame_samples = info
        if offset + frame_length > len(data):
            raise ValueError("The supplied MP3 contains a truncated audio frame.")
        if frames and (sample_rate != frame_sample_rate or samples_per_frame != frame_samples):
            raise ValueError("The supplied MP3 changes sample rate or MPEG version mid-track.")
        frames.append((offset, frame_length))
        sample_rate = frame_sample_rate
        samples_per_frame = frame_samples
        offset += frame_length
    if not frames:
        raise ValueError("No MPEG Layer III audio frames were found in the supplied MP3.")
    return frames, sample_rate, samples_per_frame


def _patch_xing_header(
    audio: bytearray,
    first_frame_offset: int,
    frame_lengths: list[int],
    frame_info: int,
) -> None:
    """Keep Xing/Info duration and seek data in sync with the trimmed frames."""
    header = frame_info
    version = (header >> 19) & 0x3
    mono = ((header >> 6) & 0x3) == 3
    crc_bytes = 0 if (header >> 16) & 1 else 2
    side_info_bytes = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    marker_offset = first_frame_offset + 4 + crc_bytes + side_info_bytes
    marker = bytes(audio[marker_offset : marker_offset + 4])
    if marker not in (b"Xing", b"Info"):
        return

    flags_offset = marker_offset + 4
    flags = int.from_bytes(audio[flags_offset : flags_offset + 4], "big")
    cursor = flags_offset + 4
    frame_count = len(frame_lengths)
    frame_bytes = sum(frame_lengths)
    if flags & 0x1:
        audio[cursor : cursor + 4] = max(0, frame_count - 1).to_bytes(4, "big")
        cursor += 4
    if flags & 0x2:
        audio[cursor : cursor + 4] = frame_bytes.to_bytes(4, "big")
        cursor += 4
    if flags & 0x4:
        offsets: list[int] = []
        total = 0
        for length in frame_lengths:
            offsets.append(total)
            total += length
        toc = bytearray(100)
        for percentage in range(100):
            frame_index = percentage * frame_count // 100
            toc[percentage] = min(255, offsets[frame_index] * 256 // frame_bytes)
        audio[cursor : cursor + 100] = toc


def _trim_mp3(source: Path, destination: Path, duration: float) -> None:
    """Copy a duration-matched MP3 prefix without re-encoding the user's audio."""
    data = source.read_bytes()
    audio_offset = _id3_audio_offset(data)
    frames, sample_rate, samples_per_frame = _mp3_frames(data, audio_offset)
    keep_count = min(len(frames), int(duration * sample_rate // samples_per_frame))
    if keep_count < 1:
        raise ValueError("The requested soundtrack duration is shorter than one MP3 frame.")

    first_offset = frames[0][0]
    final_offset, final_length = frames[keep_count - 1]
    frame_lengths = [length for _, length in frames[:keep_count]]
    trimmed = bytearray(data[: final_offset + final_length])
    _patch_xing_header(
        trimmed,
        first_offset,
        frame_lengths,
        int.from_bytes(data[first_offset : first_offset + 4], "big"),
    )
    destination.write_bytes(trimmed)
    actual_duration = keep_count * samples_per_frame / sample_rate
    print(f"Source music: {source} ({len(frames) * samples_per_frame / sample_rate:.2f}s)")
    print(f"Using first {keep_count} frames ({actual_duration:.2f}s) without re-encoding.")


class GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


class GStreamer:
    """Small ctypes bridge to the system GStreamer runtime and its MP4 plugins."""

    def __init__(self) -> None:
        try:
            self.gst = ctypes.CDLL("libgstreamer-1.0.so.0")
            self.glib = ctypes.CDLL("libglib-2.0.so.0")
        except OSError as exc:
            raise RuntimeError("GStreamer is required to encode and mux the promo soundtrack.") from exc

        self.gst.gst_init.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gst.gst_parse_launch.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(GError))]
        self.gst.gst_parse_launch.restype = ctypes.c_void_p
        self.gst.gst_element_set_state.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.gst.gst_element_set_state.restype = ctypes.c_int
        self.gst.gst_element_get_bus.argtypes = [ctypes.c_void_p]
        self.gst.gst_element_get_bus.restype = ctypes.c_void_p
        self.gst.gst_bus_timed_pop_filtered.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int]
        self.gst.gst_bus_timed_pop_filtered.restype = ctypes.c_void_p
        self.gst.gst_message_parse_error.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(GError)),
            ctypes.POINTER(ctypes.c_char_p),
        ]
        self.gst.gst_mini_object_unref.argtypes = [ctypes.c_void_p]
        self.gst.gst_object_unref.argtypes = [ctypes.c_void_p]
        self.glib.g_error_free.argtypes = [ctypes.POINTER(GError)]
        self.gst.gst_init(None, None)

    @staticmethod
    def _location(path: Path) -> str:
        return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _check_plugins(self, names: tuple[str, ...]) -> None:
        self.gst.gst_element_factory_find.argtypes = [ctypes.c_char_p]
        self.gst.gst_element_factory_find.restype = ctypes.c_void_p
        missing = []
        for name in names:
            factory = self.gst.gst_element_factory_find(name.encode())
            if not factory:
                missing.append(name)
            else:
                self.gst.gst_object_unref(factory)
        if missing:
            raise RuntimeError("Missing GStreamer plugins: " + ", ".join(missing))

    def run(self, description: str) -> None:
        error = ctypes.POINTER(GError)()
        pipeline = self.gst.gst_parse_launch(description.encode(), ctypes.byref(error))
        if error:
            message = error.contents.message.decode(errors="replace") if error.contents.message else "unknown error"
            self.glib.g_error_free(error)
            raise RuntimeError("GStreamer pipeline error: " + message)
        if not pipeline:
            raise RuntimeError("GStreamer did not create the requested pipeline.")

        bus = None
        try:
            if self.gst.gst_element_set_state(pipeline, 4) == 0:
                raise RuntimeError("GStreamer could not start the media pipeline.")
            bus = self.gst.gst_element_get_bus(pipeline)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                error_message = self.gst.gst_bus_timed_pop_filtered(bus, 0, 1 << 1)
                if error_message:
                    pipeline_error = ctypes.POINTER(GError)()
                    debug = ctypes.c_char_p()
                    self.gst.gst_message_parse_error(
                        error_message,
                        ctypes.byref(pipeline_error),
                        ctypes.byref(debug),
                    )
                    detail = pipeline_error.contents.message.decode(errors="replace") if pipeline_error else "unknown error"
                    if pipeline_error:
                        self.glib.g_error_free(pipeline_error)
                    self.gst.gst_mini_object_unref(error_message)
                    raise RuntimeError("GStreamer media pipeline error: " + detail)

                eos_message = self.gst.gst_bus_timed_pop_filtered(bus, 100_000_000, 1 << 0)
                if eos_message:
                    self.gst.gst_mini_object_unref(eos_message)
                    return
            raise TimeoutError("GStreamer media pipeline exceeded 180 seconds.")
        finally:
            self.gst.gst_element_set_state(pipeline, 1)
            if bus:
                self.gst.gst_object_unref(bus)
            self.gst.gst_object_unref(pipeline)


def _box_iter(data: bytes, start: int, end: int):
    offset = start
    while offset + 8 <= end:
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header_size = 16
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            return
        yield kind, offset + header_size, offset + size
        offset += size


def _track_handlers(path: Path) -> set[bytes]:
    data = path.read_bytes()
    for kind, payload, end in _box_iter(data, 0, len(data)):
        if kind != b"moov":
            continue
        handlers: set[bytes] = set()
        for child_kind, child_payload, child_end in _box_iter(data, payload, end):
            if child_kind != b"trak":
                continue
            for trak_kind, trak_payload, trak_end in _box_iter(data, child_payload, child_end):
                if trak_kind != b"mdia":
                    continue
                for mdia_kind, mdia_payload, mdia_end in _box_iter(data, trak_payload, trak_end):
                    if mdia_kind == b"hdlr" and mdia_payload + 12 <= mdia_end:
                        handlers.add(data[mdia_payload + 8 : mdia_payload + 12])
        return handlers
    return set()


def add_background_music(video_path: Path) -> None:
    """Add the supplied MUSUBI soundtrack to a rendered silent MP4."""
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    if not SOURCE_TRACK.is_file():
        raise FileNotFoundError(SOURCE_TRACK)
    gst = GStreamer()
    gst._check_plugins(("mpegaudioparse", "qtdemux", "mp4mux"))

    with tempfile.TemporaryDirectory(prefix="musubi-pr-audio-", dir=ROOT) as temp_dir:
        temp = Path(temp_dir)
        mp3_temp = temp / "background.mp3"
        muxed_path = temp / "with-audio.mp4"
        _trim_mp3(SOURCE_TRACK, mp3_temp, DURATION)
        if not mp3_temp.is_file() or mp3_temp.stat().st_size < 10_000:
            raise RuntimeError("The trimmed background music MP3 was not created correctly.")

        gst.run(
            f"filesrc location={gst._location(video_path)} ! qtdemux name=source "
            f"source.video_0 ! queue ! mux.video_0 "
            f"filesrc location={gst._location(mp3_temp)} ! mpegaudioparse ! queue ! mux.audio_0 "
            f"mp4mux name=mux faststart=true ! filesink location={gst._location(muxed_path)}"
        )
        handlers = _track_handlers(muxed_path)
        if not {b"vide", b"soun"}.issubset(handlers):
            raise RuntimeError("The rendered MP4 does not contain both video and audio tracks.")

        track_temp = TRACK.with_name("." + TRACK.name + ".tmp")
        track_temp.write_bytes(mp3_temp.read_bytes())
        os.replace(track_temp, TRACK)
        os.replace(muxed_path, video_path)
    print(f"Audio track: {TRACK}")
    print(f"Muxed video: {video_path}")
