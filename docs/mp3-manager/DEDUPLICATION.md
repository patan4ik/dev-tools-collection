# Review and quarantine duplicate MP3s

Run from the original library root, with `--include-sorted` to include existing bitrate folders:

```powershell
mp3-manager "C:\Music" --include-sorted --deduplicate --skip-loudness --no-move --dry-run --report "C:\Music\Reports\dedup.csv"
```

Remove `--dry-run` to display the plan and answer one `[y/N]` confirmation. Rejecting retains all duplicates and processing continues. Noninteractive runs retain duplicates unless explicitly passed `--accept-duplicates`. Dry-run takes precedence over acceptance. If also repairing mojibake with `--fix-filenames` or `--fix-tags`, that repair has its own confirmation before duplicate review.

`--no-move` disables bitrate segregation, not the explicitly requested quarantine or keeper renames. Omit it to segregate surviving files afterward. `--skip-loudness` avoids FFmpeg and leaves LUFS/volume fields blank. Hashing still reads the full encoded payload of every successfully analyzed MP3; selected groups are read again after approval to check for changes. This is disk I/O, not audio decoding. There is no persistent duplicate-hash cache.

## Matching and keeper selection

Files must have the same encoded-payload length and SHA-256 digest. Leading ID3v2 and trailing ID3v1 tags are excluded, so tag differences alone do not prevent matching. ID3v2 sizes and footers follow the [ID3v2.4 structure specification](https://id3.org/id3v2.4.0-structure). Other trailers remain part of the comparison; different APE/Lyrics tags may therefore prevent a match. Malformed or changing files are skipped with a warning and nonzero exit status.

Identical file sizes, bitrates, durations, titles, or artists are insufficient evidence. Different encodings of the same recording remain separate. No acoustic similarity matching or title guessing is performed.

The keeper preference is:

1. Filename containing its tagged artist and title, ignoring case and punctuation.
2. More descriptive filename (ignoring a trailing numeric collision suffix).
3. More populated common metadata fields and cover art.
4. No trailing numeric suffix, then deterministic path order.

For example, `A-Europa - В Риге девчёнки.mp3` is preferred over `В Риге девчёнки.mp3` when its tags confirm those artist/title values. If the chosen keeper lacks them and the group's usable tags agree, the plan proposes `Artist - Title.mp3`. Windows-invalid punctuation is replaced, invalid or overlong names rejected, and existing paths protected with a numbered suffix. Conflicting artist/title pairs suppress automatic filename completion and are noted in the plan. Names are completed only for duplicate-group keepers; this is not a general library renamer. Internal tags are not merged or rewritten by deduplication.

## Audit and recovery

The console shows a grouped KEEP/QUARANTINE list and proposed keeper names. A UTF-8-with-BOM `<report-stem>.duplicates-<run-id>.csv` records every duplicate's original path, proposed keeper, quarantine destination, hash, operation status, and rename status. It is written before approval and updated after each operation. The regular report contains surviving files after an accepted run; a dry run reports all files.

Extras move to `<library>\.mp3-manager-duplicates\<run-id>\<original-relative-path>`. Their complete bytes and tags are preserved. Quarantine and tag backups are excluded from subsequent scans, including `--include-sorted`. Quarantine does **not** free disk space; nothing is permanently deleted by this feature.

To recover a file, locate its `quarantine_path` in the audit and move it back to `duplicate_original` using File Explorer. Check that the destination is free; never overwrite an existing file. Keeper renames are also recorded and can be reversed if their original paths are free. If bitrate segregation followed deduplication, use the regular report for the keeper's final destination. Review retained songs before manually removing any quarantine directory.

Avoid simultaneous library edits during processing. The program rehashes planned groups after approval, checks file fingerprints before each operation, and refuses target overwrites. It cannot make a multi-file run transactional against other programs changing the library. On interruption, keep the audit and quarantine together to inspect or restore completed operations.
