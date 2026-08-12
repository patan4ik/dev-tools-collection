#!/usr/bin/env python3
"""
cli.py

Thin CLI entry point for project-context. All actual logic lives in
sibling modules (collectors, conventions, baseline, diagram, render,
analysis, benchmark) -- this file only parses arguments and dispatches.

Version: 2.1.0

NEW IN 2.1.0: split the previous single-file cli.py (~2400 lines) into
a package of focused modules. Behavior is 100% unchanged -- pure
internal reorganization for readability/testability, not a feature
change.

Compatibility mechanism (fixed after an initial, broken attempt): sibling
modules (analysis.py, baseline.py, etc.) use RELATIVE imports
(`from .config import Config`), which only resolve correctly when Python
loads them as real submodules of a package -- never when a module is
loaded as a flat, top-level script. A naive `sys.path.insert(parent);
import analysis` fails, because analysis.py's OWN internal
`from .config import Config` still has no parent package to resolve
against when analysis.py itself is loaded as a bare top-level module.

The fix: when this file is executed directly (`python cli.py ...`), it
detects it has no package context (`__package__` is empty), inserts
this file's PARENT directory into sys.path, and dynamically re-imports
ITSELF by its real dotted path (`<package_dir_name>.cli`) via importlib.
That triggers Python's normal package-import machinery for this exact
file a SECOND time, this time WITH a correct package context, so every
sibling module's relative imports resolve normally against that second,
properly-loaded copy. The first (direct-script) load then simply
delegates to that copy's main() and exits -- no logic is duplicated
between the two code paths, and nothing runs twice.

This makes all three of these work identically:
    project-context --tree-only                        (installed console script)
    python -m dev_tools.project_context --tree-only     (module execution)
    python cli.py --tree-only                           (direct script, e.g. local testing)
"""

from __future__ import annotations

import sys
from pathlib import Path

VERSION = "2.1.0"

if __package__ in (None, ""):
    # Direct script execution: `python cli.py ...`. Re-import this same
    # file properly, as a real package submodule, so sibling modules'
    # relative imports resolve -- then delegate to it and exit.
    import importlib

    _package_dir = Path(__file__).resolve().parent
    _package_name = _package_dir.name
    _parent_dir = str(_package_dir.parent)
    if _parent_dir not in sys.path:
        sys.path.insert(0, _parent_dir)

    _cli_module = importlib.import_module(f"{_package_name}.cli")

    # Re-export the public surface for parity with the installed-package
    # import path, in case something else imports this file directly.
    main = _cli_module.main
    parse_args = _cli_module.parse_args

    if __name__ == "__main__":
        main()

else:
    # Normal case: loaded as a real package submodule -- the installed
    # console-script entry point, `python -m <package>`, or via the
    # delegation above. All relative imports resolve correctly here
    # because __package__ is set.
    import argparse
    import importlib.util

    from .analysis import run_analysis_mode
    from .baseline import (
        collect_mandatory_baseline,
        select_reference_source_file,
        select_reference_test_file,
    )
    from .benchmark import print_benchmark_table, run_benchmark
    from .collectors import collect_files, warn_if_full_dump_overload
    from .config import Config, resolve_interactive_path, running_as_frozen_exe
    from .constants import DIAGRAM_MODES, INTEGRATION_SCOPES
    from .conventions import detect_conventions
    from .render import copy_to_clipboard, render, render_graph, write_graph_output, write_output
    from .utils import estimate_tokens

    def parse_args() -> Config:
        parser = argparse.ArgumentParser(
            description="Bundles a Python project's code into a single file for LLM context."
        )
        parser.add_argument("--version", action="version", version=f"project_context.py {VERSION}")
        parser.add_argument("--root", type=str, default=".", help="Project root directory")
        parser.add_argument(
            "--output",
            type=str,
            default="project_context.md",
            help="Path to the output file (or directory for --graph). Empty/'-' for stdout",
        )
        parser.add_argument(
            "--tree-only",
            action="store_true",
            help="Output only the project tree, without file contents",
        )
        parser.add_argument(
            "--changed-only", action="store_true", help="Include only changed (git status) files"
        )
        parser.add_argument(
            "--signatures-only",
            action="store_true",
            help="Output only function/class signatures (AST) in a single file",
        )
        parser.add_argument(
            "--graph",
            action="store_true",
            help="OKF-flavored output: one markdown file per module with dependency links, plus index.md.",
        )
        parser.add_argument(
            "--grep",
            type=str,
            default=None,
            dest="grep_pattern",
            help="Include only files whose content matches a regex pattern",
        )
        parser.add_argument(
            "--max-chars",
            type=int,
            default=None,
            help="Max characters per output file, to split into parts",
        )
        parser.add_argument(
            "--format",
            type=str,
            choices=["md", "xml"],
            default="md",
            help="Output format: markdown or xml-like",
        )
        parser.add_argument(
            "--clipboard", action="store_true", help="Copy the result to the clipboard"
        )
        parser.add_argument("--no-gitignore", action="store_true", help="Ignore .gitignore rules")
        parser.add_argument(
            "--include-ext", type=str, default=None, help="Extra extensions, comma-separated"
        )
        parser.add_argument(
            "--exclude-dir",
            type=str,
            default=None,
            help="Extra directories to exclude, comma-separated",
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help="Run full/tree-only/signatures-only/graph (and grep/analysis, if set) and print a comparison table.",
        )
        parser.add_argument(
            "--integration-scope", type=str, choices=list(INTEGRATION_SCOPES), default="standalone"
        )
        parser.add_argument(
            "--diagram", type=str, choices=list(DIAGRAM_MODES), default="auto", dest="diagram_mode"
        )
        parser.add_argument("--no-conventions", action="store_true")
        parser.add_argument("--no-baseline", action="store_true")
        parser.add_argument("--no-plan-gate", action="store_true")
        parser.add_argument(
            "--analysis",
            type=str,
            default=None,
            dest="analysis_target",
            help="Path to a DIFFERENT, already-cloned local repository to analyze for tutorial-context generation.",
        )
        parser.add_argument(
            "--max-abstractions", type=int, default=10, dest="analysis_max_abstractions"
        )
        parser.add_argument(
            "--tutorial-language", type=str, default="english", dest="analysis_language"
        )
        args = parser.parse_args()

        output = None if (args.output in ("-", "", None)) else args.output

        cfg = Config(
            root=Path(args.root).resolve(),
            output=output,
            tree_only=args.tree_only,
            changed_only=args.changed_only,
            signatures_only=args.signatures_only,
            graph=args.graph,
            grep_pattern=args.grep_pattern,
            max_chars=args.max_chars,
            output_format=args.format,
            clipboard=args.clipboard,
            report=args.report,
            integration_scope=args.integration_scope,
            diagram_mode=args.diagram_mode,
            no_conventions=args.no_conventions,
            no_baseline=args.no_baseline,
            no_plan_gate=args.no_plan_gate,
            analysis_target=args.analysis_target,
            analysis_max_abstractions=args.analysis_max_abstractions,
            analysis_language=args.analysis_language,
            use_gitignore=not args.no_gitignore,
        )

        if args.include_ext:
            cfg.include_ext |= {e.strip() for e in args.include_ext.split(",") if e.strip()}
        if args.exclude_dir:
            cfg.exclude_dirs |= {d.strip() for d in args.exclude_dir.split(",") if d.strip()}
        if cfg.graph and cfg.output == "project_context.md":
            cfg.output = "project_graph"

        return cfg

    def main() -> None:
        if running_as_frozen_exe() and len(sys.argv) == 1 and sys.stdin.isatty():
            exe_dir = Path(sys.executable).resolve().parent
            chosen_path = resolve_interactive_path(exe_dir)
            sys.argv.extend(["--analysis", str(chosen_path)])

        cfg = parse_args()

        if not cfg.root.exists():
            print(f"Error: directory {cfg.root} was not found", file=sys.stderr)
            sys.exit(1)

        if cfg.report:
            if importlib.util.find_spec("tiktoken") is None:
                print(
                    "--report requires tiktoken. Install with: pip install tiktoken",
                    file=sys.stderr,
                )
                sys.exit(1)
            print_benchmark_table(run_benchmark(cfg))
            return

        if cfg.analysis_target:
            run_analysis_mode(cfg, write_files=True)
            return

        files = collect_files(cfg)
        if not files:
            print("No files matching the filters were found.", file=sys.stderr)
            sys.exit(0)

        warn_if_full_dump_overload(files, cfg)

        conventions = None if cfg.no_conventions else detect_conventions(cfg)
        baseline = None if cfg.no_baseline else collect_mandatory_baseline(cfg)
        reference_test = None if cfg.no_baseline else select_reference_test_file(cfg)
        reference_source = None if cfg.no_baseline else select_reference_source_file(cfg)

        if cfg.graph:
            graph_files = render_graph(
                files, cfg, conventions, baseline, reference_test, reference_source
            )
            out_dir = write_graph_output(graph_files, cfg)
            total_chars = sum(len(c) for c in graph_files.values())
            total_tokens, method = estimate_tokens("".join(graph_files.values()))
            print(
                f"Written to {out_dir}: {len(graph_files)} files, {total_chars} characters, ~{total_tokens} tokens total ({method}).",
                file=sys.stderr,
            )
            return

        text = render(files, cfg, conventions, baseline, reference_test, reference_source)
        written = write_output(text, cfg)
        for p in written:
            written_text = p.read_text(encoding="utf-8")
            tok_count, method = estimate_tokens(written_text)
            print(
                f"Written: {p} ({len(written_text)} characters, ~{tok_count} tokens, {method})",
                file=sys.stderr,
            )

        if cfg.clipboard:
            if copy_to_clipboard(text):
                print("Result copied to clipboard.", file=sys.stderr)

    if __name__ == "__main__":
        main()
