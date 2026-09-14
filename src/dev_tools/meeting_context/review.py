"""Generate a script-free local HTML view; transcript text is always escaped."""

import shutil
from argparse import Namespace
from html import escape
from urllib.parse import quote

from .files import (
    AUDIO_SUFFIXES,
    atomic_text,
    child_file,
    finite_number,
    read_json,
    sha256_file,
    timestamp,
)


def review(args: Namespace) -> None:
    data = read_json(args.transcript)
    if data.get("schema") != 2 or data.get("status") != "complete":
        raise ValueError("Review requires a complete schema-2 transcript.json")
    sources = data.get("sources")
    rows = data.get("segments")
    if not isinstance(sources, list) or not isinstance(rows, list):
        raise ValueError("Invalid transcript structure")
    files = {}
    for source in sources:
        path = child_file(args.audio_dir, source["file"])
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            raise ValueError("Review links may only reference supported audio/media files")
        if path.name in files:
            raise ValueError("Duplicate source in transcript")
        if sha256_file(path) != source["sha256"]:
            raise ValueError(f"Audio hash mismatch: {path.name}")
        files[path.name] = path
    content = []
    for row in rows:
        if row["source"] not in files:
            raise ValueError("Transcript references missing source audio")
        start = finite_number(row["source_start"], "source_start")
        end = finite_number(row["source_end"], "source_end", start)
        finite_number(row["start"], "start")
        finite_number(row["end"], "end", row["start"])
        if any(not isinstance(row[key], str) for key in ("id", "track", "text")):
            raise ValueError("Invalid transcript text")
        url = f"audio/{quote(row['source'], safe='')}#t={start:.3f},{end:.3f}"
        label = f"{row['id']} | {timestamp(row['start'])}–{timestamp(row['end'])} | {row['track']}"
        content.append(
            f'<article><a href="{escape(url, quote=True)}">{escape(label)}</a>'
            f'<p>{escape(row["text"])}</p></article>'
        )
    controls = "\n".join(
        f'<section><h2>{escape(name)}</h2><audio controls preload="none" '
        f'src="audio/{quote(name, safe="")}"></audio></section>'
        for name in files
    )
    warnings = "\n".join(f"<li>{escape(str(w))}</li>" for w in data.get("warnings", []))
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "media-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        "<title>Transcript evidence review</title><style>"
        "body{font:17px system-ui;max-width:960px;margin:40px auto;padding:0 24px;color:#172b3a;background:#f7f9fc}"
        "article,section{background:white;border:1px solid #dce3eb;padding:16px;margin:12px 0;border-radius:8px}"
        "a{color:#0059a3}p{white-space:pre-wrap}audio{width:100%}</style></head><body>"
        "<h1>Transcript evidence review</h1><p>Unverified ASR. Source labels are not speaker identities. "
        "Click a segment to open its audio at the source timestamp. Browser support for media fragments "
        "and formats varies; the full source controls below remain available.</p>"
        f'<p>Task: {escape(str(data.get("settings", {}).get("task", "unknown")))}</p>'
        f"<ul>{warnings}</ul>{controls}<h2>Recognized segments</h2>"
        + "\n".join(content)
        + "</body></html>"
    )
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "audio").mkdir()
    for name, path in files.items():
        shutil.copyfile(path, args.out / "audio" / name)
        if sha256_file(args.out / "audio" / name) != sha256_file(path):
            raise ValueError("Audio changed while copying; do not use this review package")
    atomic_text(args.out / "index.html", html)
    print(f"Offline review saved to {args.out / 'index.html'}. Audio copies increase disk usage.")
