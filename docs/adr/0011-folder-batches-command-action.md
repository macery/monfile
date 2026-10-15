# ADR-0011: Folder batches, the `command` action, and a configurable service account

- **Status:** Accepted
- **Date:** 2026-10-15
- **Supersedes:** ADR-0006

## Context
A customer's cross-domain host receives WSUS update batches over `scp` from a Windows machine. An existing, approved bash script signs, splits, and moves each batch across the domain. Today cron runs it with no arguments and hard-coded paths, with no logging or diagnostics. The customer wants:
- the script to run only once the **whole folder** has settled (a batch is a set of files that belong together);
- monfile's audit trail around each run;
- the service to run as the existing account `ast_low`. It is an interactive login without `sudo`. It already holds the cross-domain permissions and signing keys, and has an approved procedure. No new account is to be created.

ADR-0006 ruled out external commands in v1. It also listed the controls any future exec action must meet (DESIGN §6.4 at the time). The use case was captured with the project owner on 2026-10-15. The owner approved the proposal and its recommended defaults ("go ahead and build it"), recorded as DESIGN §13 Q16–Q21.

## Decision
1. **Folder trigger mode** (`trigger = "folder"`, DESIGN §4.6). The folder is a unit. It is READY when its listing (path, size, mtime, inode of every candidate) has been unchanged for `settle_seconds`. There is at most one run per folder at a time. The run is polled each cycle, so other folders keep being served. A failed run is held until the listing changes or an operator runs `monfile retry <folder>`. A busy exit is retried after 300 s. An interrupted run settles again. Folder batches need the daemon.
2. **`command` action** (DESIGN §6.4), allowed only as the single action of a batch folder. It meets every control ADR-0006 required:
   - The program is an absolute path in the root-owned config. It is checked at load and **before every run**: regular, executable, root-owned, not group/world-writable, no symlink in its path, and every directory above it root-owned and not group/world-writable (SR-13).
   - `Popen([program, *args], shell=False)`. `args` are fixed strings; file names are never placed in argv. The batch's files are passed in a NUL-separated list file.
   - A fixed, minimal environment, never inherited.
   - A timeout (default 2 h): SIGTERM to the program's process group, then SIGKILL after 30 s.
   - The same service account and systemd sandbox as monfile.
   - Output is captured line by line, escaped, capped (1,000 characters per line, 10,000 lines per run), and logged.
   - The program's SHA-256 is logged and stored with each run, along with every batch file's SHA-256 (`hash_batch`, default on).
3. **AGENTS.md rule 3 is amended:** `subprocess` is permitted only in the `command` action, with an argument list and `shell=False`. `os.system`, `eval`, `exec`, and `shell=True` remain forbidden everywhere.
4. **Configurable service account.** `install.sh --run-as USER` uses an existing, non-root account (and its primary group) instead of creating `monfile`. It writes a `User=`/`Group=` drop-in for both service units and installs the config as `root:<group> 0640`. The choice is remembered across upgrades. The default is unchanged.
5. **Sandbox loosening is per host.** The program inherits the service sandbox (no network, no `/home`, no privilege gain, read-only filesystem except `ReadWritePaths=`). A program that needs more gets a host-owner-approved drop-in (example in `docs/examples/command-sandbox.conf`). Running the program as a separate systemd unit was rejected for v1: an unprivileged account needs polkit to start units, polkit's configuration differs across the four target OSes, and monfile would lose direct sight of the exit code, output, and timeout.
6. **`COMMAND_FAIL` is `error`**, consistent with `ACTION_FAIL`. The customer said warning was acceptable; error lets a SIEM alert on failed transfers without special cases.

## Consequences
- \+ Replaces an unlogged cron job with settle detection, per-run audit events, manifests, timeouts, and a known program hash.
- \+ The config still can't run arbitrary code: only a program that root installed and controls can run.
- − The trust boundary now includes the program. Its behavior (what it reads, writes, sends, and prints) is outside monfile's control and must be approved by the host owner.
- − With `--run-as` an interactive account, anyone who can log in as it can alter state and manifests. Syslog forwarded off-host is then the authoritative record (SECURITY.md).
- − Loosening the sandbox for the program also loosens it for monfile on that host.
- − Without a sender-side "batch complete" signal, a transfer that stalls longer than the quiet window is processed as complete.
