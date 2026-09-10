#!/usr/bin/env sh
# Print the CHANGELOG.md section for one version, for the GitHub release body.
#   .github/scripts/release_notes.sh 0.6.1 [CHANGELOG.md]
# Exits 1 with nothing on stdout when the version has no section, so the
# caller can fall back to GitHub's generated notes.
set -eu
version="$1"
changelog="${2:-CHANGELOG.md}"
notes="$(awk -v v="$version" '
  /^## \[/ { if (found) exit; found = ($0 ~ ("^## \\[" v "\\]")); next }
  found { print }
' "$changelog")"
# Trim leading and trailing blank lines.
notes="$(printf '%s\n' "$notes" | awk 'NF { started = 1 } started { print }' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')"
[ -n "$notes" ] || exit 1
printf '%s\n' "$notes"
