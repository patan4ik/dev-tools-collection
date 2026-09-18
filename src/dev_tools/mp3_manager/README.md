# MP3 manager

Recursively inspect MP3 metadata, measure integrated loudness with FFmpeg, move
files into bitrate folders, and write a CSV report. Audio is never transcoded or
volume-normalized. Requires Python 3.11+ and Mutagen; loudness analysis additionally
requires a separate FFmpeg executable with the `ebur128` filter.
This file is named `README.md` as requested.

## Install and preview

From the repository root in your activated virtual environment:

```powershell
python -m pip install -e ".[mp3]"
ffmpeg -version
python -m dev_tools.mp3_manager.cli "C:\Music" --dry-run --report "C:\Reports\preview.csv"
```

The lightweight `mp3` extra installs Mutagen without speech-recognition packages.
The existing `audio` extra also includes Mutagen for compatibility. A base
`pip install -e .` does not install optional MP3 dependencies.

After inspecting the preview, run:

```powershell
mp3-manager "C:\Music" --report "C:\Reports\sorted.csv"
```

Both entry points have the same options:

| Argument | Meaning |
| --- | --- |
| `folder` | Root directory to scan recursively |
| `--report PATH` | CSV destination; default `<folder>/mp3_report.csv` |
| `--ffmpeg-bin PATH` | Executable path or command name; default `ffmpeg` from PATH |
| `--dry-run` | Analyze and write CSV without moving MP3 files |
| `--workers N` | Concurrent analyses; defaults to CPU count capped at 4; `1` is sequential |
| `--skip-loudness` | Metadata and bitrate only; no FFmpeg required, blank LUFS/volume columns |
| `--timeout SECONDS` | Positive per-file FFmpeg timeout; default 300 |
| `--quiet` | Hide progress and per-file results; retain warnings and final summary |
| `--cache PATH` | SQLite cache; default `<folder>/.mp3-manager-cache.sqlite3` |
| `--no-cache` | Disable all cache reads and writes |
| `--refresh-cache` | Reanalyze files and replace successful cached measurements |
| `--fix-filenames` | Preview and confirm batch Cyrillic filename repairs |
| `--fix-tags` | Preview and confirm title/artist/album repairs, with full-file backups |
| `--accept-name-fixes` | Explicitly approve proposed repairs without an interactive prompt |
| `--no-move` | Analyze/repair in place without bitrate segregation |
| `--include-sorted` | Also inspect files already in bitrate folders |
| `--csv-encoding` | `utf-8-sig` (default, BOM for Excel) or `utf-8` |

For a path containing spaces, quote the executable path without adding arguments:

```powershell
mp3-manager "C:\Music" --dry-run --ffmpeg-bin "C:\Tools\FFmpeg\bin\ffmpeg.exe"
```

See the [Windows 11 FFmpeg guide](../../../docs/mp3-manager/WINDOWS11_FFMPEG.md).
For corrupted Russian names and player metadata, see the
[batch filename and tag repair guide](../../../docs/mp3-manager/FILENAME_REPAIR.md).

## Performance and choosing a mode

See the [local performance validation](../../../docs/mp3-manager/PERFORMANCE.md)
for measured results, limitations, and a repeatable comparison procedure.

Dry-run mode still decodes every MP3 to measure full-track integrated loudness.
Skipping moves does not remove that work. By default, up to four Python worker
threads coordinate independent FFmpeg processes. They work on separate files;
report writing and moves remain sequential and preserve deterministic filename
allocation. There are at most `2 * workers` submitted futures and at most `workers`
active analyses. Progress prints completed counts and elapsed time approximately
once per second, including while waiting for a slow file.

```powershell
# Full-track LUFS, bounded parallel execution (also the default on 4+ CPU systems):
mp3-manager "C:\Music" --dry-run --workers 4 --report "C:\Reports\preview.csv"

# Fast bitrate/duration inventory; no audio decoding or loudness measurement:
mp3-manager "C:\Music" --dry-run --skip-loudness --report "C:\Reports\metadata.csv"
```

Start with four workers on local SSD storage. Try two for a busy machine, spinning
disk, or network share; more parallel reads may reduce throughput. Compare 1, 2,
4, and optionally 8 on the same representative sample using the elapsed summary.
No fixed speedup is guaranteed: track duration, CPU, storage, file cache, and other
workloads matter. The tool neither samples excerpts nor lowers sample rate to
approximate integrated loudness. `--skip-loudness` is an explicit loss of that
measurement, marked `skipped (--skip-loudness)` in CSV; it is not an error and does
not prevent moves when `--dry-run` is absent.

The optimized FFmpeg filter disables unused true-peak analysis and per-frame logs;
the report only needs the final integrated loudness summary. FFmpeg decoder,
filter-pipeline, and output codec thread settings are limited to one to reduce
CPU oversubscription across concurrent files (FFmpeg can still use internal I/O
threads). Bucket directories are pruned before traversal instead of scanned then
discarded. Cancellation cancels queued jobs; active FFmpeg calls can take until
their per-file timeout to finish. Successful results are cached between runs.

Ordinary Python threads are sufficient here because CPU-intensive decoding runs
in external FFmpeg processes. Python 3.13's optional free-threaded interpreter is
not required; this implementation remains compatible with Python 3.11+. A Python
process pool would add workers and IPC without eliminating the FFmpeg work. GPU
acceleration is not implemented for this CPU audio-analysis pipeline.
See [Python threading](https://docs.python.org/3.13/library/threading.html),
[FFmpeg ebur128](https://ffmpeg.org/ffmpeg-filters.html#ebur128-1), and
[FFmpeg filter threads](https://ffmpeg.org/ffmpeg.html#Advanced-options).

## Persistent analysis cache

Full analysis automatically stores successful metadata and LUFS in
`<folder>/.mp3-manager-cache.sqlite3`. Later runs reuse unchanged files and only
decode new or changed files. Dry runs write both the cache and CSV, without moving
MP3 files. Metadata-only runs neither read nor update the cache. Cached analysis
uses the same report columns, and relative volume is recalculated across the current set of files.

The coordinator commits each successful result separately, so completed records
survive interruptions. Workers do not share the SQLite connection. Missing,
unreadable, or corrupt caches produce a warning and fall back to normal analysis;
no corrupt database is automatically deleted. Use a new cache path or remove the
old cache after inspecting it. Cache files must have the `.sqlite3` extension and
must not be symlinks or the CSV destination.

Reuse requires matching absolute file path, size, nanosecond mtime/ctime
timestamps, device and file identity, plus FFmpeg executable path/stat fingerprint,
Mutagen version, and an internal analysis-version identifier. A file changed while
analysis was running is not cached. Analysis errors are retried on subsequent runs.
Changing workers or timeout does not invalidate successful measurements.

This is a stat-based cache, not a cryptographic content-integrity check. Deliberate
content changes preserving all recorded attributes, external FFmpeg DLL changes,
or filesystem timestamp limitations may evade invalidation. Use `--refresh-cache`
after such changes or when you need independent remeasurement. `--no-cache`
disables reuse and writes entirely and takes precedence over `--refresh-cache`.
Old CSV reports cannot safely seed this cache because they lack fingerprints.

```powershell
# Same command on subsequent runs automatically reuses stored measurements:
mp3-manager "C:\Music" --dry-run --quiet

# Store the cache separately from a read-only music directory:
mp3-manager "C:\Music" --dry-run --cache "C:\Reports\music.sqlite3" --report "C:\Reports\preview.csv"

# Force a fresh full-track measurement:
mp3-manager "C:\Music" --dry-run --refresh-cache
```

The summary reports cache hits separately from newly analyzed files. No speedup is
promised for a first scan or changed files. Renamed/moved files are cache misses;
already sorted bucket directories remain excluded. Deleted-file entries are harmless
but are not automatically pruned; deleting the cache rebuilds it on the next run.

## Buckets and moves

The following thresholds refine the original tool design. Classification uses
Mutagen's bitrate rounded to integer kbps; VBR files use its reported average.

| Reported kbps | Destination folder |
| --- | --- |
| 360 or higher | `360` |
| 256 through 359 | `256` |
| 192 through 255 | `192` |
| 160 through 191 | `160` |
| 128 through 159 | `128` |
| Below 128 | `64` |

These are ranges, not target encoding rates: 320 kbps goes into `256`, 250 into
`192`, and 96 into `64`. The `64` label also includes lower rates. Consider changing
these labels before organizing a large library if exact bitrate grouping is wanted.

Files already beneath the six bucket directories directly under the scan root
are excluded unless `--include-sorted` is supplied. Other nested directories named
`128`, etc. are scanned; backup folders remain excluded. Extension matching is
case-insensitive. Symbolic-link files and paths resolving outside the
root are skipped. Bucket directories resolving outside the root are rejected.

To redistribute files previously placed in `128`, rerun against the **original
library root**, including sorted subfolders:

```powershell
mp3-manager "C:\Music" --include-sorted --dry-run --report "C:\Music\Reports\rebucket-preview.csv"
mp3-manager "C:\Music" --include-sorted --report "C:\Music\Reports\sorted.csv"
```

Files already in their correct bucket stay in place. Destination filename
collisions receive numbered suffixes; existing files are preserved. Empty old
directories remain. Do not use `--no-move` when redistributing. Cached bitrate
measurements are assigned using the new thresholds without cache invalidation;
files moved or renamed since their cache entry was recorded can still be misses.
For sorting without LUFS, add `--skip-loudness` (measurement columns will be blank).

Moves flatten album subdirectories; existing destination names receive ` (1)`,
` (2)`, etc. Empty source directories remain. Preview first and keep a backup:
there is no undo command or transaction across moves. Do not run concurrent
organizers on the same directory; name selection is not a filesystem lock.

## Loudness and report semantics

FFmpeg analyzes the first audio stream with `ebur128=peak=none:framelog=verbose`,
using a configurable timeout (300 seconds per file by default). Integrated LUFS
comes from the final summary; unused true-peak values are not calculated.
`volume_percent` is a linear position between the quietest (0) and loudest (100)
successful LUFS measurements in this run. It is **not** playback volume, amplitude,
or a perceptual loudness percentage. One measured file, or identical measurements,
receives 100 by convention. Keep `integrated_loudness_lufs` for cross-run comparisons.

CSV columns are `file_name`, `relative_path` (relative to scan root),
`destination_path` (relative to report directory when possible, absolute otherwise),
`bitrate_kbps`, `bitrate_bucket_kbps`, `duration_seconds`,
`integrated_loudness_lufs`, `volume_percent`, and `analysis_status`.
CSV defaults to UTF-8 with BOM for Excel; use `--csv-encoding utf-8` for BOM-free
output. Name repair adds audit columns when issues are reviewed; see its guide.
Rows sort by descending bitrate, descending relative loudness, then filename path.
Dry runs leave destination paths empty. Existing reports are overwritten, including
on dry runs; use a different report name to retain history. MP3 and symlink report
destinations are rejected.

Unreadable metadata is warned about and skipped. Loudness errors leave measurement
columns empty and appear in `analysis_status`; valid-metadata files still move.
Move failures appear in that same status column, leave destination blank, and do
not stop subsequent files. Report-write failures can occur after files have moved.
Rerunning excludes already sorted files, so it does not reconstruct an earlier report.

Exit codes: `0` = completed (including an empty scan); `1` = metadata, measurement,
move, or report failure; `2` = invalid setup or arguments. Missing FFmpeg is detected
before analysis or moves. Only use a trusted FFmpeg binary; the executable is run
as a separate argument list with no shell, and Windows batch wrappers are rejected.

## Suggested follow-up work

1. Decide whether to replace the surprising `360` threshold with exact/common
   bitrate groups, or expose configurable ranges. This changes existing behavior.
2. Add a durable move manifest and rollback/resume, atomic collision reservation,
   and a report written atomically before/after each move for recovery.
3. Add cache pruning and optional content hashing for libraries
   where stronger validation is more important than avoiding extra file reads.
4. Include skipped metadata failures as CSV rows and record FFmpeg version and
   run metadata to make reports auditable.
5. Add real FFmpeg integration tests in CI, including silence, VBR, damaged files,
   embedded cover art, and Windows Unicode paths. Unit tests mock FFmpeg; local
   real-file benchmarks are supplemental evidence, not a cross-platform CI guarantee.

The collection version remains `1.7.0`; aligning package and individual-tool release
versions needs a repository-wide release decision.

Filter reference: [FFmpeg ebur128 documentation](https://ffmpeg.org/ffmpeg-filters.html#ebur128-1).


## Duplicate review

Use `--deduplicate` to review identical encoded audio, prefer detailed artist/title filenames, and quarantine approved extras. Missing keeper names can be completed from consistent ID3 artist/title tags. This does not merge differently encoded recordings merely because size, duration, or tags match.

```powershell
mp3-manager "C:\Music" --include-sorted --deduplicate --skip-loudness --no-move --report "C:\Music\Reports\dedup.csv"
```

The command displays each keeper, proposed rename, and duplicate, then asks once for approval. Add `--dry-run` for a preview. `--no-move` disables bitrate segregation; approved duplicate quarantine and keeper renames still apply. `--skip-loudness` omits LUFS measurements to avoid FFmpeg decoding. See [the duplicate guide](../../../docs/mp3-manager/DEDUPLICATION.md) for matching, recovery, and unattended use.

