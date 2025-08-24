#!/usr/bin/env bash
set -euo pipefail

# check-spdx-headers.sh
# Check (default) or add SPDX + copyright headers if missing.
#
# Usage:
#   ./check-spdx-headers.sh
#   ./check-spdx-headers.sh --add-if-missing
#   ./check-spdx-headers.sh --add-if-missing --license MIT --owner "Your LLC" --year 2025
#
# Notes:
# - Operates only on files tracked by git (respects .gitignore, avoids secrets/artifacts).
# - Skips pi-gen/ (submodule).
# - Targets: *.py, *.sh, any executable text file, or any file with a shebang.
# - Preserves shebang and Python encoding lines; no duplicate insertion.

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

# Collect only tracked files (null-delimited), skip pi-gen/
mapfile -d '' FILES < <(git ls-files -z | grep -zv '^pi-gen/')

python3 - "$DO_ADD" "$LICENSE_ID" "$OWNER" "$YEAR" <<'PY'
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

missing = []
data = sys.stdin.buffer.read().split(b"\0")
for raw in data:
    if not raw: continue
    p = pathlib.Path(raw.decode())
    try:
        b = p.read_bytes()
    except Exception:
        continue
    if b"\x00" in b[:1024]:  # skip binary
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
PY <<<"$(printf '%s\0' "${FILES[@]}")"
