# Native Windows: the gap

Native Windows (without WSL) is **not supported yet**, and nothing in this
repository has ever been run there. The intent is Windows and Linux both, with
[Everything](https://www.voidtools.com/) (`es.exe`) providing file search on
Windows where Linux uses `plocate`. That is planned, not built. What "make it
work" actually means, smallest first:

| Item | What native Windows needs | Size |
|---|---|---|
| **Shadow mode's detached worker** | `start_new_session=True` is POSIX-only and raises on Windows. Needs `creationflags=DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP`. Shadow is the default mode, so this is on the first-run path. | small |
| **Python launcher** | Windows has no `python3` on PATH. Probe the `py` launcher, then `python.exe`, and resolve to an absolute interpreter path, since a hook runs with a minimal environment. | small |
| **Hook command form** | The same command string with a Windows interpreter and an absolute Windows path, backslashes escaped in JSON. `install/wire.sh` is bash, so the writer needs a Python or PowerShell equivalent. | small |
| **Path handling** | `$HOME` is usually unset (`%USERPROFILE%`); a drive root is `C:\`, not `/`, and there may be several; separators may be either slash; comparisons must be case-insensitive. | medium |
| **File search** | The `plocate` suggestion string becomes `es.exe -i "<pattern>"`; `es`/`es.exe` must join the already-indexed tool family so R8 does not suggest replacing the indexed tool with itself; `filesearch/` becomes a *detection* check rather than an installer, because Everything maintains its own index and is not installable from here. | medium |
| **No systemd** | Task Scheduler equivalents for the timers (`schtasks /sc HOURLY` covers them); the daemon would need a Task or a Windows service. Each unit's `Nice`/`CPUQuota` intent maps only coarsely. | medium |
| **The daemon's transport** | It listens on a Unix domain socket, mode 700, with no TCP listener at all. The recommended first Windows release simply **skips it** and uses the direct-HTTPS fallback the client already has (about 0.9 s against about 0.3 s warm), exactly as WSL-without-systemd does today. A named pipe is the correct analogue but needs a non-stdlib dependency; a localhost TCP listener is stdlib-only but is precisely the design this project refuses. | large, or small if skipped |
| **The shell scripts** | The installer, doctor, wire, deploy and rollback are all bash. Either require Git Bash, or port the installer and doctor to Python, which they arguably should be anyway given the guard is stdlib Python. | large |

The smallest credible native-Windows release, in dependency order: the
detached worker, the Python launcher, the hook command form, path handling,
then file search -- with the daemon skipped and the installer and doctor
ported to Python. That is the guard itself, in shadow mode, with the
file-search rule pointing at `es.exe`, no daemon and nothing scheduled.

Until then, the supported Windows route is WSL2 with systemd on. See
[INSTALL-WSL.md](INSTALL-WSL.md).
