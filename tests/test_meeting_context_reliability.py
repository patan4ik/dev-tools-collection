"""Failure-oriented checks. No real recording, model download, or cloud calls."""

import argparse
import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.meeting_context.cli import parser
from dev_tools.meeting_context.files import (
    atomic_text,
    child_file,
    job_lock,
    local_path,
    read_json,
    save_json,
    sha256_file,
)
from dev_tools.meeting_context.models import describe_model, download_model
from dev_tools.meeting_context.packaging import bundle, init_context, verify_bundle
from dev_tools.meeting_context.recording import record
from dev_tools.meeting_context.review import review
from dev_tools.meeting_context.transcription import collect_sources, transcribe


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "model"
        self.model.mkdir()
        for name in ("config.json", "tokenizer.json", "model.bin"):
            (self.model / name).write_bytes(b"mock model")
        self.audio = self.root / "audio"
        self.audio.mkdir()
        for name in ("first.wav", "second.wav"):
            (self.audio / name).write_bytes(b"mock audio; not used by a decoder")
        self.entries = [
            {"file": "first.wav", "track": "remote", "offset_seconds": 10},
            {"file": "second.wav", "track": "microphone", "offset_seconds": 0},
        ]
        save_json(
            self.audio / "recording.json",
            {"version": 2, "status": "complete", "files": self.entries},
        )
        self.args = argparse.Namespace(
            input=self.audio,
            model=self.model,
            out=self.root / "text",
            language="en",
            task="transcribe",
            hotwords=None,
            initial_prompt=None,
            no_vad=False,
            resume=False,
        )

    def engine(self, failure=None, text="Do not deploy yet."):
        calls = []

        def run(path, **kwargs):
            name = Path(path).name
            calls.append((name, kwargs))
            if name == failure:
                raise RuntimeError("simulated ASR interruption")
            segments = iter([SimpleNamespace(start=1, end=2, text=text)])
            return segments, SimpleNamespace(language="en")

        return (
            SimpleNamespace(model=SimpleNamespace(is_multilingual=False), transcribe=run),
            calls,
        )

    def run_success(self, text="Do not deploy yet."):
        engine, calls = self.engine(text=text)
        with (
            patch("dev_tools.meeting_context.transcription.load_engine", return_value=engine),
            patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")),
        ):
            transcribe(self.args)
        return calls

    def test_resume_only_incomplete_source(self):
        engine, calls = self.engine(failure="second.wav")
        with patch("dev_tools.meeting_context.transcription.load_engine", return_value=engine):
            with self.assertRaises(RuntimeError):
                transcribe(self.args)
        job = read_json(self.args.out / "transcription-job.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(list(job["completed"]), ["first.wav"])
        self.assertFalse((self.args.out / "transcript.txt").exists())
        self.args.resume = True
        engine, calls = self.engine()
        with patch("dev_tools.meeting_context.transcription.load_engine", return_value=engine):
            transcribe(self.args)
        self.assertEqual([call[0] for call in calls], ["second.wav"])
        result = read_json(self.args.out / "transcript.json")
        self.assertEqual([row["start"] for row in result["segments"]], [1, 11])

    def test_completed_resume_is_idempotent_without_model_loading(self):
        self.run_success()
        self.args.resume = True
        before = (self.args.out / "transcript.txt").read_bytes()
        with patch(
            "dev_tools.meeting_context.transcription.load_engine",
            side_effect=AssertionError("Must not load model"),
        ):
            transcribe(self.args)
        self.assertEqual((self.args.out / "transcript.txt").read_bytes(), before)

    def test_changed_audio_invalidates_resume(self):
        self.run_success()
        self.args.resume = True
        (self.audio / "first.wav").write_bytes(b"changed audio")
        with self.assertRaisesRegex(ValueError, "Cannot resume"):
            transcribe(self.args)

    def test_changed_settings_invalidates_resume(self):
        self.run_success()
        self.args.resume = True
        self.args.no_vad = True
        with self.assertRaisesRegex(ValueError, "Cannot resume"):
            transcribe(self.args)

    def test_model_hash_change_rejected(self):
        descriptor = describe_model(self.model)
        save_json(self.model / "model-manifest.json", descriptor)
        (self.model / "model.bin").write_bytes(b"different model")
        with self.assertRaisesRegex(ValueError, "Model files differ"):
            describe_model(self.model)

    def test_missing_tokenizer_rejected_before_engine(self):
        (self.model / "tokenizer.json").unlink()
        with self.assertRaises(ValueError):
            transcribe(self.args)

    def test_model_download_requires_immutable_revision(self):
        args = argparse.Namespace(
            repo="Systran/faster-whisper-small.en",
            revision="main",
            out=self.root / "download",
        )
        with self.assertRaisesRegex(ValueError, "40-character"):
            download_model(args)
        self.assertFalse(args.out.exists())

    def test_cli_offline_defaults(self):
        args = parser().parse_args(["transcribe", "audio", "--out", "text"])
        self.assertEqual(args.model, Path("models/small.en"))
        self.assertEqual(args.language, "en")
        self.assertFalse(hasattr(args, "allow_download"))

    def test_hints_passed_without_changing_recognized_text(self):
        hints = self.root / "terms.txt"
        hints.write_text("Kubernetes, PostgreSQL", encoding="utf-8")
        self.args.hotwords = hints
        calls = self.run_success()
        self.assertEqual(calls[0][1]["hotwords"], "Kubernetes, PostgreSQL")
        self.assertIn(
            "Do not deploy yet.",
            (self.args.out / "transcript.txt").read_text(encoding="utf-8"),
        )

    def test_english_model_rejects_translation(self):
        self.args.task = "translate"
        engine, _ = self.engine()
        with patch("dev_tools.meeting_context.transcription.load_engine", return_value=engine):
            with self.assertRaisesRegex(ValueError, "English-only"):
                transcribe(self.args)

    def test_invalid_offset_and_duplicate_sources(self):
        for invalid in (-1, float("nan"), "1", True):
            entries = [dict(self.entries[0], offset_seconds=invalid)]
            # Deliberately write nonstandard NaN JSON to test rejection at the boundary.
            (self.audio / "recording.json").write_text(json.dumps({"version": 2, "files": entries}))
            with self.assertRaises(ValueError):
                collect_sources(self.audio)
        save_json(
            self.audio / "recording.json",
            {"version": 2, "files": [self.entries[0]] * 2},
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            collect_sources(self.audio)

    def test_unsafe_paths_and_hash_mismatch_rejected(self):
        for value in (
            "https://example.com/audio.wav",
            "//server/share",
            "\\\\server\\share",
        ):
            with self.assertRaises(ValueError):
                local_path(value)
        for value in ("../first.wav", "first.wav:secret", "sub/first.wav"):
            with self.assertRaises(ValueError):
                child_file(self.audio, value)
        self.entries[0]["sha256"] = "0" * 64
        save_json(self.audio / "recording.json", {"version": 2, "files": self.entries})
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            collect_sources(self.audio)

    def test_manifest_must_be_an_object(self):
        (self.audio / "recording.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            collect_sources(self.audio)

    def test_review_rejects_non_audio_links(self):
        self.run_success()
        payload = self.audio / "payload.html"
        payload.write_text("<script>test</script>")
        data = read_json(self.args.out / "transcript.json")
        data["sources"] = [{"file": payload.name, "sha256": sha256_file(payload)}]
        save_json(self.args.out / "transcript.json", data)
        args = argparse.Namespace(
            transcript=self.args.out / "transcript.json",
            audio_dir=self.audio,
            out=self.root / "review",
        )
        with self.assertRaisesRegex(ValueError, "supported audio"):
            review(args)

    def test_output_lock_prevents_concurrent_job(self):
        with job_lock(self.root), self.assertRaises(RuntimeError), job_lock(self.root):
            self.fail("Second process must not acquire the lock")
        with job_lock(self.root):
            pass

    def test_atomic_replace_failure_preserves_original(self):
        path = self.root / "file.txt"
        path.write_text("original")
        with patch("dev_tools.meeting_context.files.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                atomic_text(path, "replacement")
        self.assertEqual(path.read_text(), "original")
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_review_escapes_html_and_uses_chunk_relative_time(self):
        self.run_success(text='<script>alert("test")</script> & do not deploy')
        args = argparse.Namespace(
            transcript=self.args.out / "transcript.json",
            audio_dir=self.audio,
            out=self.root / "review",
        )
        review(args)
        html = (args.out / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("first.wav#t=1.000,2.000", html)
        self.assertNotIn("first.wav#t=11.000", html)
        self.assertEqual(
            sha256_file(args.out / "audio" / "first.wav"),
            sha256_file(self.audio / "first.wav"),
        )

    def test_bundle_checks_integrity_and_incomplete_jobs(self):
        self.run_success()
        context = self.root / "context.txt"
        init_context(argparse.Namespace(out=context))
        args = argparse.Namespace(
            transcript=self.args.out / "transcript.txt",
            context=context,
            out=self.root / "bundle",
        )
        bundle(args)
        verify_bundle(argparse.Namespace(input=args.out))
        (args.out / "transcript.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_bundle(argparse.Namespace(input=args.out))
        job = read_json(self.args.out / "transcription-job.json")
        job["status"] = "failed"
        save_json(self.args.out / "transcription-job.json", job)
        args.out = self.root / "other-bundle"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            bundle(args)

    def test_low_disk_recording_fails_before_opening_device(self):
        args = argparse.Namespace(out=self.root / "capture", min_free_mb=512)
        with (
            patch("dev_tools.meeting_context.recording.audio_backend", return_value=None),
            patch(
                "dev_tools.meeting_context.recording.shutil.disk_usage",
                return_value=SimpleNamespace(free=1),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Insufficient"):
                record(args)
        self.assertEqual(read_json(args.out / "recording.json")["status"], "needs_review")
