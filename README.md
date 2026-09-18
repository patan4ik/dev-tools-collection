# Developer tools collection

Standalone Python command-line tools in a shared `src/dev_tools` package.

| Tool | Install extra | Entry point | Documentation |
| --- | --- | --- | --- |
| Project context | `report` for token counting | `project-context` | [Project context guide](src/dev_tools/project_context/README.md) |
| Meeting context | `audio` for Windows capture and local ASR | `meeting-context` | [Meeting audio guide](src/dev_tools/meeting_context/README.md) |
| MP3 manager | `mp3` for Mutagen | `mp3-manager` | [MP3 manager guide](src/dev_tools/mp3_manager/README.md) |

Install only the extras you need; the collection's base dependencies remain empty.

```powershell
python -m pip install -e ".[dev,report,mp3]"
python -m pytest
```

Meeting context needs a separate approved speech model. No local chat LLM is needed.
Follow the [Windows 11 integration and real-call test procedure](docs/meeting-context/WINDOWS11_GUIDE.md)
before using it for real meeting minutes. See [integration validation](docs/meeting-context/VALIDATION.md)
for the distinction between the new module's checks and existing repository findings.

## MP3 manager

Install the lightweight extra and validate the separately installed FFmpeg:

```powershell
python -m pip install -e ".[mp3]"
ffmpeg -version
python -m dev_tools.mp3_manager.cli "C:\Music" --dry-run
```

Remove `--dry-run` to move files into `360`, `256`, `192`, `160`, `128`, or `64` folders and write
a bitrate-sorted CSV. These are threshold buckets: 320 kbps files go into `256`.
Dry runs still write a report. `volume_percent` is relative to the measured LUFS
range in the current run, not playback volume; use `integrated_loudness_lufs` for
cross-run comparisons. Moving flattens source subdirectories; preview and back up first.

The `160` bucket covers 160–191 kbps and `192` covers 192–255 kbps, including 250.
To redistribute already sorted files, rerun from the original library root with
`--include-sorted` (first add `--dry-run` to preview). Correctly placed files stay put.

See the [tool guide](src/dev_tools/mp3_manager/README.md) for behavior and exit codes,
and the [Windows 11 FFmpeg PATH guide](docs/mp3-manager/WINDOWS11_FFMPEG.md) for setup,
validation, troubleshooting, and `--ffmpeg-bin` usage.

Full loudness analysis now runs up to four files concurrently, with progress and
elapsed time. Use `--workers 2` for lower resource use or `--workers 1` for sequential
analysis. `--dry-run --skip-loudness` provides a fast bitrate/duration inventory
without FFmpeg; LUFS and relative-volume columns are then empty. Dry-run alone
still measures full-track loudness on cache misses. Successful measurements are
saved automatically to `<folder>/.mp3-manager-cache.sqlite3`, so unchanged files
need no repeat decoding. Use `--cache PATH` to choose the location, `--refresh-cache`
to remeasure, or `--no-cache` to disable caching. Dry runs also update the cache.
See the tool guide for tuning and cache-validation limitations.

For corrupted Russian names, `--fix-filenames --fix-tags` previews a batch of
proposed repairs and asks once before applying them. Use `--dry-run` for preview
only, or `--no-move` to repair in place. Tag edits have full-file backups. Reports
now default to UTF-8 with BOM for Excel (`--csv-encoding utf-8` keeps BOM-free output).
See the [filename/tag repair guide](docs/mp3-manager/FILENAME_REPAIR.md) for validation,
collision handling, supported encodings, device limits, and commands.


## Duplicate review

Use `--deduplicate` to review identical encoded audio, prefer detailed artist/title filenames, and quarantine approved extras. Missing keeper names can be completed from consistent ID3 artist/title tags. This does not merge differently encoded recordings merely because size, duration, or tags match.

```powershell
mp3-manager "C:\Music" --include-sorted --deduplicate --skip-loudness --no-move --report "C:\Music\Reports\dedup.csv"
```

The command displays each keeper, proposed rename, and duplicate, then asks once for approval. Add `--dry-run` for a preview. `--no-move` disables bitrate segregation; approved duplicate quarantine and keeper renames still apply. `--skip-loudness` omits LUFS measurements to avoid FFmpeg decoding. See [the duplicate guide](docs/mp3-manager/DEDUPLICATION.md) for matching, recovery, and unattended use.
