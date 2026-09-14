# Windows 11: save, validate, and test meeting audio before committing

This procedure uses `C:\Users\patan\Scripts\dev-tools` and the integrated package
`dev_tools.meeting_context`. It does not commit or push anything. All example meeting
statements below are explicitly synthetic acceptance-test data.

## 1. Place the files in the existing repository

The integration prepared for this task has this layout:

```text
dev-tools/
  pyproject.toml                       # adds audio extra, CLI and template resources
  README.md                            # collection index; fixes missing declared README
  CHANGELOG.md                         # Unreleased entry
  .gitignore                          # private recording/model folders
  .github/workflows/tests.yml          # adds module type/security + Windows unit gate
  src/dev_tools/
    project_context/                  # existing tool: preserve its local edits
    meeting_context/
      __init__.py
      __main__.py
      cli.py
      files.py
      models.py
      packaging.py
      recording.py
      review.py
      transcription.py
      README.md
      templates/
        MOM_RULES.md
        meeting_context.txt
        GENERATE_MINUTES_PROMPT.md
        REVIEW_MINUTES_PROMPT.md
  tests/
    test_*.py                         # existing project_context tests unchanged
    test_meeting_context_workflow.py
    test_meeting_context_reliability.py
    test_meeting_context_integration.py
  docs/meeting-audio/
    WINDOWS11_GUIDE.md
    VALIDATION.md
```

The task workspace also contains a staged copy under
`C:\Users\patan\Documents\ChatGPT\audio_tools\integration\dev-tools` and a
hash-checked installer `integration\install.ps1`. The installer copies only the
manifest's new/changed files, refuses an unexpected destination version, backs up
existing files in the task workspace, and verifies copied bytes. It does not copy
the staged `.git`, virtual environments, build output, recordings, models, the
Speakr checkout, or existing `project_context` source. If integration has already
been applied successfully, no second copy is necessary.

For another Windows PC, copy the NEW `meeting_context` directory, three new tests,
and `docs/meeting-context` directory. Merge the reviewed changes to `pyproject.toml`,
`.gitignore`, `CHANGELOG.md`, and the test workflow against that PC's current files.
Do not overwrite shared files with an old snapshot or add a second top-level
`meeting_tools` package. Do not replace the collection's pyproject with the old
standalone audio tool pyproject. Keep your existing project-context CLI and tests.

Inspect before installing:

```powershell
Set-Location C:\Users\patan\Scripts\dev-tools
git status --short
git diff -- pyproject.toml .gitignore CHANGELOG.md .github/workflows/tests.yml
```

The local snapshot already had edits to `src/dev_tools/project_context/cli.py`
and its README. Those edits are not part of the audio integration. The package
version remains 1.7.0, despite older changelog entries using newer numbers; version
reconciliation is left to your release process.

## 2. Activate the venv and install development dependencies

```powershell
python --version
# Only create the venv if you do not already have the intended one:
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -c "import sys, platform; print(sys.executable); print(sys.version); print(platform.machine())"
python -m pip install -e ".[dev,report]"
```

Use Python 3.11+; this integration was validated locally with Python 3.13.0 x64.
Activation is optional: if your employer blocks PowerShell activation scripts,
use `.\.venv\Scripts\python.exe` instead of `python` in each command. Do not
change corporate execution policy to bypass that restriction.

`dev` installs pytest, pytest-cov, Black, Ruff, mypy and Bandit. `report` preserves
the existing tool's optional tokenizer. Unit tests do not need the `audio` extra,
microphone access, speech weights, or a local chat LLM.

## 3. Run unit tests and code checks first

```powershell
python -m pytest -q
python -m black --check .
python -m ruff check .
python -m mypy --python-version 3.13 src tests/test_meeting_context_workflow.py tests/test_meeting_context_reliability.py tests/test_meeting_context_integration.py
python -m bandit -r src/dev_tools/meeting_context
# Also inspect the whole repository:
python -m bandit -r src
```

To run only the new tool's tests:

```powershell
python -m pytest tests/test_meeting_context_workflow.py tests/test_meeting_context_reliability.py tests/test_meeting_context_integration.py -v
```

Whole-repository Bandit had seven existing low-severity findings in
`project_context/cli.py` before this change. See VALIDATION.md. The current project_context also has two mypy findings in analysis.py and diagram.py. Do not mistake those
for audio regressions or suppress all subprocess checks globally. Review them as
separate existing-tool work before declaring every repository gate clean.

If you edit code to fix a finding, use `python -m black` on those files, review
Ruff's proposed changes, and rerun the gates. Passing tests with mocked ASR is not
evidence that a real microphone/model works.

## 4. Review and install real audio dependencies

Review `[project.optional-dependencies].audio` in `pyproject.toml`, then:

```powershell
python -m pip install -e ".[audio]"
python -m pip check
python -m dev_tools.meeting_context --help
meeting-context --help
```

If the console command is not found, use `python -m dev_tools.meeting_context` in
its place; both invoke the same CLI. Installation needs approved package-index
access. The collection uses dependency ranges; the old standalone Windows 3.13
hash lock is not a lock for all collection extras. For an approved installation
record, save the resolved package list locally and review/audit it through your
company's process:

```powershell
New-Item -ItemType Directory -Force local-validation | Out-Null
python -m pip freeze | Set-Content local-validation\installed-packages.txt
```

Do not assume the earlier standalone package audit certifies this newly resolved
environment. Do not upload package inventories containing private repository URLs.

## 5. Provision the speech model

Prefer an IT-approved CTranslate2 Whisper directory copied to `models\small.en`.
Required files include `model.bin`, `config.json`, and `tokenizer.json`.
No weights are shipped, downloaded automatically by transcription, or needed by
unit tests. This speech recognizer is not a local conversational LLM.

If authorised to obtain a public model, review
[Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en)
and its model/license information. On the approved connected PC, resolve and record
the revision before provisioning:

```powershell
$modelRevision = python -c "from huggingface_hub import HfApi; print(HfApi().model_info('Systran/faster-whisper-small.en', token=False).sha)"
if ($LASTEXITCODE -ne 0) { throw 'Model revision lookup failed' }
$modelRevision = $modelRevision.Trim()
Write-Output $modelRevision
# After reviewing that exact revision:
meeting-context download-model --repo Systran/faster-whisper-small.en --revision $modelRevision --out models\small.en
```

Resolving a SHA does not itself approve a model or authenticate its publisher.
The downloader records the revision and local file hashes. To use a provisioned
directory elsewhere, copy all its files, including its manifest. Normal transcription
uses that local directory offline and fails if required files are missing.

## 6. Windows and browser preflight

1. Use a headset, choose the intended microphone/output in Teams' browser meeting
   settings, and allow microphone access for the Teams site. Confirm Windows permits
   the browser and Python to access the microphone.
2. Run `meeting-context devices`. Note the `LOOPBACK` index corresponding to the
   headset/speaker output Teams actually uses and the `INPUT` index for your mic.
   Device indices may change after reconnect/restart. Do not copy indices from
   someone else's computer.
3. Close or mute unrelated audio sources. This records the complete selected
   playback endpoint, not an isolated browser tab. Teams mute affects what Teams
   sends; it does not mute this independent Python microphone capture.
4. Check available disk space and use an approved local, non-synced recording folder.
   The recorder retains PCM WAV and the review package makes an extra audio copy.

For a 15-second preflight, play known speech through the chosen output and speak
into your mic. Enter your actual device indices interactively:

```powershell
meeting-context devices
$loopbackIndex = Read-Host 'LOOPBACK device index used by Teams'
$micIndex = Read-Host 'INPUT microphone device index'
$trial = 'meetings\preflight-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
meeting-context record --out $trial --loopback $loopbackIndex --mic $micIndex --seconds 15
Get-Content (Join-Path $trial 'recording.json')
```

Open and listen to the remote and microphone WAVs. Continue only when both expected
sources are audible and the recording status/warnings make sense. A status of
`complete` or a nonzero signal alone does not establish intelligible audio.

## 7. Short real Teams browser call, before a longer human meeting

Microsoft's [Teams troubleshooting documentation](https://support.microsoft.com/en-us/teams/meetings/my-camera-isn-t-working-in-microsoft-teams)
says the built-in **Make a test call** feature is unavailable in Teams on the web.
Use an actual short meeting for end-to-end browser testing. A solo meeting tests
your microphone only unless somebody/something actually sends remote audio.

1. Create an authorised private meeting in Teams and join through your normal work
   browser with computer audio. Join the same meeting from a second device/account
   permitted by your tenant, or ask a colleague for a short test. Guest access may
   be restricted; do not bypass it. Keep the second device in another room or use
   headsets to prevent feedback. For a no-colleague test, you can operate that second
   device yourself to supply the remote speech.
2. Read the following **synthetic test script** on the appropriate device. Separate
   turns and say the track name aloud. Repeat a marker near the end to check timing:
   - Laptop microphone: “Local microphone check. Do not deploy on Friday.”
   - Second device: “Remote audio check. The incident number is four seven two.”
   - Second device: “I propose restarting the service. No decision has been made.”
   - Laptop microphone: “The test action is to review the logs. No owner or deadline
     has been agreed.”
   - Both devices, sequentially: “End marker, local” / “End marker, remote.”
3. 7. Short real Teams browser call, before a longer human meeting
```

Open `$reviewDir\index.html` in your approved browser. Compare every scripted line
against the original WAVs, including the source timestamps. If media-fragment seeking
does not work in that browser, use the full source audio controls or a local player.
Do not upload the files to a public audio tester.

## 8. Decide whether the short test passes

These are proposed acceptance criteria for this tool, not an industry accuracy
standard. Record actual observations in ignored `local-validation`:

| Check | Proposed pass condition |
| --- | --- |
| Source capture | Both voices audible in their intended tracks; no unexplained missing sections or capture errors. |
| Key meaning | “Do not deploy,” “propose,” “no decision,” incident number, and unspecified owner/deadline retained correctly. |
| Timing | Beginning/end markers and chunk boundaries let you locate evidence accurately enough for manual review. Record observed drift rather than assuming synchronization. |
| ASR quality | All scripted key facts recoverable correctly. Any harmful change of meaning is a fail until corrected/retested. |
| Practical performance | Record actual duration, ASR elapsed time, disk usage, and whether Teams remained usable; decide whether that is acceptable for your work PC. No speed target is asserted without measurement. |
| Privacy | Only intended meeting/audio captured; files and models remain in approved ignored folders. |

If a device is silent, correct endpoint selection and retest. For recognition errors,
first check the audio, then use short terminology hints or a larger approved model
and rerun into a new output directory. A changed model/settings cannot reuse an old
checkpoint. If transcription was merely interrupted, repeat its same command/settings
with `--resume`. Do not treat better-looking text as proof without listening.

## 9. Longer call with a consenting human

After the short check passes, test a 20–30 minute non-sensitive conversation or
another duration representative of your real meetings. Those durations are a
suggested test plan, not measured capability. Use several speaker turns, a short
overlap, pauses, domain terms, a clear decision, an explicit action, a proposal that
is rejected, and deliberately unspecified fields. Keep a small reference checklist.

```powershell
$longTrial = 'meetings\teams-long-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
meeting-context record --out $longTrial --loopback $loopbackIndex --mic $micIndex
# After the call, Ctrl+C once; wait for final status.
meeting-context transcribe $longTrial --model models\small.en --out ($longTrial + '-text')
meeting-context review --transcript (Join-Path ($longTrial + '-text') 'transcript.json') --audio-dir $longTrial --out ($longTrial + '-review')
```

Listen to early, middle, and late sections plus every decision/action. Check drift,
omissions, echo duplicates, and overlap handling. The tool labels source tracks,
not individual remote participants. Do not assign speaker identities by guesswork.

Fill context and test the final manual MoM handoff only in an LLM approved for these
contents:

```powershell
meeting-context init-context --out ($longTrial + '-context.txt')
# Edit the context file, preserving unknown fields.
meeting-context bundle --transcript (Join-Path ($longTrial + '-text') 'transcript.txt') --context ($longTrial + '-context.txt') --out ($longTrial + '-llm')
meeting-context verify-bundle ($longTrial + '-llm')
```

Confirm that generated minutes preserve evidence, negations, disagreements, actual
owners/dates, and unknown fields. They remain a human-reviewed draft. No recording
or accuracy tests have been performed on your behalf by this integration step.

## 10. Review and commit only after acceptance

```powershell
python -m pytest -q
python -m black --check .
python -m ruff check .
python -m mypy --python-version 3.13 src
python -m bandit -r src/dev_tools/meeting_context
git status --short
git diff --check
git diff -- pyproject.toml .gitignore CHANGELOG.md .github/workflows/tests.yml
git ls-files -- meetings models audio-output local-validation
```

The last command must not list real meeting data/model files intended to remain
private. `.gitignore` does not untrack files committed previously. Review new files
as well: `git diff` alone does not show untracked content. Stage the intended files
explicitly rather than using `git add .`:

```powershell
git add -- pyproject.toml .gitignore CHANGELOG.md README.md .github/workflows/tests.yml src/dev_tools/meeting_context tests/test_meeting_context_workflow.py tests/test_meeting_context_reliability.py tests/test_meeting_context_integration.py docs/meeting-context
git diff --cached --stat
git diff --cached
```

Keep your earlier `project_context` edits separate unless you deliberately want
them in the same commit. Only after reviewing the staged diff and acceptance results:

```powershell
git commit -m "Add local meeting audio capture and transcription tool"
# Push your intended branch only when you are ready:
git push
```

The agent does not execute these final Git commands. The added CI jobs are proposed
configuration; actual GitHub CI results will exist only after you push.
