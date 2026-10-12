#!/usr/bin/python3
"""Verifier for the Steam Frame bar: verdict, charge, audio, window, config, install."""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import refresh  # noqa: E402
import scrub_check  # noqa: E402
import validate_manifest  # noqa: E402
import window  # noqa: E402

RADIO = """SteamVR is Ready. The computer is not sending a picture yet.
radio: up
traffic: rx 12 KB/s  tx 40 KB/s over 1s
steam flags: pipewire vrlinkforceenable
steamvr: Ready
compositor: up
charge: 64% discharging
audio: desktop
"""
PICTURE = """The computer is sending the picture to the Frame.
radio: up
traffic: rx 80 KB/s  tx 820 KB/s over 1s
steam flags: pipewire vrlinkforceenable
steamvr: Standby
compositor: up
charge: 80% charging
audio: game
"""
REPORT_KEYS = {"verdict", "link", "charge", "steamvr", "audio", "find", "bridge", "extra", "error"}


def dotted(*parts):
    return ".".join(str(part) for part in parts)


class StatusParse(unittest.TestCase):
    def test_radio_and_picture(self):
        radio = refresh.parse_status(RADIO)
        picture = refresh.parse_status(PICTURE)
        self.assertEqual(radio["link"], "radio")
        self.assertEqual(radio["verdict"], "SteamVR is Ready. The computer is not sending a picture yet.")
        self.assertEqual(radio["charge"], "64% discharging")
        self.assertEqual(radio["steamvr"], "Ready")
        self.assertEqual(radio["audio"], "on the desktop")
        self.assertEqual(picture["link"], "picture")
        self.assertEqual(picture["verdict"], "The computer is sending the picture to the Frame.")
        self.assertEqual(picture["charge"], "80% charging")
        self.assertEqual(picture["steamvr"], "Standby")
        self.assertEqual(picture["audio"], "in the game")
        named = refresh.parse_status("waiting\naudio: desktop\n", "workstation")
        self.assertEqual(named["audio"], "on the workstation desktop")
        for state in ("Ready", "Standby", "NotReady", "Connecting", "unknown"):
            parsed = refresh.parse_status("waiting\nsteamvr: " + state + "\n")
            self.assertEqual(parsed["steamvr"], state)
        down = refresh.parse_status("The radio is down.\n")
        self.assertEqual(down["link"], "down")

    def test_markup_and_bad_charge_are_stripped(self):
        parsed = refresh.parse_status("hello <img src='x'>\ncharge: 1% discharging\n")
        self.assertNotIn("<", parsed["verdict"])
        self.assertNotIn(">", parsed["verdict"])
        self.assertEqual(refresh.parse_status("waiting\ncharge: 101% charging\n")["charge"], "unknown")


class RefreshCommands(unittest.TestCase):
    def test_refresh_is_only_the_three_read_only_verbs(self):
        commands = refresh.planned_commands("/usr/local/bin/steam-frame")
        self.assertEqual(
            commands,
            [
                ["/usr/local/bin/steam-frame", "status"],
                ["/usr/local/bin/steam-frame", "find"],
                ["/usr/local/bin/steam-frame", "bridge"],
            ],
        )
        for argv in commands:
            self.assertNotIn("--apply", argv)
            self.assertNotIn("vrcmd", argv)

    def test_collect_passes_status_through_and_runs_extra(self):
        seen = []

        def runner(argv, timeout):
            seen.append(argv)
            name = argv[-1]
            if name == "status":
                return 1, RADIO, ""
            if name == "find":
                return 0, "Controllers are off. Press the Steam button.\n", ""
            if name == "bridge":
                return 1, RADIO, ""
            if argv[0].endswith("probe"):
                return 0, "probe-line one\nprobe-line two\n", ""
            raise AssertionError(argv)

        report = refresh.collect(
            refresh.planned_commands("/bin/steam-frame"),
            ["/tmp/probe"],
            runner,
            "workstation",
        )
        self.assertEqual(
            [cmd[-1] if cmd[0].endswith("steam-frame") else cmd[0] for cmd in seen],
            ["status", "find", "bridge", "/tmp/probe"],
        )
        self.assertEqual(report["verdict"], "SteamVR is Ready. The computer is not sending a picture yet.")
        self.assertEqual(report["link"], "radio")
        self.assertEqual(report["audio"], "on the workstation desktop")
        self.assertEqual(report["find"], "Controllers are off. Press the Steam button.")
        self.assertEqual(report["extra"], ["probe-line one", "probe-line two"])
        self.assertEqual(set(report), REPORT_KEYS)
        self.assertNotIn("--apply", json.dumps(seen))

    def test_refuses_write_commands(self):
        for argv in (
            ["/usr/bin/bash", "-c", "echo hi"],
            ["/usr/bin/steam", "bridge", "--apply"],
            ["/usr/bin/systemctl", "restart", "pipewire"],
            ["/usr/bin/vrcmd", "buzz"],
            ["/usr/local/bin/steam-frame-host"],
            ["/usr/bin/gamescope"],
        ):
            with self.assertRaises(ValueError):
                refresh.validate_extra(argv)

    def test_register_one_command_without_touching_the_widget(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "probe-status")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\necho probe-from-slot\n")
            os.chmod(script, 0o700)
            config = os.path.join(tmp, "config")
            os.mkdir(config, 0o700)
            os.environ["XDG_CONFIG_HOME"] = config
            try:
                self.assertEqual(refresh.do_register([script]), 0)
                slot = os.path.join(config, "steam-frame", "extra-status.json")
                with open(slot, encoding="utf-8") as handle:
                    self.assertEqual(json.load(handle), [script])
                mode = stat.S_IMODE(os.stat(slot).st_mode)
                self.assertEqual(mode, 0o600)
                dirfd = refresh.open_dir_chain(config, ["steam-frame"], create=False)
                try:
                    loaded = refresh.read_extra_argv(dirfd)
                finally:
                    os.close(dirfd)
                self.assertEqual(loaded, [script])
                link = os.path.join(tmp, "linked")
                os.symlink(script, link)
                with self.assertRaises(ValueError):
                    refresh.validate_extra([link])
            finally:
                os.environ.pop("XDG_CONFIG_HOME", None)

    def test_widget_refresh_does_not_call_window_or_apply(self):
        with open(os.path.join(ROOT, "Panel.qml"), encoding="utf-8") as handle:
            panel = handle.read()
        self.assertIn('root.refreshHelper, "refresh"', panel)
        self.assertIn('root.windowHelper, "toggle"', panel)
        self.assertNotIn("--apply", panel)
        self.assertNotIn("vrcmd", panel)
        self.assertNotIn("steam-frame-host", panel)
        self.assertNotIn("textFormat: Text.AutoText", panel)
        self.assertGreaterEqual(panel.count("textFormat: Text.PlainText"), 8)


class ConfigFile(unittest.TestCase):
    def test_example_is_empty_and_valid(self):
        with open(os.path.join(ROOT, "config.example.json"), encoding="utf-8") as handle:
            payload = json.load(handle)
        settings = refresh.validate_settings(payload)
        self.assertEqual(settings["desktop_name"], "")
        self.assertEqual(settings["steam_frame_bin"], "")
        self.assertEqual(settings["headset_ip"], "")
        self.assertEqual(settings["headset_host"], "")
        self.assertIsNone(settings["headset_port"])
        self.assertEqual(settings["headset_user"], "")
        self.assertEqual(settings["connection_name"], "")
        self.assertEqual(settings["steam_dir"], "")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                self.assertEqual(refresh.load_settings(), refresh.default_settings())
            finally:
                os.environ.pop("XDG_CONFIG_HOME", None)

    def test_operator_fields_are_not_forwarded(self):
        address = dotted(203, 0, 113, 10)
        marker = "desk-marker"
        with tempfile.TemporaryDirectory() as tmp:
            config_home = os.path.join(tmp, "config")
            os.mkdir(config_home, 0o700)
            frame = os.path.join(config_home, "steam-frame")
            os.mkdir(frame, 0o700)
            binary = os.path.join(tmp, "steam-frame")
            with open(binary, "w", encoding="utf-8") as handle:
                handle.write(
                    "#!/bin/sh\n"
                    "case \"$1\" in\n"
                    "  status) printf '%s\\n' 'The computer is sending the picture to the Frame.' "
                    "'charge: 10% charging' 'steamvr: Ready' 'audio: desktop' ;;\n"
                    "  find) printf '%s\\n' 'Controllers are off.' ;;\n"
                    "  bridge) printf '%s\\n' 'bridge ready' ;;\n"
                    "esac\n"
                )
            os.chmod(binary, 0o700)
            payload = {
                "desktop_name": "workstation",
                "steam_frame_bin": binary,
                "headset_ip": address,
                "headset_host": "headset.example",
                "headset_port": 22,
                "headset_user": "steamos",
                "connection_name": "frame-dongle",
                "steam_dir": "/opt/steam",
            }
            path = os.path.join(frame, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.chmod(path, 0o600)
            os.environ["XDG_CONFIG_HOME"] = config_home
            try:
                loaded = refresh.load_settings()
                self.assertEqual(loaded["headset_ip"], address)
                commands = refresh.planned_commands(refresh.steam_frame_bin(loaded))
                blob = json.dumps(commands)
                self.assertNotIn(address, blob)
                self.assertNotIn("headset.example", blob)
                self.assertNotIn("/opt/steam", blob)
                self.assertEqual(commands[0][0], binary)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = refresh.do_refresh()
                self.assertEqual(code, 0)
                report = json.loads(buf.getvalue())
                self.assertEqual(set(report), REPORT_KEYS)
                rendered = json.dumps(report)
                self.assertNotIn(address, rendered)
                self.assertNotIn("headset.example", rendered)
                self.assertNotIn(marker, rendered)
                self.assertEqual(report["audio"], "on the workstation desktop")
                self.assertEqual(report["link"], "picture")
                self.assertEqual(report["find"], "Controllers are off.")
            finally:
                os.environ.pop("XDG_CONFIG_HOME", None)

    def test_refuses_loose_unknown_and_broken_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_home = os.path.join(tmp, "config")
            os.mkdir(config_home, 0o700)
            frame = os.path.join(config_home, "steam-frame")
            os.mkdir(frame, 0o700)
            path = os.path.join(frame, "config.json")
            secret_marker = "not-a-real-token-" + "abc123"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{ " + secret_marker)
            os.chmod(path, 0o600)
            os.environ["XDG_CONFIG_HOME"] = config_home
            try:
                with self.assertRaises(ValueError) as broken:
                    refresh.load_settings()
                self.assertNotIn(secret_marker, str(broken.exception))
                os.chmod(path, 0o644)
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"desktop_name": "workstation"}, handle)
                with self.assertRaises(ValueError):
                    refresh.load_settings()
                os.chmod(path, 0o600)
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"desktop_name": "workstation", "extra": "nope"}, handle)
                with self.assertRaises(ValueError):
                    refresh.load_settings()
                with self.assertRaises(ValueError):
                    refresh.validate_settings({"headset_port": True})
                with self.assertRaises(ValueError):
                    refresh.validate_settings({"steam_dir": "relative/steam"})
                with self.assertRaises(ValueError):
                    refresh.validate_settings({"headset_ip": "not-an-address"})
            finally:
                os.environ.pop("XDG_CONFIG_HOME", None)

        with tempfile.TemporaryDirectory() as tmp:
            config_home = os.path.join(tmp, "config")
            os.mkdir(config_home, 0o700)
            frame = os.path.join(config_home, "steam-frame")
            os.mkdir(frame, 0o755)
            os.environ["XDG_CONFIG_HOME"] = config_home
            try:
                with self.assertRaises(PermissionError):
                    refresh.load_settings()
            finally:
                os.environ.pop("XDG_CONFIG_HOME", None)


class WindowPlan(unittest.TestCase):
    def test_hide_and_show_the_status_window(self):
        clients = [{
            "address": "0xa1b2c3d4e5f6",
            "class": "vrmonitor",
            "title": "SteamVR Status",
            "workspace": {"name": "desk"},
        }, {
            "address": "0x111111",
            "class": "vrmonitor",
            "title": "vrmonitor",
            "workspace": {"name": "special:helper"},
        }]
        hidden = window.plan("hide", clients, "", "1")
        self.assertEqual(hidden["state"], "hidden")
        self.assertEqual(hidden["moves"], [{"workspace": "special:frame-hidden", "address": "0xa1b2c3d4e5f6"}])
        self.assertEqual(hidden["save"], "desk")
        script = window.eval_script(hidden["moves"])
        self.assertIn("follow = false", script)
        self.assertIn('["0xa1b2c3d4e5f6"]', script)
        self.assertIn('"special:frame-hidden"', script)
        self.assertIn('w.title == "SteamVR Status"', script)
        self.assertNotIn("movetoworkspacesilent", script)
        self.assertNotIn("os.execute", script)
        moved = [{
            "address": "0xa1b2c3d4e5f6",
            "class": "vrmonitor",
            "title": "SteamVR Status",
            "workspace": {"name": "special:frame-hidden"},
        }]
        shown = window.plan("show", moved, hidden["save"], "1")
        self.assertEqual(shown["state"], "shown")
        self.assertEqual(shown["moves"], [{"workspace": "desk", "address": "0xa1b2c3d4e5f6"}])
        show_script = window.eval_script(shown["moves"])
        self.assertIn("follow = false", show_script)
        self.assertIn('"desk"', show_script)
        self.assertEqual(window.plan("hide", [clients[1]], "", "1")["state"], "absent")
        with self.assertRaises(ValueError):
            window.eval_script([{"workspace": '"); os.execute("id"', "address": "0xa1b2c3d4e5f6"}])
        with self.assertRaises(ValueError):
            window.focus_script('2"); os.execute("id"')

    def test_rejects_a_title_injected_into_the_dispatch(self):
        clients = [{
            "address": "0xabc;dispatch",
            "class": "vrmonitor",
            "title": "SteamVR Status",
            "workspace": {"name": "desk"},
        }]
        action = window.plan("hide", clients, "", "1")
        self.assertEqual(action["moves"], [])
        self.assertEqual(action["state"], "absent")


class ReleaseFiles(unittest.TestCase):
    def test_manifest_readme_and_scrub(self):
        manifest = validate_manifest.validate(ROOT)
        self.assertEqual(manifest["id"], "jordan.steam-frame")
        self.assertEqual(manifest["entryPoints"]["barWidget"], "Panel.qml")
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        self.assertIn("tag=v" + manifest["version"], readme)
        self.assertIn("install-from-tag.sh", readme)
        self.assertIn("omarchy/plugins/jordan.steam-frame", readme)
        self.assertIn("docs/screenshot.svg", readme)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "screenshot.svg")))
        self.assertEqual(scrub_check.scan(ROOT), [])

    def test_scrub_check_flags_a_planted_address(self):
        planted = dotted(198, 51, 100, 20)
        host = "th" + "or"
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "notes.txt"), "w", encoding="utf-8") as handle:
                handle.write("desk " + host + " at " + planted + "\n")
            categories = {item[2] for item in scrub_check.scan(tmp)}
            self.assertIn("ipv4", categories)
            self.assertIn("house-name", categories)

    def test_local_install_and_uninstall_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
            env["HOME"] = os.path.join(tmp, "home")
            os.mkdir(env["HOME"], 0o700)
            install = os.path.join(ROOT, "scripts", "install.sh")
            uninstall = os.path.join(ROOT, "scripts", "uninstall.sh")
            subprocess.check_call(["/bin/sh", install], env=env)
            dest = os.path.join(env["XDG_CONFIG_HOME"], "omarchy", "plugins", "jordan.steam-frame")
            with open(os.path.join(dest, "manifest.json"), encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["id"], "jordan.steam-frame")
            self.assertFalse(os.path.exists(os.path.join(dest, ".git")))
            junk = os.path.join(dest, "junk.txt")
            with open(junk, "w", encoding="utf-8") as handle:
                handle.write("x")
            subprocess.check_call(["/bin/sh", install], env=env)
            self.assertTrue(os.path.isfile(os.path.join(dest, "Panel.qml")))
            self.assertFalse(os.path.exists(junk))
            kept = os.path.join(env["XDG_CONFIG_HOME"], "steam-frame")
            os.mkdir(kept, 0o700)
            with open(os.path.join(kept, "config.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
            subprocess.check_call(["/bin/sh", uninstall], env=env)
            self.assertFalse(os.path.exists(dest))
            self.assertTrue(os.path.isfile(os.path.join(kept, "config.json")))


if __name__ == "__main__":
    unittest.main()
