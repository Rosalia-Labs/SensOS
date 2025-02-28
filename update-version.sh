#!/bin/sh

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

# Get Git metadata
GIT_COMMIT=$(git rev-parse HEAD)
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
GIT_TAG=$(git describe --tags --exact-match 2>/dev/null || echo "")
GIT_DIRTY=$(git diff --quiet || echo "true")

# Ensure empty values are preserved
[ -z "$GIT_TAG" ] && GIT_TAG=""
[ -z "$GIT_DIRTY" ] && GIT_DIRTY="false"

# Write updated `VERSION` file
cat > "$VERSION_FILE" <<EOF
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

