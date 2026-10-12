#!/bin/sh
# Copy this checkout into the Omarchy plugin directory. Skips .git and bytecode.
# Replacing the directory is idempotent: a second run ends on the same tree.
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
dest=${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/jordan.steam-frame
parent=$(dirname "$dest")
stage=$(mktemp -d)
payload=$(mktemp)
newdest=${dest}.new
backup=${dest}.bak

cleanup() {
    rm -rf "$stage"
    rm -f "$payload"
    if [ -d "$newdest" ]; then
        rm -rf "$newdest"
    fi
}
trap cleanup EXIT

tar -C "$root" \
    --exclude=.git \
    --exclude=__pycache__ \
    --exclude='*.pyc' \
    -cf "$payload" .
tar -C "$stage" -xf "$payload"
rm -f "$payload"
test -f "$stage/manifest.json"

mkdir -p "$parent"
rm -rf "$newdest" "$backup"
mv "$stage" "$newdest"
if [ -d "$dest" ]; then
    mv "$dest" "$backup"
fi
if mv "$newdest" "$dest"; then
    rm -rf "$backup"
else
    status=$?
    if [ -d "$backup" ]; then
        mv "$backup" "$dest"
    fi
    exit "$status"
fi

trap - EXIT
