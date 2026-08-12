"""Allows `python -m project_context ...` in addition to the installed
`project-context` console-script entry point."""

from .cli import main

if __name__ == "__main__":
    main()
