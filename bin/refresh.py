#!/usr/bin/python3 -I
"""Read-only Steam Frame panel refresh.

Runs steam-frame status, find, and bridge with no --apply. One optional
extra status command is read from ~/.config/steam-frame/extra-status.json.
Desk settings are read from ~/.config/steam-frame/config.json. Prints one
JSON object. Does not restart Steam, gamescope, or PipeWire, does not run
vrcmd, does not launch a game, and does not open a connection to the headset.
"""

import json
import os
import re
import stat
import subprocess
import sys
import threading

MAX_OUT = 16384
MAX_ERR = 1024
MAX_EXTRA_FILE = 4096
MAX_CONFIG = 8192
CMD_TIMEOUT = 22
EXTRA_TIMEOUT = 8
STEAMVR_STATES = ("Ready", "Standby", "NotReady", "Connecting", "unknown")
DESKTOP_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,39}[A-Za-z0-9])?$")
HOST_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
CONN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,80}$")
DENY_BASE = frozenset({
    "steam",
    "steam-frame-host",
    "gamescope",
    "gamescope-session",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "systemctl",
    "vrcmd",
    "pactl",
    "wpctl",
    "bash",
    "sh",
    "dash",
    "zsh",
    "env",
    "python",
    "python3",
    "kill",
    "pkill",
    "reboot",
    "shutdown",
})
DENY_ARG = frozenset({"--apply", "restart", "start", "stop", "--shutdown"})
PATH_RE = re.compile(r"/[A-Za-z0-9_./+-]+")
CHARGE_RE = re.compile(r"(\d{1,3})% (charging|discharging)")


def plain(value, limit):
    text = "" if value is None else str(value)
    out = []
    for ch in text:
        if ch in "<>&":
            continue
        code = ord(ch)
        if code < 32 or 0x7F <= code <= 0x9F:
            continue
        out.append(ch)
        if len(out) >= limit:
            break
    return "".join(out)


def default_settings():
    return {
        "desktop_name": "",
        "steam_frame_bin": "",
        "headset_ip": "",
        "headset_host": "",
        "headset_port": None,
        "headset_user": "",
        "connection_name": "",
        "steam_dir": "",
    }


def _bad(key):
    raise ValueError("config.json key " + key + " is invalid")


def _optional_text(value, key, pattern):
    if value is None:
        return ""
    if not isinstance(value, str):
        _bad(key)
    if value == "":
        return ""
    if pattern.fullmatch(value) is None:
        _bad(key)
    return value


def _optional_abs_path(value, key):
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 200:
        _bad(key)
    parts = value.split("/")
    if not value.startswith("/") or any(part in ("", ".", "..") for part in parts[1:]):
        _bad(key)
    if PATH_RE.fullmatch(value) is None:
        _bad(key)
    return value


def _optional_ipv4(value):
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 15:
        _bad("headset_ip")
    parts = value.split(".")
    if len(parts) != 4:
        _bad("headset_ip")
    for part in parts:
        if not part.isdigit() or len(part) > 3 or (len(part) > 1 and part.startswith("0")):
            _bad("headset_ip")
        if int(part) > 255:
            _bad("headset_ip")
    return value


def _optional_port(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _bad("headset_port")
    if not 1 <= value <= 65535:
        _bad("headset_port")
    return value


def validate_settings(payload):
    """Check config.json. Error text never includes the values."""
    if not isinstance(payload, dict):
        raise ValueError("config.json must be an object")
    allowed = set(default_settings())
    if set(payload) - allowed:
        raise ValueError("config.json has an unknown key")
    host = _optional_text(payload.get("headset_host", ""), "headset_host", HOST_RE)
    if len(host) > 253:
        _bad("headset_host")
    return {
        "desktop_name": _optional_text(payload.get("desktop_name", ""), "desktop_name", DESKTOP_RE),
        "steam_frame_bin": _optional_abs_path(payload.get("steam_frame_bin", ""), "steam_frame_bin"),
        "headset_ip": _optional_ipv4(payload.get("headset_ip", "")),
        "headset_host": host,
        "headset_port": _optional_port(payload.get("headset_port", None)),
        "headset_user": _optional_text(payload.get("headset_user", ""), "headset_user", USER_RE),
        "connection_name": _optional_text(payload.get("connection_name", ""), "connection_name", CONN_RE),
        "steam_dir": _optional_abs_path(payload.get("steam_dir", ""), "steam_dir"),
    }


def steam_frame_bin(settings=None):
    if settings is None:
        settings = load_settings()
    custom = settings.get("steam_frame_bin") or ""
    if custom:
        return custom
    return os.path.expanduser("~/.local/bin/steam-frame")


def planned_commands(bin_path):
    """The only steam-frame invocations a refresh performs."""
    return [
        [bin_path, "status"],
        [bin_path, "find"],
        [bin_path, "bridge"],
    ]


def parse_status(text, desktop_name=""):
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    verdict = lines[0] if lines else ""
    fields = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        fields.setdefault(key.strip().lower(), val.strip())
    if "is sending the picture to the Frame." in verdict:
        link = "picture"
    elif "radio is down" in verdict:
        link = "down"
    else:
        link = "radio"
    raw_state = fields.get("steamvr", "unknown").split("|", 1)[0].strip()
    state = raw_state if raw_state in STEAMVR_STATES else "unknown"
    audio = fields.get("audio", "unknown")
    if audio == "game":
        audio_phrase = "in the game"
    elif audio == "desktop":
        if desktop_name:
            audio_phrase = "on the " + desktop_name + " desktop"
        else:
            audio_phrase = "on the desktop"
    else:
        audio_phrase = "unknown"
    charge = fields.get("charge", "unknown")
    match = CHARGE_RE.fullmatch(charge)
    if charge != "unknown" and not (match and int(match.group(1)) <= 100):
        charge = "unknown"
    return {
        "verdict": plain(verdict, 300),
        "link": link,
        "charge": plain(charge, 32),
        "steamvr": state,
        "audio": audio_phrase,
    }


def first_line(text):
    for line in str(text).splitlines():
        if line.strip():
            return plain(line.strip(), 300)
    return ""


def validate_extra(argv):
    if not isinstance(argv, list) or not 1 <= len(argv) <= 6:
        raise ValueError("extra status command must be 1 to 6 arguments")
    for arg in argv:
        if not isinstance(arg, str) or arg == "" or len(arg) > 200:
            raise ValueError("bad extra argument")
        if "\x00" in arg or "\n" in arg or "\r" in arg:
            raise ValueError("bad extra argument")
        if arg in DENY_ARG or arg.startswith("steam://") or "rungameid" in arg:
            raise ValueError("extra status command is not read-only")
    exe = argv[0]
    parts = exe.split("/")
    if not exe.startswith("/") or any(part in ("", ".", "..") for part in parts[1:]):
        raise ValueError("extra command must be an absolute path")
    if PATH_RE.fullmatch(exe) is None:
        raise ValueError("extra command path has unexpected characters")
    base = os.path.basename(exe)
    if base in DENY_BASE:
        raise ValueError("extra status command is not read-only")
    st = os.lstat(exe)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("extra command must be a regular file, not a symlink")
    if st.st_uid != os.geteuid() or st.st_mode & 0o002:
        raise ValueError("extra command has the wrong owner or mode")
    if not os.access(exe, os.X_OK):
        raise ValueError("extra command is not executable")
    return argv


def _killpg(pid):
    try:
        os.killpg(pid, 15)
    except OSError:
        return


def run_bounded(argv, timeout, cap, env=None):
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    out_box = []
    err_box = []

    def drain(stream, box, limit):
        data = bytearray()
        while len(data) <= limit:
            chunk = stream.read(65536)
            if not chunk:
                break
            data += chunk
        box.append(bytes(data))
        if len(data) > limit:
            _killpg(proc.pid)

    threads = [
        threading.Thread(target=drain, args=(proc.stdout, out_box, cap)),
        threading.Thread(target=drain, args=(proc.stderr, err_box, MAX_ERR)),
    ]
    for thread in threads:
        thread.start()
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _killpg(proc.pid)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 9)
                except OSError:
                    pass
                proc.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=1)
            raise
        for thread in threads:
            thread.join(timeout=1)
        out = out_box[0] if out_box else b""
        err = err_box[0] if err_box else b""
        if len(out) > cap:
            raise RuntimeError("command output exceeded the limit")
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


def open_dir_chain(root, parts, create=False):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for name in parts:
            if name in (".", "..") or "/" in name:
                raise PermissionError(name)
            try:
                nfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, 0o700, dir_fd=fd)
                nfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nfd
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
                raise PermissionError(name)
            if st.st_mode & 0o077:
                if not create:
                    raise PermissionError(name)
                os.fchmod(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def config_root():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def read_config_payload(dirfd):
    try:
        fd = os.open(
            "config.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=dirfd,
        )
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or stat.S_ISLNK(st.st_mode)
            or st.st_uid != os.geteuid()
            or st.st_nlink != 1
            or st.st_size > MAX_CONFIG
            or st.st_mode & 0o077
        ):
            raise ValueError("refusing config.json")
        os.set_blocking(fd, True)
        data = b""
        while len(data) <= MAX_CONFIG:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_CONFIG:
            raise ValueError("config.json is too large")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("config.json is not valid JSON") from None
        return validate_settings(payload)
    finally:
        os.close(fd)


def load_settings():
    """Read config.json. A missing file uses defaults. A bad file raises."""
    root = config_root()
    try:
        dirfd = open_dir_chain(root, ["steam-frame"], create=False)
    except FileNotFoundError:
        return default_settings()
    try:
        loaded = read_config_payload(dirfd)
    finally:
        os.close(dirfd)
    if loaded is None:
        return default_settings()
    return loaded


def read_extra_argv(dirfd):
    try:
        fd = os.open(
            "extra-status.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=dirfd,
        )
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or stat.S_ISLNK(st.st_mode)
            or st.st_uid != os.geteuid()
            or st.st_nlink != 1
            or st.st_size > MAX_EXTRA_FILE
        ):
            raise ValueError("refusing extra-status.json")
        os.set_blocking(fd, True)
        data = b""
        while len(data) <= MAX_EXTRA_FILE:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_EXTRA_FILE:
            raise ValueError("extra-status.json is too large")
        payload = json.loads(data.decode("utf-8"))
        return validate_extra(payload)
    finally:
        os.close(fd)


def write_extra_argv(dirfd, argv):
    validate_extra(argv)
    payload = json.dumps(argv).encode("utf-8")
    if len(payload) > MAX_EXTRA_FILE:
        raise ValueError("extra status command is too long")
    tmp = ".extra-status." + os.urandom(8).hex() + ".tmp"
    fd = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=dirfd,
    )
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            wrote = os.write(fd, view)
            view = view[wrote:]
        os.fsync(fd)
        os.rename(tmp, "extra-status.json", src_dir_fd=dirfd, dst_dir_fd=dirfd)
        os.fsync(dirfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)


def extra_lines(text):
    lines = []
    for line in str(text).splitlines():
        cleaned = plain(line.strip(), 200)
        if cleaned:
            lines.append(cleaned)
        if len(lines) >= 8:
            break
    return lines


def build_report(status_text, find_text, bridge_text, extra_text, problems, desktop_name=""):
    parsed = parse_status(status_text, desktop_name)
    report = {
        "verdict": parsed["verdict"],
        "link": parsed["link"],
        "charge": parsed["charge"],
        "steamvr": parsed["steamvr"],
        "audio": parsed["audio"],
        "find": first_line(find_text),
        "bridge": first_line(bridge_text),
        "extra": extra_lines(extra_text),
        "error": plain(" ".join(problems), 300),
    }
    return report


def collect(commands, extra, runner, desktop_name=""):
    """runner(argv, timeout) -> (returncode, stdout, stderr) or raises."""
    problems = []
    outputs = []
    for argv in commands:
        if "--apply" in argv or "vrcmd" in argv:
            raise RuntimeError("refresh refused a write")
        try:
            _code, out, _err = runner(argv, CMD_TIMEOUT)
        except Exception as exc:
            outputs.append("")
            problems.append(plain(os.path.basename(argv[0]) + " " + argv[-1] + " failed", 80))
            problems.append(plain(str(exc), 120))
            continue
        outputs.append(out)
    extra_text = ""
    if extra:
        try:
            _code, extra_text, _err = runner(extra, EXTRA_TIMEOUT)
        except Exception as exc:
            problems.append(plain("extra status failed: " + str(exc), 160))
    while len(outputs) < 3:
        outputs.append("")
    return build_report(outputs[0], outputs[1], outputs[2], extra_text, problems, desktop_name)


def emit(report):
    sys.stdout.write(json.dumps(report, ensure_ascii=True) + "\n")


def do_refresh():
    # Headset address, SSH host, connection name, and Steam directory are
    # validated here and then dropped. They are not command arguments.
    config_problem = ""
    try:
        settings = load_settings()
    except Exception as exc:
        settings = default_settings()
        config_problem = plain("config not loaded: " + str(exc), 160)
    desktop_name = settings["desktop_name"]
    bin_path = steam_frame_bin(settings)
    if not os.path.isfile(bin_path) or not os.access(bin_path, os.X_OK):
        problems = ["steam-frame is not executable"]
        if config_problem:
            problems.insert(0, config_problem)
        emit(build_report("", "", "", "", problems, desktop_name))
        return 1
    commands = planned_commands(bin_path)
    extra = None
    problems_prefix = []
    if config_problem:
        problems_prefix.append(config_problem)
    try:
        root = config_root()
        dirfd = open_dir_chain(root, ["steam-frame"], create=False)
        try:
            extra = read_extra_argv(dirfd)
        finally:
            os.close(dirfd)
    except FileNotFoundError:
        extra = None
    except Exception as exc:
        problems_prefix.append(plain("extra status not loaded: " + str(exc), 160))
        extra = None

    def runner(argv, timeout):
        return run_bounded(argv, timeout, MAX_OUT)

    report = collect(commands, extra, runner, desktop_name)
    if problems_prefix:
        report["error"] = plain(" ".join(problems_prefix + ([report["error"]] if report["error"] else [])), 300)
    emit(report)
    return 0


def do_register(argv):
    validate_extra(argv)
    root = config_root()
    if not os.path.isdir(root):
        raise SystemExit("config directory is missing")
    dirfd = open_dir_chain(root, ["steam-frame"], create=True)
    try:
        write_extra_argv(dirfd, argv)
    finally:
        os.close(dirfd)
    print("registered")
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "register":
        if len(argv) < 3 or argv[2] != "--":
            print("use: refresh.py register -- /absolute/command [args...]", file=sys.stderr)
            return 2
        return do_register(argv[3:])
    if len(argv) == 1 or (len(argv) == 2 and argv[1] == "refresh"):
        return do_refresh()
    print("use: refresh.py [refresh|register -- cmd]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except ValueError as exc:
        print(plain(str(exc), 200), file=sys.stderr)
        sys.exit(2)
