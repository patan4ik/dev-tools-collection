# Batch filename and tag repair

`Àâàíñ` is an example of Windows-1251 Cyrillic bytes previously interpreted as
Western text: reversing that interpretation produces `Аванс`. UTF-8 bytes
misinterpreted as Latin-1/Windows-1252 can produce `Ð...`/`Ñ...` text. The original
byte values must still be recoverable. Text copied from a terminal may omit control
characters that remain in the actual on-disk name; analysis uses the on-disk name.

Windows filenames are Unicode already. There is no UTF-8 flag to attach to an MP3
filename, and audio data must not be rewritten as text. Filename repair changes
the Unicode name. Tag repair changes ID3 text frames. CSV encoding is separate.
See [Microsoft filename documentation](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file).

## Preview first

From the activated repository environment:

```powershell
$music = 'C:\Users\patan\Music\My Music\iPhone'
python -m dev_tools.mp3_manager.cli $music --dry-run --skip-loudness --fix-filenames --fix-tags --report "$music\Reports\name-preview.csv"
```

This avoids loudness decoding, prints original/proposed values in a table, and
writes a UTF-8-BOM audit named `name-preview.name-fixes-<unique-id>.csv` beside the
main report. The audit contains relative path, field, original value, proposed
value, status, reason, and backup location. Dry-run never changes MP3 names or tags,
even if `--accept-name-fixes` is also supplied. Omit `--skip-loudness` if you need
LUFS in this report; the existing analysis cache can still be used.

## Apply the reviewed batch in place

```powershell
python -m dev_tools.mp3_manager.cli $music --skip-loudness --fix-filenames --fix-tags --no-move --report "$music\Reports\name-repair.csv"
```

The tool recomputes the preview and asks one `Apply ... changes? [y/N]` question for
the whole proposed batch. Only `y` or `yes` accepts. Any other answer declines the
repairs, and analysis/reporting continues. Without `--no-move`, the normal bitrate
segregation also continues after either acceptance or rejection. Use `--no-move`
when you only want name/tag repairs. Invalid or ambiguous entries remain unchanged.

Already segregated files are normally excluded. Add `--include-sorted --no-move`
to inspect and repair files in the bitrate folders too. Tag backups are always
excluded from scanning.

Noninteractive runs decline repairs by default. After reviewing proposals,
`--accept-name-fixes` explicitly permits them without a prompt; it requires at
least one repair flag. It does not mean "accept every possible guess": unresolved
and invalid entries remain unchanged. `--quiet` does not hide the repair preview.

## What is changed

- `--fix-filenames`: proposes reversible Cyrillic repairs to the filename stem,
  preserving the MP3 extension. Existing Cyrillic, ordinary Latin words, and
  transliteration such as `ya ostanus odna` are not automatically translated.
- `--fix-tags`: applies the same detection to title (`TIT2`), artist (`TPE1`),
  album artist (`TPE2`), and album (`TALB`). Players that display embedded tags need
  those repaired too. Other fields are not automatically guessed or rewritten.
- ID3v2.3 keeps version 2.3 and uses UTF-16 for repaired frames; ID3v2.4 keeps
  version 2.4 and uses UTF-8. Corrupt legacy ID3v1 text can be upgraded to ID3v2.3
  UTF-16, explicitly shown in the preview. Legacy ID3v1 blocks are removed from
  files whose tags are repaired, avoiding an outdated second title/artist copy.
  Unsupported ID3 versions are left for review.
- Before tag edits, a full original MP3 backup is stored beneath
  `<folder>/.mp3-manager-backups/<unique-run-id>/<original-relative-path>`.
  The audit records its location before tag saving. A failed tag save attempts to
  restore that backup. No audio transcoding is performed. Backup files need disk
  space; keep them until you have checked the results on your device.
- Renames do not create full-file backups because audio bytes are unchanged. The
  audit records the original name; the main report records original relative path
  and actual destination. There is no automatic batch undo command or all-or-nothing
  transaction. Check failure statuses if a batch was interrupted.

Mutagen documents the [ID3 text encoding values](https://mutagen.readthedocs.io/en/latest/api/id3_frames.html)
and [ID3 version conversion](https://mutagen.readthedocs.io/en/latest/user/id3.html).
Device support for Cyrillic fonts and ID3 versions varies; test a copied track on
the target player before relying on the whole library. Encoding repair cannot add
missing font/language support to a player.

## Validation and limits

The heuristic requires reversible decoding and plausible Cyrillic runs. This is
evidence of a likely repair, not proof of the intended language. Preview approval
is essential for unusual legitimate names containing accented characters.
Replacement characters, damaged byte sequences, and unsupported forms are listed
for manual review; missing characters are never invented. This does not detect
every possible encoding error and does not convert transliteration into Russian.

Targets are checked for Windows forbidden/control characters, device names,
trailing spaces/dots, MP3 extension, and a 255-UTF-16-unit component limit. Existing
and planned names are compared case-insensitively. Collisions receive ` (1)`,
` (2)`, etc. **in the preview**, so the user sees the exact target before approval.
If that destination appears after approval, the rename fails rather than
overwriting it. On POSIX, no-overwrite rename uses a hard link and fails safely
if the filesystem cannot support it. Filesystem/path-length errors are reported.
Do not run concurrent library mutators while applying repairs.

Renames and tag changes invalidate affected stat-based cache entries; those files
may require a fresh analysis on the next loudness run. Other entries remain usable.
Only files with readable MP3 metadata enter the repair stage; metadata failures
are still warned about and skipped by the scanner.

## Reports and text encoding

Main reports now default to UTF-8 with a BOM (`utf-8-sig`), which helps Excel detect
the encoding. Use `--csv-encoding utf-8` for consumers requiring BOM-free UTF-8;
Python readers can use `encoding='utf-8-sig'` with either format. Audit reports
always use UTF-8 with BOM. Encoding a corrupted string as UTF-8 alone does not
repair its characters. [Microsoft's Excel guidance](https://support.microsoft.com/en-us/excel/opening-csv-utf-8-files-correctly-in-excel)
explains the BOM behavior.

When issues are reviewed, the main report adds `original_relative_path` and
`name_repair_status`. On an applied rename, `file_name` and `relative_path` show the
actual corrected name. On a dry run or rejection, those columns retain the actual
unchanged name; proposed values live in the separate audit. Inspect that audit
for `unresolved`, `blocked`, or `failed` entries. Operational repair failures return
exit code 1; unresolved/declined/dry-run proposals alone do not make analysis fail.

## Local validation

A read-only preview of 904 library files proposed 61 filename and 177 tag-field
repairs, including five collision-safe numbered filenames. It completed in 1.4
seconds using the existing loudness cache. No original library filenames/tags were
changed. Four copied real MP3s were then repaired successfully; SHA-256 checks of
their non-ID3 payloads matched before/after, and original library file hashes stayed
unchanged. Unit tests cover rejection, dry-run, collisions, Unicode reports, legacy
ID3 upgrade, version/cover-art preservation, and restoration after a tag-save failure.
