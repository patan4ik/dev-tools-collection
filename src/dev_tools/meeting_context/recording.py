import queue
import shutil
import sys
import time
import wave
from argparse import Namespace
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .files import save_json, sha256_file


def audio_backend() -> Any:
    if sys.platform != "win32":
        raise RuntimeError("Recording requires Windows WASAPI. Transcription works independently.")
    import pyaudiowpatch as pa

    return pa


def devices(_: Namespace) -> None:
    pa = audio_backend()
    with pa.PyAudio() as p:
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["maxInputChannels"]:
                kind = "LOOPBACK" if d.get("isLoopbackDevice") else "INPUT"
                print(f"{i:3} {kind:8} {d['name']} ({int(d['defaultSampleRate'])} Hz)")


class ChunkWriter:
    """Bound WAV size; finalize each chunk for recovery after interruption."""

    def __init__(
        self,
        folder: Path,
        label: str,
        rate: int,
        channels: int,
        chunk_seconds: float,
        offset: float,
    ) -> None:
        self.folder, self.label = folder, label
        self.rate, self.channels = rate, channels
        self.limit = int(rate * chunk_seconds)
        self.offset = offset
        self.total = self.frames = 0
        self.wav: wave.Wave_write | None = None
        self.entries: list[dict[str, Any]] = []
        self.peak = 0

    def write(self, data: bytes) -> None:
        from array import array

        samples = array("h", data)
        if samples:
            self.peak = max(self.peak, max(abs(s) for s in samples))
        width = 2 * self.channels
        if len(data) % width:
            raise ValueError("Incomplete PCM frame received")
        while data:
            if self.wav is None:
                name = f"{self.label}-{len(self.entries) + 1:04}.wav"
                self.wav = wave.open(str(self.folder / name), "wb")
                self.wav.setparams((self.channels, 2, self.rate, 0, "NONE", "not compressed"))
                self.frames = 0
                self.entries.append(
                    {
                        "file": name,
                        "track": self.label,
                        "offset_seconds": self.offset + self.total / self.rate,
                    }
                )
            count = min(len(data) // width, self.limit - self.frames)
            self.wav.writeframes(data[: count * width])
            self.frames += count
            self.total += count
            self.entries[-1]["duration_seconds"] = self.frames / self.rate
            data = data[count * width :]
            if self.frames == self.limit:
                self.close()

    def close(self) -> None:
        if self.wav is not None:
            self.wav.close()
            self.wav = None


def record(args: Namespace) -> None:
    pa = audio_backend()
    args.out.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "version": 2,
        "started_utc": datetime.now(UTC).isoformat(),
        "status": "recording",
        "files": [],
        "devices": [],
        "warnings": [],
    }
    save_json(args.out / "recording.json", manifest)
    writers, streams = [], []
    packets: queue.Queue[tuple[str, float, bytes]] = queue.Queue(maxsize=512)
    problems: queue.SimpleQueue[str] = queue.SimpleQueue()
    epoch = time.monotonic()
    completed = False

    def callback_for(label: str) -> Callable[..., tuple[None, int]]:
        def callback(
            data: bytes, frame_count: int, time_info: Any, status: int
        ) -> tuple[None, int]:
            if status:
                problems.put(f"{label}: audio callback status {status}; audio may be missing")
            try:
                packets.put_nowait((label, time.monotonic(), data))
            except queue.Full:
                problems.put(f"{label}: disk writer queue full; recording stopped")
                return (None, pa.paAbort)
            return (None, pa.paContinue)

        return callback

    try:
        reserve = args.min_free_mb * 1024 * 1024
        if shutil.disk_usage(args.out).free < reserve:
            raise RuntimeError("Insufficient free disk space for the configured reserve")
        with pa.PyAudio() as p, ExitStack() as stack:
            output = (
                p.get_default_wasapi_loopback()
                if args.loopback is None
                else p.get_device_info_by_index(args.loopback)
            )
            if not output.get("isLoopbackDevice"):
                raise ValueError("--loopback must select a LOOPBACK device from 'devices'")
            selected = [("remote", output)]
            if args.mic is not None:
                mic = (
                    p.get_default_input_device_info()
                    if args.mic == "default"
                    else p.get_device_info_by_index(int(args.mic))
                )
                if mic.get("isLoopbackDevice") or not mic["maxInputChannels"]:
                    raise ValueError("--mic must select a microphone INPUT device")
                selected.append(("microphone", mic))
            by_label = {}
            for label, d in selected:
                rate, channels = int(d["defaultSampleRate"]), int(d["maxInputChannels"])
                if label == "microphone":
                    channels = 1
                writer = ChunkWriter(args.out, label, rate, channels, args.chunk_seconds, 0)
                writers.append(writer)
                by_label[label] = writer
                manifest["devices"].append(
                    {
                        "track": label,
                        "name": d["name"],
                        "index": d["index"],
                        "rate": rate,
                        "channels": channels,
                    }
                )
                stream = p.open(
                    format=pa.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=int(d["index"]),
                    frames_per_buffer=1024,
                    stream_callback=callback_for(label),
                    start=False,
                )
                stack.callback(stream.close)
                streams.append(stream)
                print(f"{label}: {d['name']}")
            epoch = time.monotonic()
            for stream in streams:
                stream.start_stream()
            print(f"Recording to {args.out}. Press Ctrl+C to stop.", flush=True)
            last_save = epoch
            last_disk_check = epoch

            def consume(packet: tuple[str, float, bytes]) -> None:
                label, received, data = packet
                writer = by_label[label]
                if writer.total == 0:
                    writer.offset = max(
                        0,
                        received - epoch - len(data) / (2 * writer.channels * writer.rate),
                    )
                writer.write(data)

            try:
                while args.seconds is None or time.monotonic() - epoch < args.seconds:
                    if time.monotonic() - last_disk_check > 1:
                        if shutil.disk_usage(args.out).free < reserve:
                            raise RuntimeError(
                                "Free disk space fell below reserve; recording stopped"
                            )
                        last_disk_check = time.monotonic()
                    if not problems.empty():
                        raise RuntimeError(problems.get())
                    if any(not stream.is_active() for stream in streams):
                        raise RuntimeError("Audio stream stopped unexpectedly")
                    try:
                        consume(packets.get(timeout=0.1))
                    except queue.Empty:
                        pass
                    if time.monotonic() - last_save > 5:
                        manifest["files"] = [e for w in writers for e in w.entries]
                        save_json(args.out / "recording.json", manifest)
                        last_save = time.monotonic()
                completed = True
            except KeyboardInterrupt:
                completed = True
            finally:
                for stream in streams:
                    stream.stop_stream()
                while not packets.empty():
                    consume(packets.get_nowait())
    except Exception as exc:
        manifest["warnings"].append(str(exc))
        raise
    finally:
        for writer in writers:
            writer.close()
            if not writer.total or writer.peak == 0:
                manifest["warnings"].append(
                    f"{writer.label}: no nonzero audio captured; check device"
                )
        while not problems.empty():
            manifest["warnings"].append(problems.get())
        manifest["status"] = (
            "complete" if completed and not manifest["warnings"] else "needs_review"
        )
        manifest["elapsed_seconds"] = time.monotonic() - epoch
        manifest["files"] = [e for w in writers for e in w.entries]
        for entry in manifest["files"]:
            entry["sha256"] = sha256_file(args.out / entry["file"])
        save_json(args.out / "recording.json", manifest)
        print(f"Recording status: {manifest['status']}; see recording.json")
