# FFmpeg PATH registration and validation on Windows 11

FFmpeg is a separate executable. Installing Mutagen or the Python package does not
install it. Use a build linked from the [official FFmpeg download page](https://ffmpeg.org/download.html)
(Windows links include Gyan and BtbN), and choose the build matching your machine.
Extract the archive to a stable directory you control. The examples below assume
the executable is `C:\Tools\FFmpeg\bin\ffmpeg.exe`; substitute your actual path.

## 1. Validate the executable before changing PATH

In PowerShell:

```powershell
$ffmpegDir = 'C:\Tools\FFmpeg\bin'
$ffmpegExe = Join-Path $ffmpegDir 'ffmpeg.exe'
if (-not (Test-Path -LiteralPath $ffmpegExe -PathType Leaf)) {
    throw "ffmpeg.exe was not found at $ffmpegExe"
}
& $ffmpegExe -version
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg could not start' }
& $ffmpegExe -hide_banner -filters 2>&1 | Select-String 'ebur128'
```

The version command must succeed and the filter list must contain `ebur128`.
If startup reports missing DLLs, retain the full extracted build directory or
choose a suitable standalone build. Do not download individual DLLs from random sites.

## 2. Register the bin directory

For the current PowerShell session only:

```powershell
$env:Path = "$ffmpegDir;$env:Path"
```

For persistent registration, search Windows Settings/Start for **Edit environment
variables for your account**. Under your **User variables**, select `Path`, choose
**Edit**, then **New**, and add `C:\Tools\FFmpeg\bin`. Add the directory, not the
executable; do not replace existing entries or include quotes in the entry. Confirm
the dialogs. User scope avoids changing the system-wide configuration.

Alternatively, preserve the existing user PATH with PowerShell:

```powershell
$userPathBefore = [Environment]::GetEnvironmentVariable('Path', 'User')
$userPathParts = @($userPathBefore -split ';' | Where-Object { $_ })
if ($userPathParts -notcontains $ffmpegDir) {
    $userPathAfter = (@($userPathParts) + $ffmpegDir) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $userPathAfter, 'User')
}
```

Run section 1 first so `$ffmpegDir` is set and the executable has been validated.
Avoid copying the combined process PATH into the user PATH. Persistent changes do
not update already running applications. Close and reopen your terminal host and
any IDE/Codex application that will launch the tool. If a new process still inherits
an old PATH, sign out and back in. These scope and persistence rules are documented
by [Microsoft's PowerShell environment-variable guide](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables).

## 3. Verify resolution and actual analysis

In a freshly opened PowerShell window:

```powershell
Get-Command ffmpeg -All | Select-Object CommandType, Source
where.exe ffmpeg
ffmpeg -version
ffmpeg -hide_banner -nostats -f lavfi -i 'sine=frequency=1000:duration=3' -af ebur128 -f null -
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg loudness smoke test failed' }
```

Check that the resolved executable is the build you intended. The synthetic tone
test should end with an integrated loudness summary in LUFS; it does not create an
audio file. See the [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html#ebur128-1).

Activate the repository's Python environment, then verify the same resolver used
by MP3 manager:

```powershell
python -c "import shutil; print(shutil.which('ffmpeg'))"
python -m pip install -e ".[mp3]"
python -m dev_tools.mp3_manager.cli 'C:\Music' --dry-run --report 'C:\Reports\mp3-preview.csv'
$LASTEXITCODE
```

Use a small folder of copied MP3 files initially. Expect exit code 0 and numeric
LUFS for successfully measured files. Review `analysis_status` for failures.

## Troubleshooting and bypass

| Symptom | Check |
| --- | --- |
| `ffmpeg` is not recognized / Python prints `None` | Correct `bin` directory, then restart the launching application |
| Multiple executables in `where.exe` | Inspect PATH order or select an explicit binary |
| Absolute path works but command name fails | PATH registration or inherited process environment |
| `No such filter: ebur128` | Validate another build using `-filters` |
| Missing `mutagen` | Install `.[mp3]` using the same Python that runs the command |
| FFmpeg times out | Current per-file limit is 300 seconds; analyze the file manually to diagnose |

No PATH change is necessary if you pass the executable explicitly:

```powershell
python -m dev_tools.mp3_manager.cli 'C:\Music' --dry-run --ffmpeg-bin 'C:\Tools\FFmpeg\bin\ffmpeg.exe'
```

Use a native executable, not a `.cmd` or `.bat` wrapper. To undo registration,
remove only the entry you added in the User Path editor, then reopen applications.
This document provides instructions; validation of your chosen FFmpeg build must
be performed on the Windows installation where you will run the tool.
