# Developer tools collection

Standalone Python command-line tools in a shared `src/dev_tools` package.

| Tool | Install extra | Entry point | Documentation |
| --- | --- | --- | --- |
| Project context | `report` for token counting | `project-context` | [Project context guide](src/dev_tools/project_context/README.md) |
| Meeting context | `audio` for Windows capture and local ASR | `meeting-context` | [Meeting audio guide](src/dev_tools/meeting_context/README.md) |

Install only the extras you need; the collection's base dependencies remain empty.

```powershell
python -m pip install -e ".[dev,report]"
python -m pytest
```

Meeting context needs a separate approved speech model. No local chat LLM is needed.
Follow the [Windows 11 integration and real-call test procedure](docs/meeting-audio/WINDOWS11_GUIDE.md)
before using it for real meeting minutes. See [integration validation](docs/meeting-audio/VALIDATION.md)
for the distinction between the new module's checks and existing repository findings.
