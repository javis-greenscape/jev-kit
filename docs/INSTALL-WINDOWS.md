# Install on native Windows (no WSL)

For a person and for an AI agent. Everything below runs on Windows itself:
no WSL, no Linux VM, no bash unless you happen to have Git for Windows.

**What you get:** the Airlock guard on `PreToolUse`, in shadow mode, with the
rules table, the key file, the health check, a doctor and an uninstaller, and
file search steered at [voidtools Everything](https://www.voidtools.com/)
where Linux uses `plocate`.

**What you do not get, and why.** These are out of scope on Windows in this
release, not broken:

| Not on Windows | Why |
|---|---|
| The warm-connection daemon | It listens on a **Unix domain socket**, which Windows does not have. Every judgement is the direct HTTPS call instead: about **0.9 s** cold against about **0.3 s** through a warm daemon on Linux. A named pipe is the right analogue but needs a non-stdlib dependency; a localhost TCP listener is stdlib-only and is precisely the design this project refuses. |
| `belay` (the Stop-hook verifier) | Not ported. It clones and wraps a third-party repository. |
| `compaction` | Not ported, and it is the component that sends the most off the machine. |
| `browser`, `review` | Not ported. Both clone third-party repositories. |
| `tuning` | Not ported. It drives real Claude sessions through shell scripts. |
| `filesearch/` (the index **builder**) | On Linux airlock builds the index. On Windows it does not and must not: Everything is third-party software with its own installer and service. airlock **detects** it and steers at it. |

---

## What you need first

1. **Python 3.8 or later, from [python.org](https://www.python.org/downloads/).**
   Not the Microsoft Store stub: the `python.exe` the Store puts on `PATH` is
   an app-execution alias that opens the Store rather than running anything,
   and the installer deliberately refuses to put it in a hook command. The
   `py` launcher that the python.org installer puts in `C:\Windows` is what
   the launchers below look for first.

2. **Claude Code**, installed and working.

3. **[Git for Windows](https://git-scm.com/downloads/win) — recommended, not
   required.** It decides which shell your hook command has to be written
   for, so it changes what the installer writes. Claude Code's setup
   documentation puts it plainly:

   > Git for Windows is recommended on native Windows so Claude Code can use
   > the Bash tool. If Git for Windows is not installed, Claude Code uses
   > PowerShell as the shell tool instead.

   The guard covers both tools either way. **But if you install Git for
   Windows later, re-run the installer with `--wire`**, because the hook
   command has to change shape: `"py.exe" "hook.py"` is a valid command in
   bash and is *not* one in PowerShell, which needs the call operator
   (`& "py.exe" "hook.py"`). The installer detects which you have and writes
   the matching form.

4. **A `TYPESAFE_API_KEY`** — the one thing a human has to supply. See below.
   Without it the guard still installs and still fails open; it simply judges
   nothing, so only the code-only rules fire.

5. **Optional: [voidtools Everything](https://www.voidtools.com/) and its ES
   command-line client.** Two separate downloads, and conflating them is the
   usual confusion:
   - the **Everything application** maintains the index (a GUI plus a
     service);
   - the **ES command-line client** is a single `es.exe`, downloaded
     separately from the same site under *Downloads > Command-line
     Interface*.

   A machine can have the first and not the second. Put `es.exe` anywhere on
   `PATH`, or set `AIRLOCK_ES_PATH` to its full path. **airlock will not
   install either for you.**

---

## Install

```cmd
git clone https://github.com/jonathanavis96/jev-kit.git %USERPROFILE%\code\jev-kit
cd %USERPROFILE%\code\jev-kit

install\windows-install.cmd --check-only
install\windows-install.cmd
install\windows-doctor.cmd
```

From PowerShell, use the `.ps1` launchers instead:

```powershell
powershell -ExecutionPolicy Bypass -File install\windows-install.ps1 --check-only
```

Or call Python directly, which is all the launchers do:

```cmd
py -3 install\windows_install.py --check-only
```

`--check-only` prints the plan, every path it would use and every
prerequisite still missing, and installs nothing.

**The installer never elevates, never writes outside your user profile, never
touches the registry, `PATH`, or any service, and never edits a
`settings.json` unless you pass `--wire`.** Without `--wire` it prints the
exact JSON to add and stops.

### The one thing it needs from a human

A **TypeSafe API key**, for the Jev judgements.

1. Ask the human for a `TYPESAFE_API_KEY`.
2. They put it in `%APPDATA%\airlock\env`, as one line:

   ```
   TYPESAFE_API_KEY=...
   ```

   Create the file however you like; nothing in airlock needs it to exist
   before the install.

3. **Never print the key.** Not into a terminal somebody is reading, not into
   a file you then display, not into a commit message, not to confirm it.
   `airlock/keyfile.py` reads the value with plain Python file I/O and never
   shells out, so the key never reaches a command line or a log. The doctor
   checks only that the `TYPESAFE_API_KEY=` **line** is present; it never
   reads the value.

A note on file permissions, said plainly rather than papered over: on Linux
airlock `chmod`s the key file to 0600. On Windows there is no equivalent —
`os.chmod` there only toggles the read-only attribute and cannot express
"owner only" — so **airlock does not chmod it and does not pretend to**. The
file's privacy comes from `%APPDATA%` being a per-user directory with a
Windows ACL that already denies other non-administrative users. The doctor
reports it that way, as an ACL claim rather than a mode check.

### Wire it up

```cmd
py -3 install\windows_install.py --wire
```

That edits `%USERPROFILE%\.claude\settings.json`, **backing it up to a
timestamped sibling first**. Pass `--settings <path>` (repeatable) to wire
somewhere else as well.

Add `--schedule-health` to register an hourly health check with Task
Scheduler. It is off by default: a scheduled task outlives the session that
made it, so it is opt-in exactly as `--wire` is.

### Verify

```cmd
py -3 install\windows_doctor.py
```

The doctor does not check that files exist. It **runs** a real deny and a
real allow through the actual hook process against a throwaway profile, a
malformed payload, the kill switch, the Windows file-search classification,
Everything detection and the health check, and reports what came back. Exit 0
when everything installed works. Anything not installed is `skip`, never
`FAIL`, and the daemon is `skip` permanently.

To prove a deny with your own hands, pipe a fake PreToolUse event at the
launcher:

```cmd
echo {"session_id":"t","cwd":"C:\\","tool_name":"Bash","tool_input":{"command":"xdg-open https://example.com"}} | py -3 "%LOCALAPPDATA%\airlock\airlock-hook.py"
```

with `AIRLOCK_MODE=enforce` set. That must print JSON containing
`"permissionDecision": "deny"`. An ordinary command must print **nothing at
all**.

---

## Where everything lives

| What | Where |
|---|---|
| Config: mode, `rules.json`, the key file | `%APPDATA%\airlock\` |
| State: the shadow log, loop protection | `%LOCALAPPDATA%\airlock\state\` |
| Releases and the `current` pointer | `%LOCALAPPDATA%\airlock\` |
| The stable hook launcher | `%LOCALAPPDATA%\airlock\airlock-hook.py` |

Every environment override still works and still wins:
`AIRLOCK_CONFIG_DIR`, `AIRLOCK_STATE_DIR`, `AIRLOCK_HOME`,
`AIRLOCK_KEY_FILE`, `AIRLOCK_MODE`, `AIRLOCK_DISABLE`.

### How `current` works without symlink privilege

On Linux, `current` is a symlink and a deploy or rollback is one atomic
`rename`. Windows does not hand symlink privilege to an ordinary user
(`os.symlink` fails with *WinError 1314: A required privilege is not held by
the client* — measured, not assumed), so that mechanism is not available.
There are three moving parts instead:

1. **A directory junction** (`mklink /J`), when the volume allows one. A
   junction needs **no privilege at all**, unlike a symlink, and once made,
   `...\airlock\current\hooks\airlock.py` behaves exactly like the Linux
   symlink. It succeeded on the Windows 11 machine this was tested on. It is
   NTFS-only and cannot point at a network location, which is why it is not
   the only mechanism.

2. **`current.txt`**, a one-line text file holding the absolute path of the
   live release. Always written, junction or not, because it cannot fail: no
   filesystem feature, no privilege, no volume type.

3. **`airlock-hook.py`**, a small stable launcher in the install root.
   `settings.json` points **here**, never at a release directory. It resolves
   `current` (junction first, then `current.txt`) and runs the real hook **in
   the same process**, so the indirection costs one small file read rather
   than a second interpreter start-up.

The point of the third part is that `settings.json` never has to change
again. A rollback is one line of `current.txt`, and the next tool call picks
it up — the same promise the Linux symlink makes.

---

## File search: Everything instead of plocate

On Linux, R8 steers a disk-wide filename search at the `plocate` index
airlock installs. On Windows the same rule steers at Everything, which keeps
a live NTFS index of its own:

```
es.exe -path "<folder>" -n 50 "<pattern>"
```

- `-path <dir>` confines the search to one folder and its subfolders
- `-n <count>` stops after N results
- `-r` treats the pattern as a regular expression
- `-i` **matches case**. `es` is case-**in**sensitive by default, so `-i`
  makes a search *stricter*, not looser — the opposite of `grep -i`, and the
  one flag that is easy to get backwards.

The results are instant because they come from an index, not a crawl.

The guard recognises these as disk-wide searches on Windows, through the Bash
tool and the PowerShell tool alike: `dir /s`, `where /r`, `findstr /s`,
`Get-ChildItem -Recurse` (and its `gci` / `ls` / `dir` aliases),
`Select-String -Path`, plus the ordinary `find` and `grep` a Git Bash session
still issues. `cmd.exe /c ...` and `powershell -Command ...` wrappers are
unwrapped and the inner command is classified. A command that already uses
`es` is never told to use `es`.

All three spellings of a path compare equal and case-insensitively:
`C:\Users\alice`, `C:/Users/alice` and Git Bash's `/c/Users/alice`. A drive
root (`C:\`) or a UNC share root is disk-wide by definition.

---

## Modes, kill switch, rollback, uninstall

```cmd
echo shadow  > "%APPDATA%\airlock\mode"    :: the default: log only, block nothing
echo enforce > "%APPDATA%\airlock\mode"    :: a deny now blocks
echo off     > "%APPDATA%\airlock\mode"    :: complete no-op
```

Kill switch, any one of which makes the hook do nothing:

```cmd
set AIRLOCK_DISABLE=1                      :: this shell, immediately
type nul > "%APPDATA%\airlock\disabled"    :: this machine, until removed
echo off > "%APPDATA%\airlock\mode"
```

Rollback: edit `%LOCALAPPDATA%\airlock\current.txt` to name an older
directory under `releases\`. The next tool call follows it; `settings.json`
needs no edit.

Uninstall:

```cmd
py -3 install\windows_uninstall.py --check-only
py -3 install\windows_uninstall.py
py -3 install\windows_uninstall.py --purge
```

It removes **only airlock's own** `PreToolUse` entry, backing each
`settings.json` up first, then the scheduled task, the launcher, the pointer,
the junction and the releases. **Config and state survive by default** — the
config directory holds your key file and the state directory holds the shadow
log, which is the whole record of what the guard would have done. `--purge`
removes those too and says what it removed.

---

## What was actually tested, and on what

Honesty about coverage, because "it should work" is not verification.

| Claim | How it was established |
|---|---|
| The unit suite passes on Windows | **Run on the real thing**: Windows Python 3.11.9 on a Windows 11 workstation. 659 tests, OK, 84 skipped. The skips are tests that assert something only POSIX has (a Unix socket, a `chmod` mode, the XDG layout, a bash script, a fixture that builds symlinks); each prints its reason. |
| A deny works | **Piped a real PreToolUse event** at the installed launcher, through the Bash tool and through the PowerShell tool. Both returned the documented deny JSON, exit 0. |
| An allow works | Same, with an ordinary command: zero bytes on stdout and stderr, exit 0. |
| Fail-open works | A malformed payload: zero bytes, exit 0. The kill switch: zero bytes, exit 0. |
| The install works | Ran the real installer into a scratch directory: release copy, directory junction (it succeeded), `current.txt`, launcher, mode file, and a `settings.json` written with a backup. Re-running reported "already wired" and changed nothing. |
| The doctor works | 11 passed, 0 failed, 2 skipped (the daemon, and the key file, which was deliberately absent). |
| Everything detection works | `es.exe` found at a real path and reported; the running index detected; `es.exe -path "C:\Windows\System32" -n 3 notepad.exe` returned three real results in the expected shape. ES version 1.1.0.38. |
| The file-search steer is right | `dir /s C:\ *.xlsm` classified `disk_wide`, program `dir`, root `C:\`, and the deny text names `es.exe` with the flags above. A command already using `es` was allowed. |
| **A Jev-judged deny** | **Verified on Linux only.** The Windows machine was tested **keyless on purpose** — no API key was copied to it — so every judged path there fail-opened, which is what the fail-open evidence above shows. The code-only rules (R5, R6, R7, R3, R9) need no key and no network, and those are what the Windows deny proof used. |
| The daemon | **Not tested and not present.** It does not run on Windows at all. |

The hook entry point takes roughly **450 ms** on that Windows machine against
roughly **40 ms** on Linux, for the same code doing the same work. Almost all
of it is CPython start-up plus the antivirus filter in front of every
`CreateProcess`; no change to this repository moves it, and it is a cost every
PreToolUse hook on a Windows machine pays, not just this one.

---

## If you are an AI agent

1. Read this file and `install/README.md`. Do not start by reading the whole
   repository.
2. Run `py -3 install\windows_install.py --check-only` and report the plan and
   every prerequisite warning to the human **before installing anything**.
3. **Ask the human for the `TYPESAFE_API_KEY`** and tell them where to put it.
   Do not ask them to paste it into your session. If they do anyway, do not
   echo it back, do not write it into a file you then display, and say plainly
   that it should be rotated.
4. Run the installer, then the doctor, and report the **real** output. A
   download that succeeded is not a capability that works.
5. **Before wiring, back up every `settings.json` you are about to touch.**
   `--wire` does this for you; if you edit by hand, do it yourself first.
6. Use **absolute paths** in the hook command, and let the installer write it:
   it picks the bash or the PowerShell shape from whether Git for Windows is
   present, and getting that backwards produces a hook that silently never
   runs.
7. **Prove a deny works** with the piped fake event above before telling
   anyone the install is done.
8. Leave it in `shadow`. Arming it is the human's decision.
9. Do not install Everything or `es.exe` for them. Report that it is missing,
   say it is a separate download, and let them decide.
