# Meeting audio

Record Windows playback and microphone into separate PCM WAV chunks, transcribe
locally, review timestamped evidence, and prepare an MoM rules/context/prompt package
for manual use with your approved LLM. The tool performs no automatic uploads and
requires no local chat LLM. The local speech model is a separate requirement.

## Install and invoke

From the repository root in an approved Python environment:

```powershell
python -m pip install -e ".[audio]"
meeting-context --help
# Equivalent if the console command is not on PATH:
python -m dev_tools.meeting_context --help
```

The `audio` extra declares PyAudioWPatch (Windows only), faster-whisper, and
huggingface-hub. Other tools do not acquire audio/ML dependencies unless this
extra is requested. Python 3.11+ matches the collection; Windows capture requires
compatible wheels for your exact Python version and architecture.

## Workflow

```powershell
meeting-context devices
meeting-context record --out meetings\trial --seconds 120 --chunk-seconds 30
meeting-context transcribe meetings\trial --model models\small.en --out meetings\trial-text
meeting-context review --transcript meetings\trial-text\transcript.json --audio-dir meetings\trial --out meetings\trial-review
meeting-context init-context --out meetings\trial-context.txt
# Fill the context file with known facts, then:
meeting-context bundle --transcript meetings\trial-text\transcript.txt --context meetings\trial-context.txt --out meetings\trial-llm
meeting-context verify-bundle meetings\trial-llm
```

Run from the repository root so ignored `meetings/` and `models/` folders contain
private data. Never commit recordings, transcripts, models, review audio copies,
or generated real minutes. Loopback records everything played through the selected
output, not only the Teams browser tab. Select Teams' actual devices with
`--loopback INDEX --mic INDEX`. Both default Windows devices are captured if omitted.
Teams/browser mute does not mute this independent microphone recorder.

Press Ctrl+C once to finish an indefinite recording. Sources are finalized in
five-minute WAV chunks by default; the manifest records warnings and hashes.
Disk reserve defaults to 512 MiB. Headsets reduce microphone echo; no acoustic
echo cancellation or per-person diarization is implemented.

Transcription always uses an existing local CTranslate2 model directory. For
approved online provisioning, `download-model --repo OWNER/NAME --revision SHA
--out models\NAME` requires a full 40-character reviewed model revision and
records file hashes. It reads no meeting audio. The default model location is
`models/small.en`; no model is included in the repository.

Use the same transcription command plus `--resume` after failure. Completed
sources are reused only if audio/model hashes, settings, and ASR versions match.
The interrupted source restarts from its beginning. Domain term files can be
supplied with `--hotwords` and `--initial-prompt` (each at most 2000 characters).
These are hints, not instructions to invent desired meeting content.

English speech/transcription is the default. Other languages require a multilingual
model and `--language CODE` or `auto`; `--task translate` produces explicitly
labeled English translation. English-only models reject those modes.

HTML review is offline and script-free. It escapes transcript text and links to
source-relative times in audio copies. Browser media-format/fragment support varies.
Review important names, numbers, decisions, dates, and negations against the audio.
Track offsets are approximate and can drift; ASR output is unverified.

Manually attach `MOM_RULES.md`, `transcript.txt`, and `meeting_context.txt` to an
LLM approved for these contents; use `GENERATE_MINUTES_PROMPT.md`, then audit the
draft with `REVIEW_MINUTES_PROMPT.md`. Sending text to an external service is a
separate deliberate disclosure. The prompts are packaged resources and survive
wheel installation; they are not separate top-level Python packages.

## Tests and acceptance

Unit tests use simulated audio/ASR and need no microphone, model, network, or
`audio` extra. Pytest discovers both the preserved unittest regressions and new
pytest-style integration checks. Tests are under `tests/test_meeting_context_*`.

Follow [Windows 11 acceptance instructions](../../../docs/meeting-audio/WINDOWS11_GUIDE.md)
from the repository root documentation (relative navigation in some package viewers
may differ). No real Teams or ASR accuracy result has been established yet.
