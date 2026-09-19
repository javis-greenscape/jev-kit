# Native Windows: what is done, and what is left

Native Windows (without WSL) was the gap in the first release. **The core is
now built and has been run on a real Windows 11 machine.** This file records
what that cost, what it proved, and what is still open, so nobody has to
re-derive it.

The install itself is **[INSTALL-WINDOWS.md](INSTALL-WINDOWS.md)**. This file
does not repeat it, and does not contradict it: if the two ever disagree,
INSTALL-WINDOWS.md is the one being followed and this one is stale.

## Done

| Item | How it was resolved |
|---|---|
| **Shadow mode's detached worker** | `start_new_session=True` is POSIX-only. Windows gets `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW` (the third stops a console flashing up on every judged call), from `airlock/platform_compat.py`. |
| **Python launcher** | The installer resolves an absolute interpreter: an explicit `AIRLOCK_PYTHON`, then `sys.executable`, then `C:\Windows\py.exe`, then a `python.exe` on `PATH` -- skipping the Microsoft Store alias stub, which is not an interpreter and would produce a hook that opens the Store. |
| **Hook command form** | Written by `install/windows_install.py` in one of **two** shapes, because they are not interchangeable: `"py.exe" "hook.py"` for Git Bash, and `& "py.exe" "hook.py"` for PowerShell, which needs the call operator. The installer detects which shell applies. Install Git for Windows later and you must re-run it with `--wire`. |
| **Path handling** | `airlock/winpath.py`: `C:\...`, `C:/...`, Git Bash's `/c/...` and Cygwin's `/cygdrive/c/...` all reduce to one canonical spelling, compared case-insensitively. A drive root or a UNC share root is disk-wide. |
| **Both shell tools** | Claude Code registers the PowerShell tool instead of Bash on a Windows machine without Git for Windows, so a hook matching only `Bash` would never fire there. Every shell rule matches `Bash|PowerShell`. |
| **Config, state and release directories** | `%APPDATA%\airlock`, `%LOCALAPPDATA%\airlock\state` and `%LOCALAPPDATA%\airlock`, with every environment override and the legacy-name fallback intact, and no `/tmp` anywhere on the Windows path. |
| **The key file** | The kit-level default is `%APPDATA%\jev-kit\env`, then the guard-era `%APPDATA%\airlock\env`, then the pointer file, then the legacy list -- the same seven-step order as POSIX, spelled for the platform. It is written down once, in the module docstring of `airlock/keyfile.py`, and the Windows installer and doctor both call that one resolver rather than spelling a path themselves. R1 protects both Windows key paths, in either separator and either case. |
| **Pointer-file trust** | The POSIX trust checks on `keyfile.path` and `repo.path` are an owner check and a mode check, and Windows has neither a uid nor a meaningful `st_mode`. There, the structural checks still run (a regular file; an absolute, existing target) and the permission checks are replaced by `platform_compat.pointer_trust_notes()`, which records **what was checked and what was not** and follows the pointer. Pretending the ownership check passed would be a lie; refusing every pointer would take the guard offline over a question the stdlib cannot answer there. |
| **File permissions** | `chmod 0o600` only toggles the read-only attribute on Windows and says nothing about who may read a file, so `platform_compat.restrict_path` skips it and `describe_permissions` reports the honest thing: privacy comes from the per-user ACL on `%APPDATA%`, not from a mode. |
| **File locking** | `fcntl.flock` has no Windows equivalent, so the log and the loop-protection state use `msvcrt.locking` on a byte range **past end of file**: Windows byte-range locks are mandatory, and a lock over live data would make an unrelated read in another process fail rather than wait. |
| **File search** | The suggestion comes from one platform-neutral function, `policy.filename_search_suggestion()`: `plocate` on Linux, `es.exe` on Windows. `es` joins the indexed-tool family, so a command already using Everything is never told to use Everything. `airlock/everything.py` detects `es.exe` and the running index and **never installs** either. |
| **R6 on a desktop** | No longer a Windows question. R6 (opening a GUI or a browser) is **`off` by default on every platform**, because most machines running Claude Code have a desktop; a headless machine of any kind turns it on with `{"R6-gui-or-browser": "deny"}` in `rules.json`. What remains Windows-specific is only the deny **text**: when R6 is turned on there it drops the "headless server" and "$DISPLAY unset" wording, neither of which is true on that platform. See `airlock/headless.py` and the FAQ entry "I run this on a headless server". |
| **No systemd** | The health check can be registered with Task Scheduler, but **only** behind an explicit `--schedule-health` flag. Nothing else is scheduled. |
| **The daemon's transport** | **Skipped, as recommended.** `client.ask()` checks up front that the platform has Unix sockets and goes straight to the direct HTTPS call, rather than letting `socket.AF_UNIX` raise into a blanket `except`. `python -m airlock.daemon` says so and exits 0 on Windows. |
| **`current` without symlink privilege** | `os.symlink` fails for an ordinary Windows user with *WinError 1314: A required privilege is not held by the client* -- measured on the test machine, not assumed. Instead: a **directory junction** (`mklink /J`, which needs no privilege) when the volume allows one; a one-line **`current.txt`** text pointer, written always, which cannot fail; and a stable **`airlock-hook.py`** launcher in the install root that `settings.json` points at, which resolves `current` and runs the real hook **in the same process**. `settings.json` never changes again, and a rollback is one line of `current.txt`. |
| **The shell scripts** | The installer, doctor and uninstaller are ported to Python, as the original gap list suggested they arguably should have been anyway. `install/_wire.py` is shared with the bash path. |

## What was proved on the machine, and how

Windows Python **3.11.9** on **Windows 11**, everything run from a scratch
directory under the Windows `%TEMP%` and removed afterwards.

- **The unit suite**, `py.exe -3 -m unittest discover -s tests`: OK, with a
  the POSIX-only tests skipped. Each skip prints its own reason. **No test
  requires Windows to pass**: the whole Windows code path is exercised from
  Linux by injecting the platform, so `install/deploy.sh`'s Linux test run
  still gates it.
- **A real deny**, piped at the installed launcher in enforce mode, through
  the **Bash tool** and again through the **PowerShell tool**. The rule is R1,
  secret exposure: `type %APPDATA%\jev-kit\env` and
  `Get-Content $env:APPDATA\airlock\env`. Nothing is executed and no key file
  is opened. The guard classifies the string and blocks it.
- **A real allow**: `echo hello` produces no output at all.
- **The Everything steer**: `dir /s C:\ *.xlsm` classifies as
  `scope=disk_wide program=dir`, search-like, would-deny true, and the deny
  text names `es.exe -path "<folder>" -n 50 "<pattern>"`. A command already
  using `es` is not steered at `es`.
- **Keyless fail-open**: with no key loadable, a malformed payload and the
  kill switch (`AIRLOCK_DISABLE=1`) both exit 0 with no output.
- **The Jev-judged path, with a real key** (2026-09-19, Windows Python
  **3.11.9**, Windows 11, key present in the scratch profile only and
  destroyed afterwards). All of it in **enforce** mode, piped at the installed
  launcher:
  - a **judged search deny** through the **Bash tool**, in both spellings
    (`dir /s C:\ *.xlsm` and the Git Bash form `find /c -name '*.xlsm'`),
    each returning the `es.exe` steer as deny JSON;
  - the same through the **PowerShell tool**:
    `Get-ChildItem -Path C:\ -Recurse -Filter *.xlsm`;
  - a **judged ALLOW**: `dir /s /b C:\proj\src\*.xlsm`, path-scoped inside a
    project folder, judged in 1032 ms and not blocked;
  - the **tier guard** on an `Agent` call two rungs over (blocked) and one
    rung over (the warn `additionalContext`, nothing blocked);
  - the **rewrite** output with `AIRLOCK_TIER_REWRITE=1`:
    `subagent_type` changed `fable` -> `scout-find`, every other field
    byte-identical;
  - the **doctor with a key present**: 13 passed, 0 failed, 2 skipped (the
    daemon, and `settings.json`, which is left unwired here);
  - the **health check's direct HTTPS probe**: `status=healthy`,
    `direct_ask {ok: true, latency_ms: 1125}`.
- **Install then uninstall**, for real: release copy, junction created,
  `current.txt`, launcher, mode file `shadow`, `settings.json` written with a
  timestamped backup; re-running reported `already wired ... no change`; and
  `--purge` removed the hook entry (only airlock's own), the junction, the
  pointer, the launcher, the releases, then state and config.

## Still open, and why

- `belay`, `compaction`, `browser`, `review` and `tuning` are **not ported**.
- The **warm daemon** has no Windows transport, and will not get one until
  a named pipe can be done without a non-stdlib dependency. A localhost TCP
  listener is the design this project refuses.
- **`filesearch/` the index *builder*** is Linux-only on purpose. Everything
  is third-party software with its own installer and service; airlock detects
  and steers, and never installs.
- `deploy.sh`, `rollback.sh` and `wire.sh` stay bash and Linux-only. Their
  Windows jobs are done by `windows_install.py` and `current.txt`.
- **The enforce-mode budget on Windows is 2000 ms, decided and applied.**
  32 judged calls on that machine: median **1030 ms**, min 953, p95 1092,
  max **1359 ms**. **One** of the 32 died on a TLS handshake timeout
  (`_ssl.c:989: The handshake operation timed out`) and **fail-opened**, a
  3.1% fail-open rate, against the Linux-tuned 1500 ms budget. Windows has no
  warm daemon, so every judgement pays a fresh TLS handshake, unlike POSIX,
  WSL and macOS, which keep 1500 ms because the daemon is there. The owner's
  decision is a Windows-specific default of **2000 ms**
  (`airlock/enforce.py:WINDOWS_DEFAULT_BUDGET_MS`, selected by
  `platform_compat.is_windows()`): it trades a slower worst case for fewer
  silent allows, comfortably clears the measured 1359 ms max, and still
  leaves 3 s of headroom under the 5 s PreToolUse hook timeout wired in
  `settings.json`. `AIRLOCK_BUDGET_MS` (and its legacy names) still overrides
  this on every platform, exactly as before.
- **macOS is still untested**, and its row in the README is unchanged.

## One number worth knowing before you measure anything there

The hook entry point takes roughly **450 ms** on Windows against roughly
**40 ms** on Linux, for the same code. Almost all of it is CPython start-up
plus the antivirus filter in front of every `CreateProcess`. Nothing in this
repository moves it, and every PreToolUse hook on that machine pays it, which
is why the entry-point budget is 800 ms there.
