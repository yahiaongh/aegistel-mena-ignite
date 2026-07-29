#!/usr/bin/env bash

# Usage:
#   ./merge_files.sh <source_directory> <destination_file>

set -euo pipefail

SRC_DIR="${1:-.}"
DEST_FILE="${2:-output.txt}"

# Truncate destination file
: > "$DEST_FILE"

find "$SRC_DIR" -type f | while IFS= read -r file; do
    rel_path="${file#"$SRC_DIR"/}"

    {
        echo "===== $rel_path ====="
        cat "$file"
        echo
        echo
    } >> "$DEST_FILE"
done
