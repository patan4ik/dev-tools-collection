# Contributing to Dev Tools Collection

Thanks for your interest in contributing! This repo collects small, standalone
developer CLI tools. Here's how to get started.

## Development Setup

1. Clone and set up:
```bash
git clone https://github.com/patan4ik/dev-tools-collection.git
cd dev-tools-collection
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[report]"
```

2. Run tests:
```bash
pytest -v
```

## Project Structure
- src/dev_tools/project_context/cli.py — LLM-ready project context generator
- tests/ — unit tests for all tools

## Adding a new tool
- Create a new subpackage under src/dev_tools/<tool_name>/
- Register its entry point in pyproject.toml under [project.scripts]
- Add tests under tests/
- Document it in README.md and this file's Project Structure section

## Changelog & Versioning
Update CHANGELOG.md under the appropriate version heading for any feature or fix.

## License
By contributing, you agree that your code will be licensed under the MIT License.