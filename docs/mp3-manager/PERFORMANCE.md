# MP3 manager performance validation

## Full-library report and cache validation

The user's full dry run reported 904 files in 380.9 seconds with four workers.
Inspection of that CSV confirmed 904 `ok` statuses and 60.21 hours of aggregate
track duration. This confirms no recorded analysis errors; it is not an independent
audio-quality assessment. Full integrated loudness requires reading/decoding the
tracks, whereas repeated scans can reuse stored measurements.

Caching is enabled by default and commits successful measurements per file to a
SQLite database. The earlier CSV is not imported into the cache: it lacks the
file and executable fingerprints required for validation. Cache creation requires
one fresh full scan. Subsequent scans validate file attributes and regenerate the
CSV without decoding matching files. See the tool guide for fingerprint limitations.

Local full-library validation completed with these results:

| Run | Reused | Newly analyzed | Tool-reported elapsed |
| --- | ---: | ---: | ---: |
| Fresh cache creation | 0 | 904 | 637.3 seconds |
| Cached repeat | 904 | 0 | 0.5 seconds |

The fresh and cached CSVs were byte-for-byte identical to one another and to the
user's original preview. No audio files were moved. These are elapsed times printed
by the tool, excluding interpreter startup. The initial validation run overlapped
development/checks and was slower than the user's 380.9-second run; caching does not
claim to accelerate initial decoding. The 0.5-second result is an observed local
repeat scan, not a guarantee for other machines or changed files.

Validated locally on Windows with Python 3.13.0 and FFmpeg
`2026-09-17-git-7070fe638e-full_build-www.gyan.dev` on 2026-09-18.

## Observed results

A read-only sample comprised the first 16 eligible paths in sorted order from the
user's iPhone music directory. Tracks were decoded in full; no files were moved.

| Mode | Files | Elapsed seconds |
| --- | ---: | ---: |
| Original sequential FFmpeg measurement | 16 | 35.082 |
| Optimized analysis, 1 worker | 16 | 20.118 |
| Optimized analysis, 4 workers | 16 | 12.576 |
| Metadata only, 4 workers | 16 | 0.033 |

All 16 reported integrated LUFS values matched the original implementation exactly
at FFmpeg's reported precision, with no measurement failures. Four-worker analysis
was about 2.8 times faster than the original measurement loop in this run.

Limitations: these are single-run observations in the listed order, without cache
eviction or isolated background load (the regression suite also ran during the
benchmark). The original timing covers loudness measurement; optimized timings
also include metadata reads and worker coordination. CSV output, scanning and moves
are excluded. This is not a prediction for an entire library or other hardware.

A separate end-to-end `--dry-run --skip-loudness --quiet` scan of the directory
found **904 eligible files**, wrote their CSV into the validation workspace, and
reported **3.4 seconds**. No music files were changed. This mode does not measure
LUFS or volume percentages and therefore is not equivalent to full analysis.

## Why these changes help

- Bounded Python threads wait for independent FFmpeg processes, allowing multiple
  CPU-intensive decodes concurrently. Default workers are CPU count capped at four.
- Removing true-peak computation avoids work unused by the report. Only full-track
  integrated loudness is needed.
- `framelog=verbose` with `-loglevel info` retains the final summary while hiding
  frequent per-frame loudness messages. Decoder, filter and output codec thread
  settings are limited to one to reduce oversubscription.
- Pruning destination buckets avoids walking already organized library subtrees.
- Metadata-only mode entirely avoids FFmpeg startup and full audio decoding.

Free-threaded Python is unnecessary: the heavy computation happens outside the
Python interpreter. Ordinary threads work on supported Python 3.11+ installations.
See [Python's threading documentation](https://docs.python.org/3.13/library/threading.html),
[FFmpeg ebur128 options](https://ffmpeg.org/ffmpeg-filters.html#ebur128-1), and
[FFmpeg threading settings](https://ffmpeg.org/ffmpeg.html).

## Reproduce on your own sample

Use a representative folder of copied tracks. Do not run several benchmarks at
the same time. Compare the same files and repeat in reverse order to reduce cache
and background-load bias. These commands write separate reports and never move MP3s:

```powershell
python -m dev_tools.mp3_manager.cli 'C:\MusicSample' --dry-run --workers 1 --no-cache --quiet --report 'C:\Reports\sample-w1.csv'
python -m dev_tools.mp3_manager.cli 'C:\MusicSample' --dry-run --workers 2 --no-cache --quiet --report 'C:\Reports\sample-w2.csv'
python -m dev_tools.mp3_manager.cli 'C:\MusicSample' --dry-run --workers 4 --no-cache --quiet --report 'C:\Reports\sample-w4.csv'
```

Compare elapsed summaries, exit codes, and CSV measurements. Try eight workers only
if CPU and storage have spare capacity. Two or one may be better for spinning disks,
network shares, or a machine running other intensive applications. More workers
increase resource use and do not guarantee more throughput.

## Verification

- 241 tests and 2 subtests passed, including 40 MP3 test cases after cache implementation.
- Regression coverage includes concurrent execution bounded to two workers,
  equality with sequential results, deterministic duplicate-name moves,
  partial failures, metadata-only mode, progress, and timeout/argument validation.
- Black and Ruff passed across source/tests; mypy passed across source and MP3 tests.
- Bandit reported no findings with the two existing reviewed subprocess suppressions.
- Cache tests cover byte-identical repeat reports, per-file invalidation, executable
  changes, forced refresh, transient errors, files changed during analysis, corrupt
  database fallback, disabled caching, destination validation, and relative-volume
  recalculation when files are added.

Persistent caching is now implemented. Benchmarks of uncached decoding must use
`--no-cache` as above. For cache performance, run the same command twice with caching
enabled and compare both elapsed time and the cache-hit summary. File/executable
stat fingerprints, Mutagen version, and analysis-version matching govern reuse;
see the tool guide for limitations and forced refresh.
