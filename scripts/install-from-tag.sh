#!/bin/sh
# Install one tagged release into ~/.config/omarchy/plugins/jordan.steam-frame.
# The README one-liner downloads this file from that same tag.
set -eu

tag=${1:-v1.0.0}
case $tag in
    *[!A-Za-z0-9._-]*)
        echo "invalid tag" >&2
        exit 2
        ;;
esac

repo=jordanpartridge/omarchy-steam-frame
dest=${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/jordan.steam-frame
parent=$(dirname "$dest")
arc=$(mktemp -d)
stage=$(mktemp -d)
newdest=${dest}.new
backup=${dest}.bak

cleanup() {
    rm -rf "$arc" "$stage"
    if [ -d "$newdest" ]; then
        rm -rf "$newdest"
    fi
}
trap cleanup EXIT

url="https://github.com/${repo}/archive/refs/tags/${tag}.tar.gz"
curl -fsSL "$url" -o "$arc/src.tar.gz"
tar -xzf "$arc/src.tar.gz" -C "$stage" --strip-components=1
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
rm -rf "$arc"
