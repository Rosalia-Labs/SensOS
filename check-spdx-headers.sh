#!/usr/bin/env bash
set -euo pipefail

# check-spdx-headers.sh
# Check (default) or add SPDX + copyright headers if missing.
#
# Usage:
#   ./check-spdx-headers.sh
#   ./check-spdx-headers.sh --add-if-missing
#   ./check-spdx-headers.sh --add-if-missing --license MIT --owner "Rosalia Labs LLC" --year 2025

LICENSE_ID="MIT"
OWNER="Rosalia Labs LLC"
YEAR="$(date +%Y)"
DO_ADD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --add-if-missing) DO_ADD=1; shift ;;
    --license) LICENSE_ID="${2:?}"; shift 2 ;;
    --owner) OWNER="${2:?}"; shift 2 ;;
    --year) YEAR="${2:?}"; shift 2 ;;
    -h|--help) sed -n '1,60p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v git >/dev/null || { echo "error: git not found" >&2; exit 2; }

# FD 3 will carry a NUL-delimited list of tracked files excluding pi-gen/
# (Respects .gitignore automatically because we use git ls-files)
python3 - "$DO_ADD" "$LICENSE_ID" "$OWNER" "$YEAR" 3< <(git ls-files -z | grep -zv '^pi-gen/') <<'PY'
import sys, os, stat, re, pathlib

DO_ADD  = int(sys.argv[1])
LICENSE = sys.argv[2]
OWNER   = sys.argv[3]
YEAR    = sys.argv[4]

SPDX   = f"# SPDX-License-Identifier: {LICENSE}\n"
COPY   = f"# Copyright (c) {YEAR} {OWNER}\n"
HEADER = SPDX + COPY

spdx_re = re.compile(r"SPDX-License-Identifier\s*:\s*", re.IGNORECASE)
enc_re  = re.compile(r"^#.*coding[:=]\s*[-\w.]+", re.IGNORECASE)

def is_target_file(path: pathlib.Path, text: str) -> bool:
    ext = path.suffix.lower()
    if ext in {".py", ".sh"}:
        return True
    try:
        st = path.stat()
        if bool(st.st_mode & stat.S_IXUSR):
            return True
    except Exception:
        pass
    return text.startswith("#!")

def insert_header(text: str) -> str:
    lines = text.splitlines(keepends=True)
    i = 0
    if lines and lines[0].startswith("#!"):
        i = 1
    if i < len(lines) and enc_re.match(lines[i] if lines[i] else ""):
        i += 1
    insert = HEADER
    if i < len(lines) and lines[i].strip():
        insert += "\n"
    return "".join(lines[:i] + [insert] + lines[i:])

# Read NUL-delimited file list from FD 3
with os.fdopen(3, 'rb') as f:
    raw_list = f.read().split(b'\0')

missing = []
for raw in raw_list:
    if not raw:
        continue
    p = pathlib.Path(raw.decode())
    if not p.is_file():
        continue
    try:
        b = p.read_bytes()
    except Exception:
        continue
    if b"\x00" in b[:1024]:   # skip obvious binaries
        continue
    try:
        text = b.decode("utf-8")
    except UnicodeDecodeError:
        text = b.decode("utf-8", "ignore")

    if not is_target_file(p, text):
        continue
    if spdx_re.search(text):
        continue

    if DO_ADD:
        new = insert_header(text)
        if new != text:
            p.write_text(new)
            print(f"added header: {p}")
    else:
        missing.append(str(p))

if not DO_ADD:
    if missing:
        print("Missing SPDX header in these files:")
        for m in missing:
            print(f"  {m}")
        sys.exit(1)
    else:
        print("All checked files have SPDX headers.")
PY
