#!/usr/bin/python3 -I
"""Hide or show the SteamVR status window. Not used by refresh.

The window that stays on the desk is class vrmonitor, title SteamVR Status.
steamvr-quiet already parks the untitled helper (title vrmonitor) on
special:vr silent. This moves only the status window.

This Hyprland is Lua. A classic dispatch of movetoworkspacesilent parses
special:frame-hidden as a method call. The move is hyprctl eval of
hl.dsp.window.move with follow = false, so the desk workspace stays put.
"""

import json
import os
import re
import stat
import subprocess
import sys
import threading

HYPRCTL = "/usr/bin/hyprctl"
TITLE = "SteamVR Status"
CLASS = "vrmonitor"
HIDDEN = "special:frame-hidden"
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]+$")
WS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$")
MAX_CLIENTS = 262144


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


def _killpg(pid):
    try:
        os.killpg(pid, 15)
    except OSError:
        return


def _drain(stream, cap, box, proc):
    data = bytearray()
    while len(data) <= cap:
        chunk = stream.read(65536)
        if not chunk:
            break
        data += chunk
    box.append(bytes(data))
    if len(data) > cap:
        _killpg(proc.pid)


def run_bounded(argv, timeout, cap):
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out_box = []
    err_box = []
    threads = (
        threading.Thread(target=_drain, args=(proc.stdout, cap, out_box, proc)),
        threading.Thread(target=_drain, args=(proc.stderr, 4096, err_box, proc)),
    )
    for thread in threads:
        thread.start()
    try:
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _killpg(proc.pid)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 9)
                except OSError:
                    pass
                proc.wait(timeout=1)
            for thread in threads:
                thread.join(timeout=1)
            raise
        for thread in threads:
            thread.join(timeout=1)
        out = out_box[0] if out_box else b""
        err = err_box[0] if err_box else b""
        if len(out) > cap or len(err) > 4096:
            raise RuntimeError("hyprctl output exceeded the limit")
        return code, out, err
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


def targets(clients):
    found = []
    if not isinstance(clients, list):
        return found
    for client in clients:
        if not isinstance(client, dict):
            continue
        if client.get("class") != CLASS or client.get("title") != TITLE:
            continue
        address = client.get("address") or ""
        workspace = client.get("workspace") if isinstance(client.get("workspace"), dict) else {}
        name = workspace.get("name") or ""
        if ADDR_RE.fullmatch(str(address)) is None or WS_RE.fullmatch(str(name)) is None:
            continue
        found.append({"address": str(address), "workspace": str(name)})
        if len(found) >= 4:
            break
    return found


def usable_workspace(name):
    return bool(name) and WS_RE.fullmatch(name) is not None and not name.startswith("special:")


def plan(op, clients, saved, active):
    rows = targets(clients)
    visible = [row for row in rows if not row["workspace"].startswith("special:")]
    if not rows:
        return {"state": "absent", "moves": [], "save": saved if usable_workspace(saved) else ""}
    if op == "state":
        state = "shown" if visible else "hidden"
        return {"state": state, "moves": [], "save": saved if usable_workspace(saved) else ""}
    if op == "toggle":
        op = "hide" if visible else "show"
    moves = []
    next_save = saved if usable_workspace(saved) else ""
    if op == "hide":
        if visible:
            next_save = visible[0]["workspace"]
        for row in visible:
            moves.append({"workspace": HIDDEN, "address": row["address"]})
        state = "hidden"
    elif op == "show":
        dest = next_save if usable_workspace(next_save) else ""
        if not dest and usable_workspace(active):
            dest = active
        if not dest:
            return {"state": "hidden", "moves": [], "save": "", "error": "no desk workspace to restore"}
        for row in rows:
            if row["workspace"].startswith("special:"):
                moves.append({"workspace": dest, "address": row["address"]})
        state = "shown"
    else:
        raise ValueError("unknown window action")
    eval_script(moves)
    return {"state": state, "moves": moves, "save": next_save}


def state_dir():
    root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return root


def open_state_dir(create=False):
    root = state_dir()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        name = "jordan.steam-frame"
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
            raise PermissionError("state dir")
        if st.st_mode & 0o077:
            if not create:
                raise PermissionError("state dir")
            os.fchmod(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_saved(dirfd):
    try:
        fd = os.open("window.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dirfd)
    except FileNotFoundError:
        return ""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 or st.st_size > 512:
            return ""
        os.set_blocking(fd, True)
        data = os.read(fd, 513)
        if len(data) > 512:
            return ""
        payload = json.loads(data.decode("utf-8"))
        name = payload.get("workspace") if isinstance(payload, dict) else ""
        return name if usable_workspace(name) else ""
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ""
    finally:
        os.close(fd)


def write_saved(dirfd, workspace):
    if not usable_workspace(workspace):
        return
    payload = json.dumps({"workspace": workspace}).encode("utf-8")
    tmp = ".window." + os.urandom(8).hex() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dirfd)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
        os.fsync(fd)
        os.rename(tmp, "window.json", src_dir_fd=dirfd, dst_dir_fd=dirfd)
        os.fsync(dirfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)


def lua_string(value):
    if not isinstance(value, str) or WS_RE.fullmatch(value) is None and ADDR_RE.fullmatch(value) is None:
        raise ValueError("refusing value")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def eval_script(moves):
    if not isinstance(moves, list) or len(moves) > 4:
        raise ValueError("refusing window list")
    if not moves:
        return ""
    lines = ["local want = {"]
    for move in moves:
        if not isinstance(move, dict):
            raise ValueError("refusing window")
        workspace = move.get("workspace")
        address = move.get("address")
        if not isinstance(address, str) or ADDR_RE.fullmatch(address) is None:
            raise ValueError("refusing address")
        if workspace != HIDDEN and not usable_workspace(workspace):
            raise ValueError("refusing workspace")
        lines.append("[" + lua_string(address.lower()) + "] = " + lua_string(workspace) + ",")
    lines.append("}")
    lines.append(
        "local moved = 0\n"
        "for _, w in ipairs(hl.get_windows() or {}) do\n"
        '  local dest = want[string.lower(w.address or "")]\n'
        '  if dest and w.class == "vrmonitor" and w.title == "SteamVR Status" then\n'
        "    hl.dispatch(hl.dsp.window.move({ workspace = dest, window = w, follow = false }))\n"
        "    moved = moved + 1\n"
        "  end\n"
        "end\n"
        "if moved ~= " + str(len(moves)) + ' then error("window count") end'
    )
    return "\n".join(lines)


def focus_script(workspace):
    if not isinstance(workspace, str) or WS_RE.fullmatch(workspace) is None:
        raise ValueError("refusing workspace")
    return "hl.dispatch(hl.dsp.focus({ workspace = " + lua_string(workspace) + " }))"


def load_clients(runner):
    code, out, _err = runner([HYPRCTL, "clients", "-j"], 5, MAX_CLIENTS)
    if code != 0:
        raise RuntimeError("could not read windows")
    return json.loads(out.decode("utf-8"))


def load_active(runner):
    code, out, _err = runner([HYPRCTL, "activeworkspace", "-j"], 5, 8192)
    if code != 0:
        return ""
    payload = json.loads(out.decode("utf-8"))
    name = payload.get("name") if isinstance(payload, dict) else ""
    text = str(name)
    return text if WS_RE.fullmatch(text) else ""


def confirm_moves(moves, clients):
    found = {row["address"].lower(): row["workspace"] for row in targets(clients)}
    for move in moves:
        if found.get(move["address"].lower()) != move["workspace"]:
            return False
    return True


def restore_desk(runner, before):
    if not before:
        return
    after = load_active(runner)
    if after == before:
        return
    code, out, _err = runner([HYPRCTL, "eval", focus_script(before)], 5, 4096)
    text = out.decode("utf-8", "replace").strip()
    if code != 0 or text != "ok" or load_active(runner) != before:
        raise RuntimeError("the move changed the desk workspace")


def apply_plan(action, runner, dirfd):
    moves = action["moves"]
    if not moves:
        return action["state"]
    before = load_active(runner)
    code, out, err = runner([HYPRCTL, "eval", eval_script(moves)], 5, 8192)
    text = out.decode("utf-8", "replace").strip()
    if code != 0 or text != "ok":
        try:
            restore_desk(runner, before)
        except RuntimeError:
            pass
        detail = plain(text or err.decode("utf-8", "replace"), 160)
        message = "could not move the SteamVR status window"
        if detail:
            message += ": " + detail
        raise RuntimeError(message)
    if not confirm_moves(moves, load_clients(runner)):
        try:
            restore_desk(runner, before)
        except RuntimeError:
            pass
        raise RuntimeError("the SteamVR status window did not move")
    if action.get("save"):
        write_saved(dirfd, action["save"])
    restore_desk(runner, before)
    return action["state"]


def perform(op, runner=None, dirfd=None):
    if runner is None:
        runner = run_bounded
    close_dir = False
    if dirfd is None:
        try:
            dirfd = open_state_dir(create=False)
            close_dir = True
        except FileNotFoundError:
            dirfd = None
    try:
        clients = load_clients(runner)
        saved = read_saved(dirfd) if dirfd is not None else ""
        active = load_active(runner) if op in ("show", "toggle") else ""
        action = plan(op, clients, saved, active)
        if action["moves"]:
            if dirfd is None:
                dirfd = open_state_dir(create=True)
                close_dir = True
            state = apply_plan(action, runner, dirfd)
        else:
            state = action["state"]
        if action.get("error"):
            return state, action["error"]
        return state, ""
    finally:
        if close_dir and dirfd is not None:
            os.close(dirfd)


def main(argv):
    op = argv[1] if len(argv) > 1 else "state"
    if op not in ("state", "toggle", "hide", "show"):
        print("use: window.py state|hide|show|toggle", file=sys.stderr)
        return 2
    try:
        state, err = perform(op)
    except Exception as exc:
        print(plain(str(exc), 200), file=sys.stderr)
        print("absent")
        return 1
    if err:
        print(plain(err, 200), file=sys.stderr)
    print(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
