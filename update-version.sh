#!/bin/sh

set -e

VERSION_FILE="VERSION"

# Ensure Git repo exists
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Not a Git repository. Skipping version update."
    exit 0
fi

# Read the existing `VERSION` file
if [ ! -f "$VERSION_FILE" ]; then
    echo "VERSION file not found! Skipping update."
    exit 0
fi

# Extract manually set version numbers (preserving them)
MAJOR=$(awk -F' = ' '/major/ {print $2}' "$VERSION_FILE")
MINOR=$(awk -F' = ' '/minor/ {print $2}' "$VERSION_FILE")
PATCH=$(awk -F' = ' '/patch/ {print $2}' "$VERSION_FILE")
SUFFIX=$(awk -F' = ' '/suffix/ {print $2}' "$VERSION_FILE")

# Set default values if empty
MAJOR=${MAJOR:-0}
MINOR=${MINOR:-0}
PATCH=${PATCH:-0}
SUFFIX=${SUFFIX:-""}

# Parse optional arguments
while [ $# -gt 0 ]; do
    case "$1" in
    --set-major=*)
        MAJOR="${1#*=}"
        MINOR=0 # Reset minor & patch when major is set
        PATCH=0
        ;;
    --set-minor=*)
        MINOR="${1#*=}"
        PATCH=0 # Reset patch when minor is set
        ;;
    --set-patch=*)
        PATCH="${1#*=}"
        ;;
    --set-suffix=*)
        SUFFIX="${1#*=}"
        ;;
    --increment-major)
        MAJOR=$((MAJOR + 1))
        MINOR=0 # Reset minor & patch
        PATCH=0
        ;;
    --increment-minor)
        MINOR=$((MINOR + 1))
        PATCH=0 # Reset patch
        ;;
    --increment-patch)
        PATCH=$((PATCH + 1))
        ;;
    *)
        echo "Unknown option: $1"
        echo "Usage: $0 [--set-major=N] [--set-minor=N] [--set-patch=N] [--set-suffix=STRING] [--increment-major] [--increment-minor] [--increment-patch]"
        exit 1
        ;;
    esac
    shift
done

# Get Git metadata
GIT_COMMIT=$(git rev-parse HEAD)
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
GIT_TAG=$(git describe --tags --exact-match 2>/dev/null || echo "")
GIT_DIRTY=$(git diff --quiet || echo "true")

# Ensure empty values are preserved
[ -z "$GIT_TAG" ] && GIT_TAG=""
[ -z "$GIT_DIRTY" ] && GIT_DIRTY="false"

# Write updated `VERSION` file
cat >"$VERSION_FILE" <<EOF
[version]
major = $MAJOR
minor = $MINOR
patch = $PATCH
suffix = $SUFFIX

[git]
commit = $GIT_COMMIT
branch = $GIT_BRANCH
tag = $GIT_TAG
dirty = $GIT_DIRTY
EOF

echo "✅ Updated $VERSION_FILE with latest Git information."

# Auto-stage the updated `VERSION` file for commit if needed
if git diff --quiet "$VERSION_FILE"; then
    echo "ℹ️ No changes detected in $VERSION_FILE."
else
    git add "$VERSION_FILE"
    echo "📌 Staged updated $VERSION_FILE for commit."
fi
