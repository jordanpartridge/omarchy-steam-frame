#!/usr/bin/python3
"""Fail if the tree still contains desk addresses, house names, or tailnet names.

Search terms are assembled in this file so the terms themselves are not stored
as contiguous text. Matches are reported by file, line, and category. The
matching text is not printed.
"""

import os
import re
import sys


def _join(*parts):
    return "".join(parts)


PLUGIN_ID = _join("jor", "dan", ".steam-frame")
AUTHOR = _join("Jor", "dan", " Par", "tridge")
REPO = _join("jor", "dan", "par", "tridge", "/omarchy-steam-frame")
NAME_A = _join("jor", "dan")
NAME_B = _join("par", "tridge")
HOST_A = _join("th", "or")
HOST_B = _join("od", "in")
HOST_C = _join("lion-", "balance")
TAILNET = _join(".ts", ".net")

ALLOWED = (PLUGIN_ID, AUTHOR, REPO)
IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".ruff_cache", ".mypy_cache"}


def strip_allowed(text):
    cleaned = text
    for token in ALLOWED:
        cleaned = re.sub(re.escape(token), "", cleaned, flags=re.IGNORECASE)
    return cleaned


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".git")]
        for name in filenames:
            yield os.path.join(dirpath, name)


def scan(root):
    findings = []
    host_re = re.compile(
        r"\b(?:" + re.escape(HOST_A) + r"|" + re.escape(HOST_B) + r"|" + re.escape(HOST_C) + r")\b",
        re.IGNORECASE,
    )
    name_re = re.compile(re.escape(NAME_A) + r"|" + re.escape(NAME_B), re.IGNORECASE)
    tail_re = re.compile(re.escape(TAILNET), re.IGNORECASE)
    for path in iter_files(root):
        rel = os.path.relpath(path, root)
        if os.path.islink(path):
            findings.append((rel, 0, "symlink"))
            continue
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            findings.append((rel, 0, "unreadable"))
            continue
        if b"\x00" in raw:
            findings.append((rel, 0, "binary-file"))
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            text = raw.decode("latin-1")
        for number, line in enumerate(text.splitlines(), start=1):
            if IPV4_RE.search(line):
                findings.append((rel, number, "ipv4"))
            if tail_re.search(line):
                findings.append((rel, number, "tailnet"))
            if host_re.search(line):
                findings.append((rel, number, "house-name"))
            if name_re.search(strip_allowed(line)):
                findings.append((rel, number, "personal-name"))
    return findings


def main(argv):
    root = argv[1] if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings = scan(root)
    if not findings:
        print("scrub ok")
        return 0
    for rel, number, category in findings:
        print(f"scrub: {rel}:{number}: {category}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
