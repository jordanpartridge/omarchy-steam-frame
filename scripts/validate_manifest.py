#!/usr/bin/python3
"""Validate an Omarchy plugin manifest the way `omarchy plugin validate` does.

Mirrors the checks in Omarchy's omarchy-plugin-validate: schemaVersion 1,
required fields, a safe id, entry points that exist and stay inside the
plugin directory, one entry point per declared kind, bar section, and no
symlinks. Also requires a dotted numeric version so the install tag can
match manifest.json.
"""

import json
import os
import re
import sys

KIND_ENTRY = {
    "bar": "bar",
    "bar-widget": "barWidget",
    "menu": "menu",
    "overlay": "overlay",
    "panel": "panel",
    "service": "service",
}
SECTIONS = {"left", "center", "right"}
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ManifestError(ValueError):
    pass


def _fail(message):
    raise ManifestError(message)


def validate(plugin_dir):
    root = os.path.abspath(plugin_dir)
    if not os.path.isdir(root):
        _fail("plugin folder not found")
    manifest_path = os.path.join(root, "manifest.json")
    if not os.path.isfile(manifest_path) or os.path.islink(manifest_path):
        _fail("missing manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("manifest.json is not valid JSON: " + type(exc).__name__)
    if not isinstance(manifest, dict):
        _fail("manifest.json must be an object")
    if manifest.get("schemaVersion") != 1:
        _fail("unsupported or missing schemaVersion (expected 1)")
    for field in ("id", "name", "version", "kinds", "entryPoints"):
        if field not in manifest:
            _fail("manifest missing required field " + field)
    plugin_id = manifest["id"]
    if not isinstance(plugin_id, str) or plugin_id == "":
        _fail("manifest id is empty")
    if ID_RE.fullmatch(plugin_id) is None or ".." in plugin_id:
        _fail("invalid plugin id")
    if plugin_id.startswith("omarchy."):
        _fail("plugin id uses the reserved omarchy.* namespace")
    for field in ("name", "version"):
        if not isinstance(manifest[field], str) or manifest[field].strip() == "":
            _fail("manifest " + field + " must be a non-empty string")
    if VERSION_RE.fullmatch(manifest["version"]) is None:
        _fail("manifest version must be a dotted numeric version")
    kinds = manifest["kinds"]
    if not isinstance(kinds, list) or not kinds or not all(isinstance(kind, str) for kind in kinds):
        _fail("kinds must be a non-empty array of strings")
    entry_points = manifest["entryPoints"]
    if not isinstance(entry_points, dict) or not entry_points:
        _fail("entryPoints must be an object")
    for key, rel in entry_points.items():
        if not isinstance(key, str) or not isinstance(rel, str) or rel == "":
            _fail("entry point path is empty")
        if "\n" in rel or rel.startswith("/") or ".." in rel.split("/"):
            _fail("entry point must be a safe relative path")
        target = os.path.join(root, rel)
        if os.path.islink(target) or not os.path.isfile(target):
            _fail("entry point file not found")
    for kind in kinds:
        entry = KIND_ENTRY.get(kind)
        if entry is not None and entry not in entry_points:
            _fail("kind " + kind + " requires an entry point")
    widget = manifest.get("barWidget", None)
    if widget is not None:
        if not isinstance(widget, dict):
            _fail("barWidget must be an object")
        if "defaultSection" in widget and widget["defaultSection"] not in SECTIONS:
            _fail("barWidget.defaultSection must be left, center, or right")
        if "allowMultiple" in widget and not isinstance(widget["allowMultiple"], bool):
            _fail("barWidget.allowMultiple must be a boolean")
        aliases = widget.get("aliases", None)
        if aliases is not None:
            if not isinstance(aliases, list) or not all(isinstance(item, str) and item for item in aliases):
                _fail("barWidget.aliases must be an array of strings")
    for field in ("author", "license", "description"):
        if field in manifest and (not isinstance(manifest[field], str) or manifest[field].strip() == ""):
            _fail("manifest " + field + " must be a non-empty string")
    _assert_no_symlinks(root)
    return manifest


def _assert_no_symlinks(root):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in list(dirnames) + list(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                _fail("symlinks are not allowed inside a plugin folder")


def main(argv):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = argv[1] if len(argv) > 1 else root
    try:
        validate(target)
    except ManifestError as exc:
        print("manifest: " + str(exc), file=sys.stderr)
        return 1
    print("manifest ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
