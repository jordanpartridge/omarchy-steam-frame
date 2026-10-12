#!/bin/sh
# Remove the installed plugin directory. Config and window state are left in place.
set -eu

dest=${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/jordan.steam-frame
rm -rf "$dest"
printf '%s\n' "Removed ${dest}"
printf '%s\n' "If the bar still lists this plugin, remove jordan.steam-frame from the Omarchy shell config."
printf '%s\n' "Left in place: ~/.config/steam-frame/ and ~/.local/state/jordan.steam-frame/"
