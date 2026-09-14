import argparse
import json
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dev_tools.meeting_context.cli import chunk_duration
from dev_tools.meeting_context.files import timestamp
from dev_tools.meeting_context.packaging import bundle, init_context
from dev_tools.meeting_context.recording import ChunkWriter, record
from dev_tools.meeting_context.transcription import collect_sources, transcribe


class WorkflowTests(unittest.TestCase):
    def test_record_finalizes_both_tracks_and_reports_capture_failure(self):
        for status in (0, 2):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as directory,
            ):
                streams: list[Any] = []

                class FakeStream:
                    def __init__(self, options, callback_status=status):
                        self.options = options
                        self.callback_status = callback_status
                        self.active = False
                        self.closed = False

                    def start_stream(self):
                        self.active = True
                        self.options["stream_callback"](
                            array("h", [100] * 128).tobytes(),
                            128,
                            {},
                            self.callback_status,
                        )

                    def is_active(self):
                        return self.active

                    def stop_stream(self):
                        self.active = False

                    def close(self):
                        self.closed = True

                class FakeAudio:
                    def __init__(self, stream_list=streams):
                        self.stream_list = stream_list

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        pass

                    def get_default_wasapi_loopback(self):
                        return {
                            "index": 1,
                            "name": "Output",
                            "maxInputChannels": 2,
                            "defaultSampleRate": 8000,
                            "isLoopbackDevice": True,
                        }

                    def get_default_input_device_info(self):
                        return {
                            "index": 2,
                            "name": "Mic",
                            "maxInputChannels": 1,
                            "defaultSampleRate": 8000,
                        }

                    def open(self, **options):
                        stream = FakeStream(options)
                        self.stream_list.append(stream)
                        return stream

                backend = SimpleNamespace(PyAudio=FakeAudio, paInt16=8, paContinue=0, paAbort=2)
                args = argparse.Namespace(
                    out=Path(directory) / "capture",
                    loopback=None,
                    mic="default",
                    chunk_seconds=1,
                    seconds=0.01,
                    min_free_mb=1,
                )
                with patch(
                    "dev_tools.meeting_context.recording.audio_backend", return_value=backend
                ):
                    if status:
                        with self.assertRaises(RuntimeError):
                            record(args)
                    else:
                        record(args)
                data = json.loads((args.out / "recording.json").read_text())
                self.assertEqual(data["status"], "needs_review" if status else "complete")
                self.assertEqual({e["track"] for e in data["files"]}, {"remote", "microphone"})
                self.assertTrue(all(s.closed for s in streams))
                for entry in data["files"]:
                    with wave.open(str(args.out / entry["file"]), "rb") as wav:
                        self.assertGreater(wav.getnframes(), 0)

    def test_pcm_chunks_preserve_samples_and_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = ChunkWriter(root, "remote", 8000, 2, 1, 0.125)
            original = array("h", [100, -200] * 19000).tobytes()
            for start in range(0, len(original), 4096):
                writer.write(original[start : start + 4096])
            writer.close()
            recovered = b""
            for entry in writer.entries:
                with wave.open(str(root / entry["file"]), "rb") as wav:
                    self.assertEqual(
                        (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()),
                        (2, 2, 8000),
                    )
                    self.assertLessEqual(wav.getnframes(), 8000)
                    recovered += wav.readframes(wav.getnframes())
            self.assertEqual(recovered, original)
            self.assertEqual([e["offset_seconds"] for e in writer.entries], [0.125, 1.125, 2.125])
            self.assertEqual(writer.peak, 200)

    def test_bundle_preserves_full_unicode_input_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root / "context.txt"
            init_context(argparse.Namespace(out=context))
            with self.assertRaises(FileExistsError):
                init_context(argparse.Namespace(out=context))
            source = root / "source.txt"
            text = "[S000001] [00:00:01.000] Name: naïve — not approved.\n" * 500
            source.write_text(text, encoding="utf-8")
            args = argparse.Namespace(transcript=source, context=context, out=root / "bundle")
            bundle(args)
            self.assertEqual((args.out / "transcript.txt").read_text(encoding="utf-8"), text)
            self.assertEqual(len(json.loads((args.out / "bundle.json").read_text())["sha256"]), 5)
            with self.assertRaises(FileExistsError):
                bundle(args)

    def test_transcription_exhausts_generator_orders_tracks_and_preserves_negation(
        self,
    ):
        calls = []

        class FakeModel:
            def __init__(self, *args, **kwargs):
                calls.append(kwargs)
                self.model = SimpleNamespace(is_multilingual=False)

            def transcribe(self, path, **kwargs):
                calls.append(kwargs)
                text = (
                    "We did not approve the release." if "remote" in path else "Confirm tomorrow."
                )

                def segments():
                    yield SimpleNamespace(start=1, end=2, text=text)

                return segments(), SimpleNamespace(language="en")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("remote.wav", "mic.wav"):
                (root / name).write_bytes(b"fake audio for mocked ASR")
            (root / "recording.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "status": "needs_review",
                        "files": [
                            {
                                "file": "remote.wav",
                                "track": "remote",
                                "offset_seconds": 5,
                            },
                            {
                                "file": "mic.wav",
                                "track": "microphone",
                                "offset_seconds": 0,
                            },
                        ],
                    }
                )
            )
            model_dir = root / "model"
            model_dir.mkdir()
            for name in ("model.bin", "tokenizer.json", "config.json"):
                (model_dir / name).write_bytes(b"mock model")
            args = argparse.Namespace(
                input=root,
                out=root / "text",
                model=model_dir,
                language="en",
                task="transcribe",
                hotwords=None,
                initial_prompt=None,
                no_vad=False,
                resume=False,
            )
            with patch(
                "dev_tools.meeting_context.transcription.load_engine", return_value=FakeModel()
            ):
                transcribe(args)
            result = json.loads((args.out / "transcript.json").read_text())
            self.assertEqual([s["start"] for s in result["segments"]], [1, 6])
            self.assertEqual(result["segments"][1]["id"], "S000002")
            self.assertIn("not approve", (args.out / "transcript.txt").read_text(encoding="utf-8"))
            self.assertTrue(result["warnings"])
            self.assertEqual(calls[1]["language"], "en")
            self.assertFalse((args.out / "transcript.partial.json").exists())

    def test_reject_manifest_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "recording.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "status": "complete",
                        "files": [
                            {
                                "file": "../outside.wav",
                                "track": "remote",
                                "offset_seconds": 0,
                            }
                        ],
                    }
                )
            )
            with self.assertRaises(ValueError):
                collect_sources(root)

    def test_time_and_invalid_durations(self):
        self.assertEqual(timestamp(3599.9996), "01:00:00.000")
        for value in ("0", "-1", "nan", "inf", "601", "0.1"):
            with self.assertRaises(argparse.ArgumentTypeError):
                chunk_duration(value)


if __name__ == "__main__":
    unittest.main()
