"""
benchmark.py

--report mode: runs full/tree-only/signatures-only/graph (and grep,
analysis, if set) against the same root and prints a comparison table.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .analysis import run_analysis_mode
from .baseline import (
    collect_mandatory_baseline,
    select_reference_source_file,
    select_reference_test_file,
)
from .collectors import collect_files
from .config import Config
from .conventions import detect_conventions
from .render import render, render_graph


def run_benchmark(cfg: Config) -> list[dict]:
    try:
        import tiktoken
    except ImportError:
        import sys

        print("--report requires tiktoken. Install with: pip install tiktoken", file=sys.stderr)
        sys.exit(1)

    enc = tiktoken.get_encoding("cl100k_base")
    rows = []

    def measure(label: str, text_or_texts) -> dict:
        if isinstance(text_or_texts, dict):
            chars = sum(len(t) for t in text_or_texts.values())
            tokens = sum(len(enc.encode(t)) for t in text_or_texts.values())
        else:
            chars, tokens = len(text_or_texts), len(enc.encode(text_or_texts))
        return {"mode": label, "characters": chars, "tokens": tokens}

    base_cfg = replace(cfg, tree_only=False, signatures_only=False, graph=False, grep_pattern=None)
    conventions = None if cfg.no_conventions else detect_conventions(base_cfg)
    baseline = None if cfg.no_baseline else collect_mandatory_baseline(base_cfg)
    reference_test = None if cfg.no_baseline else select_reference_test_file(base_cfg)
    reference_source = None if cfg.no_baseline else select_reference_source_file(base_cfg)

    full_files = collect_files(base_cfg)
    rows.append(
        measure(
            "full",
            render(full_files, base_cfg, conventions, baseline, reference_test, reference_source),
        )
    )

    tree_cfg = replace(base_cfg, tree_only=True)
    tree_files = collect_files(tree_cfg)
    rows.append(
        measure(
            "tree-only",
            render(tree_files, tree_cfg, conventions, baseline, reference_test, reference_source),
        )
    )

    sig_cfg = replace(base_cfg, signatures_only=True)
    sig_files = collect_files(sig_cfg)
    rows.append(
        measure(
            "signatures-only",
            render(sig_files, sig_cfg, conventions, baseline, reference_test, reference_source),
        )
    )

    if cfg.grep_pattern:
        grep_cfg = replace(base_cfg, grep_pattern=cfg.grep_pattern)
        grep_files = collect_files(grep_cfg)
        rows.append(
            measure(
                f"grep:{cfg.grep_pattern}",
                render(
                    grep_files, grep_cfg, conventions, baseline, reference_test, reference_source
                ),
            )
        )

    graph_cfg = replace(base_cfg, graph=True)
    graph_files = collect_files(graph_cfg)
    rows.append(
        measure(
            "graph",
            render_graph(
                graph_files, graph_cfg, conventions, baseline, reference_test, reference_source
            ),
        )
    )

    if cfg.analysis_target:
        analysis_cfg = replace(base_cfg, analysis_target=cfg.analysis_target)
        analysis_text = run_analysis_mode(analysis_cfg, write_files=False)
        rows.append(measure(f"analysis:{Path(cfg.analysis_target).resolve().name}", analysis_text))

    baseline_tokens = rows[0]["tokens"]
    for row in rows:
        row["reduction_pct"] = round(100 * (1 - row["tokens"] / baseline_tokens), 1)
        row["multiplier"] = round(baseline_tokens / row["tokens"], 1) if row["tokens"] else 0.0

    return rows


def print_benchmark_table(rows: list[dict]) -> None:
    print(f"{'Mode':<24} {'Chars':>10} {'Tokens':>10} {'Reduction':>10} {'Smaller':>10}")
    print("-" * 66)
    for row in rows:
        print(
            f"{row['mode']:<24} {row['characters']:>10} {row['tokens']:>10} {row['reduction_pct']:>9}% {row['multiplier']:>9}x"
        )
