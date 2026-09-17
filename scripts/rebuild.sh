#!/bin/sh
# Rebuild a large data file from the chunks committed to this repository.
#
# Standalone on purpose: POSIX shell, no Python, no network, no dependency on
# the anac_data package. Copy this script next to a chunk directory and it
# still works.
#
# Usage:
#   scripts/rebuild.sh                 # rebuild every chunk set under data/dist
#   scripts/rebuild.sh <chunk-dir>     # rebuild just that one
#   scripts/rebuild.sh <chunk-dir> -o <output-dir>
#
# A chunk directory is one holding MANIFEST.json, checksums.sha256 and
# <name>.gz.partNNN files. Everything needed to verify the result travels in
# checksums.sha256, so no JSON parser is required.

set -eu

CHECKSUMS="checksums.sha256"
DEFAULT_DIST="data/dist"

die() {
    echo "rebuild: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: rebuild.sh [chunk-dir] [-o output-dir]

  chunk-dir     directory holding checksums.sha256 and *.gz.partNNN files.
                Defaults to rebuilding every chunk set under data/dist.
  -o dir        where to write the rebuilt file (default: the chunk dir's
                own location under data/raw/anac, else the chunk dir itself)
EOF
}

# ---------------------------------------------------------------------------
# Tool detection, done once. Linux ships sha256sum; macOS ships shasum.
# ---------------------------------------------------------------------------
SHA_CMD=""
SHA_CHECK=""
detect_tools() {
    if command -v sha256sum >/dev/null 2>&1; then
        SHA_CMD="sha256sum"
        SHA_CHECK="sha256sum -c"
    elif command -v shasum >/dev/null 2>&1; then
        SHA_CMD="shasum -a 256"
        SHA_CHECK="shasum -a 256 -c"
    else
        die "need sha256sum or shasum to verify the rebuild; neither was found"
    fi

    command -v gunzip >/dev/null 2>&1 \
        || die "need gunzip to decompress the chunks; it was not found"
}

# Digest of a file, just the hex.
digest_of() {
    # shellcheck disable=SC2086
    $SHA_CMD "$1" | cut -d' ' -f1
}

# Read a "# key: value" comment out of checksums.sha256.
meta() {
    sed -n "s/^# $2: *//p" "$1" | head -n 1
}

# ---------------------------------------------------------------------------
# Rebuild one chunk directory.
# ---------------------------------------------------------------------------
rebuild_one() {
    chunk_dir=$1
    output_dir=$2

    listing="$chunk_dir/$CHECKSUMS"
    [ -f "$listing" ] || die "$chunk_dir has no $CHECKSUMS - is it a chunk directory?"

    name=$(meta "$listing" "original-name")
    want=$(meta "$listing" "original-sha256")
    size=$(meta "$listing" "original-size")
    [ -n "$name" ] || die "$listing has no original-name"
    [ -n "$want" ] || die "$listing has no original-sha256"

    target="$output_dir/$name"

    # Already correct? Say so and touch nothing.
    if [ -f "$target" ]; then
        have=$(digest_of "$target")
        if [ "$have" = "$want" ]; then
            echo "$name: already correct, nothing to do"
            return 0
        fi
        echo "$name: present but does not match the recorded digest, rebuilding"
    fi

    # Every chunk must be present before we start.
    missing=""
    for chunk in $(sed -n 's/^[0-9a-f]\{64\}  //p' "$listing"); do
        [ -f "$chunk_dir/$chunk" ] || missing="$missing $chunk"
    done
    if [ -n "$missing" ]; then
        die "$name: chunk(s) missing from $chunk_dir:$missing"
    fi

    echo "$name: verifying chunks in $chunk_dir"
    # One command verifies the lot; comment lines are ignored by -c.
    # A failure prints the offending chunk's name itself.
    ( cd "$chunk_dir" && $SHA_CHECK "$CHECKSUMS" >/dev/null ) \
        || die "$name: a chunk failed verification (run '$SHA_CHECK $CHECKSUMS' in $chunk_dir to see which)"

    mkdir -p "$output_dir"
    temp="$target.rebuilding"
    # Never leave a half-written file where a finished one belongs.
    trap 'rm -f "$temp"' EXIT HUP INT TERM

    echo "$name: concatenating and decompressing"
    # Sorted names equal concatenation order - that is what the zero-padded
    # index in the chunk names is for.
    # shellcheck disable=SC2086
    cat $(ls "$chunk_dir"/*.gz.part* | sort) | gunzip -c > "$temp" \
        || die "$name: decompression failed"

    got=$(digest_of "$temp")
    if [ "$got" != "$want" ]; then
        rm -f "$temp"
        die "$name: rebuilt file does not match the recorded digest
  expected $want
  got      $got
  the chunks verified, so the recorded original digest or the chunk set itself is wrong"
    fi

    if [ -n "$size" ]; then
        actual=$(wc -c < "$temp" | tr -d ' ')
        [ "$actual" = "$size" ] \
            || die "$name: rebuilt size $actual does not match recorded $size"
    fi

    mv "$temp" "$target"
    trap - EXIT HUP INT TERM
    echo "$name: rebuilt and verified -> $target"
}

# Where should a chunk set's output go by default?
# data/dist/<dataset>/<file>/  ->  data/raw/anac/<dataset>/
default_output_for() {
    chunk_dir=$1
    dataset=$(basename "$(dirname "$chunk_dir")")
    parent=$(dirname "$(dirname "$(dirname "$chunk_dir")")")
    if [ -d "$parent/raw/anac" ]; then
        echo "$parent/raw/anac/$dataset"
    else
        echo "$chunk_dir"
    fi
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
CHUNK_DIR=""
OUTPUT_DIR=""
while [ $# -gt 0 ]; do
    case $1 in
        -o|--output-dir)
            [ $# -ge 2 ] || die "-o needs a directory"
            OUTPUT_DIR=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            die "unknown option: $1"
            ;;
        *)
            [ -z "$CHUNK_DIR" ] || die "only one chunk directory at a time"
            CHUNK_DIR=$1
            shift
            ;;
    esac
done

detect_tools

if [ -n "$CHUNK_DIR" ]; then
    [ -d "$CHUNK_DIR" ] || die "$CHUNK_DIR is not a directory"
    out=${OUTPUT_DIR:-$(default_output_for "$CHUNK_DIR")}
    rebuild_one "$CHUNK_DIR" "$out"
    exit 0
fi

# No directory given: every chunk set under data/dist.
[ -d "$DEFAULT_DIST" ] || die "$DEFAULT_DIST not found - run this from the repository root, or pass a chunk directory"

found=0
for listing in $(find "$DEFAULT_DIST" -name "$CHECKSUMS" | sort); do
    dir=$(dirname "$listing")
    out=${OUTPUT_DIR:-$(default_output_for "$dir")}
    rebuild_one "$dir" "$out"
    found=$((found + 1))
done

[ "$found" -gt 0 ] || die "no chunk sets found under $DEFAULT_DIST"
echo "rebuilt $found file(s)"
