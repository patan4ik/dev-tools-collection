import csv
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen.id3 import APIC, ID3, TIT2, TPE1

from dev_tools.mp3_manager import cli, dedup, names
from dev_tools.mp3_manager.cache import AnalysisCache, file_stamp


@pytest.fixture(autouse=True)
def available_ffmpeg(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda executable: sys.executable)


def test_bucket_for_bitrate_uses_expected_thresholds():
    assert cli.bucket_for_bitrate(400) == 360
    assert cli.bucket_for_bitrate(320) == 256
    assert cli.bucket_for_bitrate(128) == 128
    assert cli.bucket_for_bitrate(96) == 64


@pytest.mark.parametrize(
    "bitrate,bucket",
    [
        (127, 64),
        (128, 128),
        (159, 128),
        (160, 160),
        (191, 160),
        (192, 192),
        (250, 192),
        (255, 192),
        (256, 256),
        (359, 256),
        (360, 360),
    ],
)
def test_expanded_bitrate_boundaries(bitrate, bucket):
    assert cli.bucket_for_bitrate(bitrate) == bucket


def test_reprocess_old_buckets_preserves_files_and_existing_destinations(tmp_path, monkeypatch):
    files = {
        "128/a.mp3": b"160",
        "128/b.mp3": b"192",
        "128/c.mp3": b"250",
        "192/b.mp3": b"192",
        "160/stay.mp3": b"160",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(cli, "read_mp3_metadata", lambda path: (int(path.read_bytes()), 10.0))
    assert cli.find_mp3_files(tmp_path) == []
    assert len(cli.find_mp3_files(tmp_path, include_sorted=True)) == 5
    report = tmp_path / "report.csv"
    assert cli.run(tmp_path, report, "unused", True, include_sorted=True, skip_loudness=True) == 0
    assert all((tmp_path / relative).read_bytes() == content for relative, content in files.items())
    assert cli.run(tmp_path, report, "unused", False, include_sorted=True, skip_loudness=True) == 0
    expected = {
        "160/a.mp3": b"160",
        "192/b (1).mp3": b"192",
        "192/c.mp3": b"250",
        "192/b.mp3": b"192",
        "160/stay.mp3": b"160",
    }
    assert {
        p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*.mp3")
    } == expected
    # Repeating the operation must not add suffixes to correctly sorted files.
    assert cli.run(tmp_path, report, "unused", False, include_sorted=True, skip_loudness=True) == 0
    assert {
        p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*.mp3")
    } == expected


def test_cached_metadata_uses_new_bucket_without_reanalysis(tmp_path, monkeypatch):
    path = tmp_path / "128" / "song.mp3"
    path.parent.mkdir()
    path.write_bytes(b"source")
    cache = AnalysisCache(tmp_path / ".mp3-manager-cache.sqlite3", sys.executable)
    cache.put(path, file_stamp(path), 250, 10.0, -15.0)
    cache.close()

    def unexpected(*args, **kwargs):
        pytest.fail("valid cached measurements must not be reanalyzed for a bucket change")

    monkeypatch.setattr(cli, "analyze_file", unexpected)
    report = tmp_path / "report.csv"
    assert cli.run(tmp_path, report, "ffmpeg", True, include_sorted=True) == 0
    with report.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["bitrate_kbps"] == "250"
    assert row["bitrate_bucket_kbps"] == "192"
    assert row["integrated_loudness_lufs"] == "-15.00"


def test_calculate_volume_percent_normalizes_measured_records(tmp_path):
    quiet = cli.Mp3Record(tmp_path / "quiet.mp3", Path("quiet.mp3"), 128, 1.0, -30.0, None, 128)
    loud = cli.Mp3Record(tmp_path / "loud.mp3", Path("loud.mp3"), 320, 1.0, -10.0, None, 256)
    unknown = cli.Mp3Record(
        tmp_path / "unknown.mp3", Path("unknown.mp3"), 64, 1.0, None, "silence", 64
    )

    records = cli.calculate_volume_percent([quiet, loud, unknown])

    assert records[0].volume_percent == 0.0
    assert records[1].volume_percent == 100.0
    assert records[2].volume_percent is None


def test_run_moves_files_and_writes_bitrate_sorted_report(tmp_path, monkeypatch):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"not-a-real-mp3")
    second.write_bytes(b"not-a-real-mp3")
    metadata = {first: (128, 10.0), second: (320, 20.0)}

    monkeypatch.setattr(cli, "read_mp3_metadata", lambda path: metadata[path])
    monkeypatch.setattr(
        cli,
        "measure_loudness",
        lambda path, ffmpeg_bin, timeout=300: (-20.0 if path == first else -10.0, None),
    )

    report_path = tmp_path / "report.csv"
    assert cli.run(tmp_path, report_path, "ffmpeg", dry_run=False) == 0
    assert (tmp_path / "128" / "first.mp3").exists()
    assert (tmp_path / "256" / "second.mp3").exists()

    with report_path.open(newline="", encoding="utf-8-sig") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert [row["file_name"] for row in rows] == ["second.mp3", "first.mp3"]
    assert rows[0]["volume_percent"] == "100.00"


def test_scan_only_excludes_root_bucket_directories(tmp_path):
    root = tmp_path / "128" / "music"
    for name in ("track.MP3", "album/128/keep.mp3", "128/skip.mp3", "256/skip.mp3"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert {p.relative_to(root).as_posix() for p in cli.find_mp3_files(root)} == {
        "track.MP3",
        "album/128/keep.mp3",
    }


@pytest.mark.parametrize("info", [None, object()])
def test_missing_stream_info_is_reported(tmp_path, monkeypatch, info):
    monkeypatch.setattr(cli, "MP3", lambda path: SimpleNamespace(info=info))
    with pytest.raises(ValueError, match="stream information"):
        cli.read_mp3_metadata(tmp_path / "bad.mp3")


def test_valid_metadata_and_invalid_bitrate(tmp_path, monkeypatch):
    info = cli.MPEGInfo.__new__(cli.MPEGInfo)
    info.bitrate = 128000
    info.length = 12.5
    monkeypatch.setattr(cli, "MP3", lambda path: SimpleNamespace(info=info))
    assert cli.read_mp3_metadata(tmp_path / "valid.mp3") == (128, 12.5)
    info.bitrate = 0
    with pytest.raises(ValueError, match="positive bitrate"):
        cli.read_mp3_metadata(tmp_path / "bad.mp3")


@pytest.mark.parametrize(
    "output,code,value,error",
    [
        ("I: -30.0 LUFS\nI: -12.3 LUFS", 0, -12.3, None),
        ("I: -inf LUFS", 0, None, "silence"),
        ("I: inf LUFS", 0, None, "non-finite"),
        ("", 0, None, "did not return"),
        ("I: -12.3 LUFS", 1, None, "exit code 1"),
    ],
)
def test_ffmpeg_results_and_safe_arguments(tmp_path, monkeypatch, output, code, value, error):
    path = tmp_path / "track & name.mp3"

    def fake_run(command, **kwargs):
        assert command[command.index("-i") + 1] == str(path.resolve())
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 300
        assert "0:a:0" in command
        assert "ebur128=peak=none:framelog=verbose" in command
        assert command[command.index("-filter_threads") + 1] == "1"
        return subprocess.CompletedProcess(command, code, "", output)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    measured, failure = cli.measure_loudness(path, sys.executable)
    assert measured == value
    assert failure is None if error is None else error in failure


@pytest.mark.parametrize(
    "exc", [FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("ffmpeg", 300)]
)
def test_ffmpeg_execution_errors(tmp_path, monkeypatch, exc):
    def fake_run(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    value, error = cli.measure_loudness(tmp_path / "track.mp3", "ffmpeg")
    assert value is None
    assert error


def mock_analysis(monkeypatch):
    monkeypatch.setattr(cli, "read_mp3_metadata", lambda path: (128, 10.0))
    monkeypatch.setattr(cli, "measure_loudness", lambda path, exe, timeout=300: (-20.0, None))


def test_dry_run_writes_report_without_moving(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    source = tmp_path / "song.mp3"
    source.write_bytes(b"source")
    report = tmp_path / "report.csv"
    assert cli.run(tmp_path, report, "ffmpeg", True) == 0
    assert source.read_bytes() == b"source"
    assert not (tmp_path / "128").exists()
    with report.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["destination_path"] == ""
    assert row["volume_percent"] == "100.00"


def test_missing_ffmpeg_stops_before_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda executable: None)
    source = tmp_path / "song.mp3"
    source.write_bytes(b"source")
    report = tmp_path / "report.csv"
    assert cli.run(tmp_path, report, "ffmpeg", False) == 2
    assert source.read_bytes() == b"source"
    assert not report.exists()


def test_report_cannot_overwrite_audio(tmp_path):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"source")
    assert cli.run(tmp_path, source, "ffmpeg", True) == 2
    assert source.read_bytes() == b"source"


def test_duplicate_names_preserve_both_files(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    for directory, content in (("album", b"new"), ("128", b"existing")):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "song.mp3").write_bytes(content)
    assert cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", False) == 0
    assert (tmp_path / "128/song.mp3").read_bytes() == b"existing"
    assert (tmp_path / "128/song (1).mp3").read_bytes() == b"new"


def test_move_error_recorded_and_remaining_files_processed(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    (tmp_path / "a.mp3").touch()
    (tmp_path / "b.mp3").touch()
    original_move = cli.move_to_bucket

    def fail_one(record, root):
        if record.source_path.name == "a.mp3":
            raise PermissionError("locked")
        return original_move(record, root)

    monkeypatch.setattr(cli, "move_to_bucket", fail_one)
    report = tmp_path / "report.csv"
    assert cli.run(tmp_path, report, "ffmpeg", False) == 1
    assert (tmp_path / "a.mp3").exists()
    assert (tmp_path / "128/b.mp3").exists()
    assert "move failed: locked" in report.read_text()


def test_corrupt_metadata_returns_partial_failure(tmp_path, monkeypatch):
    (tmp_path / "bad.mp3").write_bytes(b"not mp3")
    assert cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", True) == 1


def test_report_write_error_returns_failure(tmp_path, monkeypatch):
    def fail_report(*args):
        raise PermissionError("locked report")

    monkeypatch.setattr(cli, "write_report", fail_report)
    assert cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", True) == 1


def test_parallel_analysis_is_bounded_and_matches_sequential(tmp_path, monkeypatch):
    paths = [tmp_path / f"{i}.mp3" for i in range(8)]
    for path in paths:
        path.touch()
    monkeypatch.setattr(cli, "read_mp3_metadata", lambda path: (128, 12.0))
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    peak = 0

    def parallel_measure(path, exe, timeout):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return -20.0 + int(path.stem), None

    monkeypatch.setattr(cli, "measure_loudness", parallel_measure)
    parallel, failed = cli.analyze_files(paths, tmp_path, sys.executable, 2, False, 300, True)
    assert not failed
    assert peak == 2
    monkeypatch.setattr(
        cli, "measure_loudness", lambda path, exe, timeout: (-20.0 + int(path.stem), None)
    )
    sequential, failed = cli.analyze_files(paths, tmp_path, sys.executable, 1, False, 300, True)
    assert not failed
    assert parallel == sequential


def test_skip_loudness_needs_no_ffmpeg_and_marks_csv(tmp_path, monkeypatch):
    (tmp_path / "song.mp3").touch()
    monkeypatch.setattr(cli, "read_mp3_metadata", lambda path: (128, 12.0))

    def unexpected(*args, **kwargs):
        pytest.fail("metadata-only analysis must not resolve or start FFmpeg")

    monkeypatch.setattr(cli.shutil, "which", unexpected)
    monkeypatch.setattr(cli, "measure_loudness", unexpected)
    report = tmp_path / "metadata.csv"
    assert cli.run(tmp_path, report, "missing", True, skip_loudness=True) == 0
    with report.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["integrated_loudness_lufs"] == ""
    assert row["volume_percent"] == ""
    assert row["analysis_status"] == "skipped (--skip-loudness)"
    assert (tmp_path / "song.mp3").exists()


@pytest.mark.parametrize("option", ["--workers", "--timeout"])
@pytest.mark.parametrize("value", ["0", "-1", "bad"])
def test_invalid_performance_options(option, value):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["music", option, value])
    assert exc.value.code == 2


def test_timeout_is_forwarded(tmp_path, monkeypatch):
    def timeout_run(command, **kwargs):
        assert kwargs["timeout"] == 7
        raise subprocess.TimeoutExpired(command, 7)

    monkeypatch.setattr(cli.subprocess, "run", timeout_run)
    assert cli.measure_loudness(tmp_path / "song.mp3", sys.executable, 7) == (
        None,
        "FFmpeg timed out after 7 seconds",
    )


def test_parallel_failures_preserve_success_and_progress(tmp_path, monkeypatch, capsys):
    paths = [tmp_path / "bad.mp3", tmp_path / "ok.mp3"]

    def metadata(path):
        if path.stem == "bad":
            raise ValueError("invalid stream")
        return 128, 12.0

    monkeypatch.setattr(cli, "read_mp3_metadata", metadata)
    monkeypatch.setattr(cli, "measure_loudness", lambda *args: (-20.0, None))
    records, failed = cli.analyze_files(paths, tmp_path, sys.executable, 2, False, 300, False)
    assert failed
    assert [record.relative_path for record in records] == [Path("ok.mp3")]
    assert "Analyzed 2/2" in capsys.readouterr().err


def test_duplicate_move_names_are_deterministic_across_worker_counts(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    for workers in (1, 4):
        root = tmp_path / str(workers)
        for album in ("a", "b"):
            (root / album).mkdir(parents=True)
            (root / album / "song.mp3").write_text(album)
        assert cli.run(root, root / "report.csv", "ffmpeg", False, workers=workers, quiet=True) == 0
        assert (root / "128/song.mp3").read_text() == "a"
        assert (root / "128/song (1).mp3").read_text() == "b"


def test_cache_reuses_results_and_invalidates_changed_files(tmp_path, monkeypatch, capsys):
    mock_analysis(monkeypatch)
    for name in ("a.mp3", "b.mp3"):
        (tmp_path / name).write_bytes(b"original")
    calls = []

    def measure(path, exe, timeout):
        calls.append(path.name)
        return -20.0, None

    monkeypatch.setattr(cli, "measure_loudness", measure)
    report = tmp_path / "report.csv"
    assert cli.run(tmp_path, report, "ffmpeg", True, quiet=True) == 0
    first = report.read_bytes()
    assert sorted(calls) == ["a.mp3", "b.mp3"]
    calls.clear()
    assert cli.run(tmp_path, report, "ffmpeg", True, quiet=True) == 0
    assert calls == []
    assert report.read_bytes() == first
    assert "Cache: 2 reused; 0 analyzed" in capsys.readouterr().out
    (tmp_path / "a.mp3").write_bytes(b"changed file")
    assert cli.run(tmp_path, report, "ffmpeg", True, quiet=True) == 0
    assert calls == ["a.mp3"]
    calls.clear()
    assert cli.run(tmp_path, report, "ffmpeg", True, quiet=True, refresh_cache=True) == 0
    assert sorted(calls) == ["a.mp3", "b.mp3"]


def test_cache_is_invalidated_by_timestamp_and_executable(tmp_path):
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"v1")
    source = tmp_path / "song.mp3"
    source.write_bytes(b"song")
    database = tmp_path / "cache.sqlite3"
    cache = AnalysisCache(database, str(executable))
    stamp = file_stamp(source)
    cache.put(source, stamp, 128, 10.0, -20.0)
    assert cache.get(source, stamp) == (128, 10.0, -20.0)
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert cache.get(source, file_stamp(source)) is None
    cache.close()
    executable.write_bytes(b"different build")
    cache = AnalysisCache(database, str(executable))
    assert cache.get(source, stamp) is None
    cache.close()


def test_failures_and_files_changed_during_analysis_are_not_cached(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    source = tmp_path / "song.mp3"
    source.write_bytes(b"original")
    calls = []

    def measure(path, exe, timeout):
        calls.append(path)
        if len(calls) == 1:
            return None, "transient failure"
        if len(calls) == 2:
            path.write_bytes(b"modified during analysis")
        return -20.0, None

    monkeypatch.setattr(cli, "measure_loudness", measure)
    report = tmp_path / "report.csv"
    for expected in (1, 0, 0, 0):
        assert cli.run(tmp_path, report, "ffmpeg", True, quiet=True) == expected
    assert len(calls) == 3


def test_corrupt_cache_falls_back_without_overwriting_it(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    (tmp_path / "song.mp3").touch()
    database = tmp_path / "cache.sqlite3"
    database.write_bytes(b"not a database")
    assert (
        cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", True, cache_path=database, quiet=True)
        == 0
    )
    assert database.read_bytes() == b"not a database"


def test_no_cache_does_not_create_database(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    (tmp_path / "song.mp3").touch()
    assert cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", True, no_cache=True) == 0
    assert not (tmp_path / ".mp3-manager-cache.sqlite3").exists()


def test_cache_cannot_be_report_or_audio(tmp_path):
    path = tmp_path / "report.sqlite3"
    assert cli.run(tmp_path, path, "ffmpeg", True, cache_path=path) == 2
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    assert cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", True, cache_path=audio) == 2
    assert audio.read_bytes() == b"audio"


def test_cache_hit_recalculates_relative_volume(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    (tmp_path / "a.mp3").touch()
    monkeypatch.setattr(
        cli, "measure_loudness", lambda path, *args: (-20.0 if path.stem == "a" else -10.0, None)
    )
    report = tmp_path / "report.csv"
    assert cli.run(tmp_path, report, "ffmpeg", True) == 0
    (tmp_path / "b.mp3").touch()
    assert cli.run(tmp_path, report, "ffmpeg", True) == 0
    with report.open(encoding="utf-8-sig", newline="") as stream:
        rows = {row["file_name"]: row for row in csv.DictReader(stream)}
    assert rows["a.mp3"]["volume_percent"] == "0.00"
    assert rows["b.mp3"]["volume_percent"] == "100.00"


@pytest.mark.parametrize(
    "original,expected",
    [
        ("Àâàíñ", "Аванс"),
        ("Çâåçäà", "Звезда"),
        ("Ùè", "Щи"),
        ("ßäð¸íà Âîøü", "Ядрёна Вошь"),
        ("Super good (ñóïåð ãóä)", "Super good (супер гуд)"),
        ("À ìíå âñ¸", "А мне всё"),
        ("Аванс".encode().decode("latin1"), "Аванс"),
        ("Привет Àâàíñ", "Привет Аванс"),
    ],
)
def test_name_repair_reversible_examples(original, expected):
    assert names.suggest_text(original)[0] == expected


def test_exact_acoustic_version_filename_round_trips_and_is_planned(tmp_path):
    # Literal original byte-preserving characters, including C1 controls hidden in some UIs.
    broken = (
        "Ð¯ Ð¾Ñ\u0081Ñ\u0082Ð°Ñ\u008eÑ\u0081Ñ\u008c "
        "(Ð°ÐºÑ\u0083Ñ\u0081Ñ\u0082Ð¸Ñ\u0087ÐµÑ\u0081ÐºÐ°Ñ\u008f "
        "Ð²ÐµÑ\u0080Ñ\u0081Ð¸Ñ\u008f).mp3"
    )
    expected = "Я остаюсь (акустическая версия).mp3"
    assert "?" not in broken
    assert broken.encode("latin1").decode("utf-8") == expected
    assert expected.encode("utf-8").decode("latin1") == broken
    assert names.suggest_text(broken)[0] == expected
    source = tmp_path / broken
    source.touch()
    changes = names.plan_changes([source], filenames=True, tags=False)
    assert len(changes) == 1
    assert changes[0].proposed == expected
    assert changes[0].status == "proposed"


@pytest.mark.parametrize(
    "original",
    [
        "Музыка",
        "А Я Все Летала",
        "ya ostanus odna",
        "Beyoncé",
        "Mötley Crüe",
        "Sigur Rós",
        "Déjà vu",
        "café",
        "AC DC",
        "東京",
    ],
)
def test_name_repair_leaves_valid_and_transliterated_names(original):
    assert names.suggest_text(original) == (None, "")


@pytest.mark.parametrize("original", ["ÐÐ½Ð¸ ÐÐµÑÑÑ", "Ð¯ Ð¾ÑÑÐ°ÑÑÑ", "broken�title", "bad\nname"])
def test_name_repair_does_not_invent_missing_characters(original):
    proposed, reason = names.suggest_text(original)
    assert proposed is None
    assert "review" in reason


@pytest.mark.parametrize(
    "filename",
    [
        "CON.mp3",
        "LPT1.mp3",
        "../track.mp3",
        "track?.mp3",
        "track.mp3.",
        "a" * 256 + ".mp3",
        "name.txt",
    ],
)
def test_name_validation_blocks_unsafe_targets(filename):
    assert names.invalid_filename(filename)


def test_filename_repair_previews_unique_names_for_collisions(tmp_path):
    a = tmp_path / "Àâàíñ.mp3"
    b = tmp_path / "Аванс.mp3"
    a.touch()
    b.touch()
    changes = names.plan_changes([a, b], True, False)
    assert len(changes) == 1
    assert changes[0].status == "proposed"
    assert changes[0].proposed == "Аванс (1).mp3"


def test_dry_run_never_applies_name_repairs_even_with_accept(tmp_path):
    source = tmp_path / "Àâàíñ.mp3"
    source.write_bytes(b"audio")
    renamed, statuses, failed = names.repair_names(
        [source],
        tmp_path,
        tmp_path / "report.csv",
        filenames=True,
        tags=False,
        dry_run=True,
        accept=True,
    )
    assert not renamed and not failed
    assert statuses[source] == "filename: dry-run"
    assert source.read_bytes() == b"audio"
    assert not (tmp_path / "Аванс.mp3").exists()
    audit = next(tmp_path.glob("report.name-fixes-*.csv"))
    assert audit.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "Аванс.mp3" in audit.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("answer", ["yes", "no", ""])
def test_one_batch_prompt_and_decline_continues_processing(tmp_path, monkeypatch, answer):
    mock_analysis(monkeypatch)
    for name in ("Àâàíñ.mp3", "Çâåçäà.mp3"):
        (tmp_path / name).write_bytes(b"audio")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    questions = []

    def respond(prompt):
        questions.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", respond)
    assert cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", False, fix_filenames=True) == 0
    assert len(questions) == 1
    expected = "Аванс.mp3" if answer == "yes" else "Àâàíñ.mp3"
    assert (tmp_path / "128" / expected).read_bytes() == b"audio"


def test_noninteractive_repair_does_not_auto_accept(tmp_path, monkeypatch):
    source = tmp_path / "Àâàíñ.mp3"
    source.touch()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    renamed, statuses, _ = names.repair_names(
        [source],
        tmp_path,
        tmp_path / "report.csv",
        filenames=True,
        tags=False,
        dry_run=False,
        accept=False,
    )
    assert not renamed
    assert statuses[source] == "filename: declined"


def test_accepted_rename_in_place_updates_report(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    source = tmp_path / "Àâàíñ.mp3"
    source.write_bytes(b"audio")
    report = tmp_path / "report.csv"
    assert (
        cli.run(
            tmp_path,
            report,
            "ffmpeg",
            False,
            fix_filenames=True,
            accept_name_fixes=True,
            no_move=True,
        )
        == 0
    )
    assert (tmp_path / "Аванс.mp3").read_bytes() == b"audio"
    assert not (tmp_path / "128").exists()
    with report.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["file_name"] == "Аванс.mp3"
    assert row["original_relative_path"] == "Àâàíñ.mp3"
    assert row["name_repair_status"] == "filename: applied"


@pytest.mark.parametrize("version,encoding", [(3, 1), (4, 3)])
def test_tag_repair_preserves_audio_art_version_and_creates_backup(tmp_path, version, encoding):
    source = tmp_path / "song.mp3"
    payload = b"UNCHANGED_AUDIO_PAYLOAD"
    source.write_bytes(payload)
    tags = ID3()
    tags.add(TIT2(encoding=1, text=["Àâàíñ"]))
    tags.add(TPE1(encoding=1, text=["Правильный артист"]))
    tags.add(APIC(encoding=0, mime="image/jpeg", type=3, data=b"cover-art"))
    tags.save(source, v2_version=version)
    original = source.read_bytes()
    _, statuses, failed = names.repair_names(
        [source],
        tmp_path,
        tmp_path / "report.csv",
        filenames=False,
        tags=True,
        dry_run=False,
        accept=True,
    )
    assert not failed
    after = ID3(source, translate=False)
    assert after["TIT2"].text == ["Аванс"]
    assert after["TIT2"].encoding == encoding
    assert after["TPE1"].text == ["Правильный артист"]
    assert after.getall("APIC")[0].data == b"cover-art"
    assert after.version[1] == version
    assert source.read_bytes().endswith(payload)
    assert "applied" in statuses[source]
    backups = list((tmp_path / names.BACKUP_DIR).rglob("*.mp3"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert cli.find_mp3_files(tmp_path, include_sorted=True) == [source]


def test_tag_save_failure_restores_backup(tmp_path, monkeypatch):
    source = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(TIT2(encoding=1, text=["Àâàíñ"]))
    tags.save(source)
    original = source.read_bytes()

    def broken_save(self, filename, **kwargs):
        filename.write_bytes(b"partial write")
        raise OSError("save failed")

    monkeypatch.setattr(ID3, "save", broken_save)
    _, statuses, failed = names.repair_names(
        [source],
        tmp_path,
        tmp_path / "report.csv",
        filenames=False,
        tags=True,
        dry_run=False,
        accept=True,
    )
    assert failed
    assert source.read_bytes() == original
    assert "failed" in statuses[source]


def test_rename_never_overwrites_existing_file(tmp_path):
    source, destination = tmp_path / "a.mp3", tmp_path / "b.mp3"
    source.write_bytes(b"a")
    destination.write_bytes(b"b")
    with pytest.raises(FileExistsError):
        names.rename_no_replace(source, destination)
    assert source.read_bytes() == b"a"
    assert destination.read_bytes() == b"b"


def test_include_sorted_does_not_move_file_onto_itself(tmp_path, monkeypatch):
    mock_analysis(monkeypatch)
    source = tmp_path / "128" / "song.mp3"
    source.parent.mkdir()
    source.write_bytes(b"audio")
    assert cli.run(tmp_path, tmp_path / "report.csv", "ffmpeg", False, include_sorted=True) == 0
    assert list(source.parent.iterdir()) == [source]


def test_legacy_id3v1_upgraded_to_unicode_with_backup(tmp_path):
    source = tmp_path / "song.mp3"
    payload = b"AUDIO" * 100
    legacy = (
        b"TAG"
        + "Аванс".encode("cp1251").ljust(30, b"\0")
        + "Ария".encode("cp1251").ljust(30, b"\0")
        + b"\0" * 30
        + b"1999"
        + b"\0" * 30
        + b"\x00"
    )
    assert len(legacy) == 128
    source.write_bytes(payload + legacy)
    assert ID3(source).version[0] == 1
    _, _, failed = names.repair_names(
        [source],
        tmp_path,
        tmp_path / "report.csv",
        filenames=False,
        tags=True,
        dry_run=False,
        accept=True,
    )
    assert not failed
    result = ID3(source)
    assert result.version[:2] == (2, 3)
    assert result["TIT2"].text == ["Аванс"]
    assert result["TPE1"].text == ["Ария"]
    assert source.read_bytes().endswith(payload)
    backup = next((tmp_path / names.BACKUP_DIR).rglob("*.mp3"))
    assert backup.read_bytes() == payload + legacy


def tagged_duplicate(
    path, payload=b"audio-payload" * 100, artist="A-Europa", title="В Риге девчёнки"
):
    path.write_bytes(payload)
    tags = ID3()
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TIT2(encoding=3, text=title))
    tags.save(path)
    return path


def test_dedup_matches_audio_not_tags_or_size(tmp_path):
    first = tagged_duplicate(tmp_path / "first.mp3")
    second = tagged_duplicate(tmp_path / "second.mp3", artist="Different metadata")
    other = tagged_duplicate(tmp_path / "other.mp3", payload=b"other-payload" * 100)
    assert dedup.payload_fingerprint(first)[:2] == dedup.payload_fingerprint(second)[:2]
    assert dedup.payload_fingerprint(first)[:2] != dedup.payload_fingerprint(other)[:2]
    groups, failed = dedup.find_duplicates([first, second, other])
    assert not failed and len(groups) == 1
    assert "conflicting tags" in groups[0].reason
    assert groups[0].target == groups[0].keeper.path


def test_dedup_prefers_verified_full_name(tmp_path):
    full = tagged_duplicate(tmp_path / "A-Europa - В Риге девчёнки.mp3")
    short = tagged_duplicate(tmp_path / "В Риге девчёнки.mp3")
    numbered = tagged_duplicate(tmp_path / "A-Europa - В Риге девчёнки (1).mp3")
    groups, failed = dedup.find_duplicates([short, numbered, full])
    assert not failed
    assert groups[0].keeper.path == full
    assert groups[0].target == full


@pytest.mark.parametrize("dry_run,accept", [(True, True), (True, False), (False, False)])
def test_dedup_preview_and_noninteractive_keep_everything(tmp_path, monkeypatch, dry_run, accept):
    files = [tagged_duplicate(tmp_path / name) for name in ("one.mp3", "two.mp3")]
    original = {p: p.read_bytes() for p in files}
    monkeypatch.setattr(dedup.sys.stdin, "isatty", lambda: False)
    renamed, removed, failed = dedup.deduplicate_files(
        files, tmp_path, tmp_path / "report.csv", dry_run=dry_run, accept=accept, quiet=True
    )
    assert not renamed and not removed and not failed
    assert all(p.read_bytes() == data for p, data in original.items())
    assert not (tmp_path / dedup.QUARANTINE_DIR).exists()
    audit = next(tmp_path.glob("report.duplicates-*.csv"))
    assert audit.read_bytes().startswith(b"\xef\xbb\xbf")


def test_dedup_quarantines_and_completes_keeper_from_tags(tmp_path):
    files = [tagged_duplicate(tmp_path / name) for name in ("one.mp3", "two.mp3")]
    original = {p: p.read_bytes() for p in files}
    renamed, removed, failed = dedup.deduplicate_files(
        files, tmp_path, tmp_path / "report.csv", dry_run=False, accept=True, quiet=True
    )
    assert not failed and len(removed) == len(renamed) == 1
    kept = next(iter(renamed.values()))
    assert kept.name == "A-Europa - В Риге девчёнки.mp3"
    assert kept.read_bytes() == original[next(iter(renamed))]
    quarantined = list((tmp_path / dedup.QUARANTINE_DIR).rglob("*.mp3"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == original[next(iter(removed))]
    assert cli.find_mp3_files(tmp_path, include_sorted=True) == [kept]
    assert not dedup.find_duplicates([kept])[0]


def test_dedup_rechecks_after_confirmation(tmp_path, monkeypatch):
    files = [tagged_duplicate(tmp_path / name) for name in ("one.mp3", "two.mp3")]
    monkeypatch.setattr(dedup.sys.stdin, "isatty", lambda: True)

    def approve(prompt):
        files[1].write_bytes(b"changed after preview")
        return "yes"

    monkeypatch.setattr("builtins.input", approve)
    renamed, removed, failed = dedup.deduplicate_files(
        files, tmp_path, tmp_path / "report.csv", dry_run=False, accept=False, quiet=True
    )
    assert failed and not renamed and not removed
    assert all(p.exists() for p in files)


def test_dedup_declined_plan(tmp_path, monkeypatch):
    files = [tagged_duplicate(tmp_path / name) for name in ("one.mp3", "two.mp3")]
    monkeypatch.setattr(dedup.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert dedup.deduplicate_files(
        files, tmp_path, tmp_path / "report.csv", dry_run=False, accept=False, quiet=True
    ) == ({}, set(), False)


def test_dedup_keeper_rename_collision(tmp_path):
    files = [tagged_duplicate(tmp_path / name) for name in ("one.mp3", "two.mp3")]
    existing = tagged_duplicate(tmp_path / "A-Europa - В Риге девчёнки.mp3", payload=b"different")
    groups, failed = dedup.find_duplicates(files + [existing])
    assert not failed
    assert groups[0].target.name == "A-Europa - В Риге девчёнки (1).mp3"


def test_dedup_failed_quarantine_preserves_files(tmp_path, monkeypatch):
    files = [tagged_duplicate(tmp_path / name) for name in ("one.mp3", "two.mp3")]

    def fail(source, destination):
        raise PermissionError("locked")

    monkeypatch.setattr(dedup, "rename_no_replace", fail)
    renamed, removed, failed = dedup.deduplicate_files(
        files, tmp_path, tmp_path / "report.csv", dry_run=False, accept=True, quiet=True
    )
    assert failed and not renamed and not removed
    assert all(p.exists() for p in files)


def test_dedup_cli_report_contains_only_survivors(tmp_path, monkeypatch):
    for name in ("one.mp3", "two.mp3"):
        tagged_duplicate(tmp_path / name)
    monkeypatch.setattr(cli, "read_mp3_metadata", lambda path: (192, 10.0))
    report = tmp_path / "report.csv"
    assert (
        cli.run(
            tmp_path,
            report,
            "unused",
            False,
            skip_loudness=True,
            no_move=True,
            deduplicate=True,
            accept_duplicates=True,
        )
        == 0
    )
    with report.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert not (tmp_path / "192").exists()
    assert len(cli.find_mp3_files(tmp_path, True)) == 1


def test_dedup_accept_flag_requires_option():
    with pytest.raises(SystemExit):
        cli.parse_args([".", "--accept-duplicates"])


def test_dedup_id3v1_and_v24_footer(tmp_path):
    plain = tmp_path / "plain.mp3"
    tagged = tmp_path / "tagged.mp3"
    plain.write_bytes(b"payload" * 100)
    header = b"ID3\x04\x00\x10\x00\x00\x00\x00"
    tagged.write_bytes(header + b"3DI" + header[3:] + plain.read_bytes() + b"TAG" + bytes(125))
    assert dedup.payload_fingerprint(plain)[:2] == dedup.payload_fingerprint(tagged)[:2]
    tagged.write_bytes(header + b"bad footer")
    with pytest.raises(ValueError, match="footer"):
        dedup.payload_fingerprint(tagged)
