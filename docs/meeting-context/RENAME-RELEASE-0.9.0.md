# Validate and release meeting-context 0.9.0

The module is `dev_tools.meeting_context`; the executable command is
`meeting-context`. The existing Python modules use relative imports, so those
imports need no rename. Test imports/mock targets, package data, console entry
point, CI commands, README examples and external launchers must use the new name.
The historical docs directory remains `docs/meeting-audio` to preserve links.
The old portable delivery is a separate snapshot; it is not updated by this rename.

## Findings and changes

- Updated stale CI paths and documentation examples.
- Module `__version__` is already 0.9.0 in the user's renamed source.
- Package distribution metadata remains 1.7.0 and existing repository tags reach
  v2.1.3. These are different version scopes. Do not downgrade the collection's
  version to 0.9.0 for one module. Recommended tag: meeting-context-v0.9.0.
- Git queries in project_context now resolve the executable from PATH to an
  absolute path, allow only three fixed queries, use shell=False and a 15-second
  timeout, and handle query failures. PATH/the installed Git must still be trusted.
- Bandit's broad B404 import and B603 shell-free subprocess findings are explicitly
  reviewed with two narrow nosec annotations and explanations. B607 partial-path
  calls are eliminated. This is not a claim of zero security risk or a full audit.
- Four regression tests cover argument construction, rejected commands, missing
  Git and timeouts. Existing analysis.py/diagram.py edits were preserved.

## 1. Activate and verify the intended environment

Run these commands one at a time in PowerShell; stop on failure.

```powershell
Set-Location C:\Users\patan\Scripts\dev-tools
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable); print(sys.version)"
git status --short
git remote -v
```

Expected origin is https://github.com/patan4ik/dev-tools-collection.git. Inspect
unrelated changes rather than discarding them. Refresh editable package metadata
after changing the console entry point:

```powershell
python -m pip install --no-deps --no-build-isolation -e .
if ($LASTEXITCODE -ne 0) { throw 'Editable install failed' }
python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
Get-Command meeting-context
meeting-context --help
python -m dev_tools.meeting_context --help
python -c "from dev_tools.meeting_context import __version__; print(__version__)"
```

The editable install requires setuptools in the venv. If missing, install the
approved build dependency using `python -m pip install 'setuptools>=68'`, then retry.
Use the module command if diagnosing stale console launchers. No model redownload
is needed because of the rename. Check your own PowerShell scripts separately:
replace old CLI/module calls; do not rename recording/model contents blindly.

## 2. Mypy: match the target to the environment being checked

The config targets Python 3.11, while the reported NumPy stub uses `type` syntax
requiring Python 3.12+. Installing numpy again cannot fix this target mismatch.
For this Python 3.13 environment run:

```powershell
python -m mypy --python-version 3.13 src tests
if ($LASTEXITCODE -ne 0) { throw 'Mypy failed' }
```

This command passed against the current staged source using the user's venv.
Keep the project minimum >=3.11 and Black/Ruff target py311 unless intentionally
dropping that support. A 3.13 type check does not establish 3.11 compatibility;
that requires a real 3.11 environment with compatible installed dependencies.
CI retains the existing 3.11/3.12/3.13 test matrix. Do not edit NumPy's stubs or
disable all import checking to hide this error.

Reference: https://mypy.readthedocs.io/en/stable/config_file.html#confval-python_version

## 3. Full local checks

```powershell
python -m black --check .
if ($LASTEXITCODE -ne 0) { throw 'Black failed' }
python -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff failed' }
python -m mypy --python-version 3.13 src tests
if ($LASTEXITCODE -ne 0) { throw 'Mypy failed' }
python -m bandit -r src
if ($LASTEXITCODE -ne 0) { throw 'Bandit failed' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
git diff --check
```

The nosec annotations are intentional, limited to the reviewed subprocess
boundary. For an audit of these accepted findings use `python -m bandit -r src
--ignore-nosec`; that audit is expected to report B404/B603. Do not globally skip
these checks. Source: https://bandit.readthedocs.io/en/latest/plugins/b603_subprocess_without_shell_equals_true.html

Check for stale identifiers with Git (untracked files need separate inspection):

```powershell
git grep -n -e meeting_audio -e meeting-audio -- src tests .github pyproject.toml README.md
```

Exit 1 means no matches, not a failed test. A retained docs/meeting-audio URL is
valid and not a Python import or executable. Include untracked source with an
editor search, or `rg` if installed. Historical changelog references may be valid.

## 4. Retest the renamed CLI on a short call

With participant agreement, use the actual Teams headset/output and mic. Teams
mute does not mute this tool's independent microphone recording.

```powershell
meeting-context devices
$loopbackIndex = Read-Host 'LOOPBACK index matching Teams output'
$micIndex = Read-Host 'INPUT index matching your mic'
$trial = 'meetings\rename-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
meeting-context record --out $trial --loopback $loopbackIndex --mic $micIndex --seconds 120 --chunk-seconds 30
if ($LASTEXITCODE -ne 0) { throw 'Recording failed' }
Get-Content (Join-Path $trial 'recording.json')
```

Inspect warnings and listen to both sources before proceeding.

```powershell
meeting-context transcribe $trial --model models\small.en --out ($trial + '-text')
if ($LASTEXITCODE -ne 0) { throw 'Transcription failed' }
meeting-context review --transcript (Join-Path ($trial + '-text') 'transcript.json') --audio-dir $trial --out ($trial + '-review')
if ($LASTEXITCODE -ne 0) { throw 'Review failed' }
Invoke-Item (Join-Path ($trial + '-review') 'index.html')
meeting-context init-context --out ($trial + '-context.txt')
notepad ($trial + '-context.txt')
```

Save known context and documented corrections before continuing:

```powershell
meeting-context bundle --transcript (Join-Path ($trial + '-text') 'transcript.txt') --context ($trial + '-context.txt') --out ($trial + '-llm')
if ($LASTEXITCODE -ne 0) { throw 'Bundle failed' }
meeting-context verify-bundle ($trial + '-llm')
if ($LASTEXITCODE -ne 0) { throw 'Bundle verification failed' }
```

Check both speakers, beginning/end, numbers and negations. Source labels do not
identify individual remote people. Existing transcription resume metadata includes
the tool version, so old jobs may reject resume after version 0.2.0 -> 0.9.0;
use a fresh output directory instead of changing stored identity records.

## 5. Review, commit and push only after acceptance

No commit, tag or push is performed by this guide. The existing untracked
tests/test_project_context.py is a legacy suite and is not automatically included
in the commit list below. Decide separately whether to keep that duplicate suite.
Do not stage audio, transcripts, models, minutes, venvs or local output.

```powershell
git fetch origin --tags
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed' }
git switch -c codex/meeting-context-0.9.0
if ($LASTEXITCODE -ne 0) { throw 'Branch creation failed; inspect existing branches' }
git diff -- src/dev_tools/project_context/analysis.py src/dev_tools/project_context/diagram.py
git diff --stat
git status --short
git check-ignore meetings models
git add -- pyproject.toml README.md CHANGELOG.md .gitignore .github/workflows/tests.yml
git add -- src/dev_tools/meeting_context
git add -- src/dev_tools/project_context/collectors.py
git add -- tests/test_meeting_context_integration.py tests/test_meeting_context_reliability.py tests/test_meeting_context_workflow.py tests/test_git_queries.py
git add -- docs/meeting-audio
```

Only after reviewing the existing analysis/diagram fixes and deciding they belong
in this release:

```powershell
git add -- src/dev_tools/project_context/analysis.py src/dev_tools/project_context/diagram.py
git diff --cached --check
git diff --cached --stat
git diff --cached
git commit -m "Add meeting-context 0.9.0 and harden project-context Git queries"
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
git push -u origin codex/meeting-context-0.9.0
if ($LASTEXITCODE -ne 0) { throw 'Branch push failed' }
```

Open a pull request into main using GitHub. Wait for CI and review; merge only
after they pass. The CI is configured for PRs into main, not feature-branch pushes
alone. Then tag the merged main commit (not an unmerged or squash-replaced commit):

```powershell
git switch main
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw 'Main update failed; inspect divergence' }
git log -1 --oneline
# Confirm this is the intended merged release commit before proceeding.
git tag -a meeting-context-v0.9.0 -m "Meeting context 0.9.0"
if ($LASTEXITCODE -ne 0) { throw 'Tag failed; do not overwrite an existing tag' }
git push origin refs/tags/meeting-context-v0.9.0
if ($LASTEXITCODE -ne 0) { throw 'Tag push failed' }
git ls-remote --tags origin 'refs/tags/meeting-context-v0.9.0*'
```

If you explicitly want the exact tag `0.9.0`, substitute it for
`meeting-context-v0.9.0` in all tag commands. The component-scoped tag avoids
confusion with existing v2.x collection history. Neither tag matches the current
v* binary-release workflow, which builds project-context only. A meeting-context
tag is a source release marker; it does not publish a meeting-context executable.
