# monfile HOWTO: set up and use

This guide takes you from a downloaded release to a working watch folder, then covers everyday use.
For reference material, see [docs/OPERATIONS.md](docs/OPERATIONS.md) (troubleshooting, upgrades), [config/monfile.example.toml](config/monfile.example.toml) (every config option) and [DESIGN.md](DESIGN.md) (how it works).

> **Status (0.3.0):** the program is implemented, unit-tested, and manually tested on Ubuntu 24.04. It has not yet been tested on RHEL 8/9 or Ubuntu 22.04. Please report anything in this guide that doesn't match what you see.

---

## 1. What monfile does

You point monfile at one or more **watch folders**. When a file in a watch folder has stopped changing for a **settle window** (5 minutes by default), monfile:
1. Runs that folder's **workflow**, such as copying the file to an outbound folder. Every copy is verified with SHA-256.
2. Applies the folder's **disposition** to the original: leave it, move it to a processed folder, or delete it.
3. Logs every step to syslog/journald, and optionally writes a JSON **manifest** per file.

monfile runs as its own unprivileged `monfile` account and never needs root once it is installed.

## 2. Before you start

You need:
- [ ] A RHEL 8/9 or Ubuntu 22.04/24.04 host, with `sudo` access for the install
- [ ] Python 3.11 or newer from the OS repositories:

  | OS | Command |
  |---|---|
  | RHEL 8 / 9 | `sudo dnf install python3.11` |
  | Ubuntu 22.04 | `sudo apt install python3.11` (from universe) |
  | Ubuntu 24.04 | nothing to install; `python3` is 3.12 |

- [ ] The watch folders on **local disk**
- [ ] To know, for each watch folder: who writes files into it, where the files should go, and what should happen to the original
- [ ] Your SIEM receiving this host's syslog. monfile alerts only through syslog (section 9).

## 3. Install

```sh
# 1. Download monfile-<version>.tar.gz and SHA256SUMS from the GitHub release page, then verify:
sha256sum -c SHA256SUMS                 # must print: monfile-<version>.tar.gz: OK

# 2. Unpack and install
tar -xzf monfile-<version>.tar.gz
cd monfile-<version>
sudo ./packaging/install.sh --dry-run   # optional: see every change first
sudo ./packaging/install.sh
```

The installer:
- checks every file against the release's `MANIFEST.sha256`, and stops if anything was changed
- creates the `monfile` system account
- installs the program under `/opt/monfile`
- puts a starter config at `/etc/monfile/monfile.toml`, only if one doesn't already exist
- installs the systemd units without enabling them
- adds fapolicyd trust automatically on RHEL hosts where fapolicyd is running
- logs each step to syslog (`journalctl -t monfile-install`)

Running a newer release's `install.sh` upgrades in place. It restarts whatever was running and keeps your config and state.

Every installed path is listed in [docs/audit/FILE_INVENTORY.md](docs/audit/FILE_INVENTORY.md).

Check it:
```sh
/opt/monfile/bin/monfile --version
```

## 4. Set up your first watch folder (worked example)

**Scenario:** an upload process (group `xfer`) drops reports into `/data/inbound/reports`. Each report should be copied to `/data/outbound/reports` for a downstream system (also group `xfer`), and the original should be archived in `/data/processed/reports`.

### 4.1 Create the folders and grant access
monfile needs to read the watch folder, write to the destination, and write to the processed folder. Because this folder moves the originals out, monfile also needs write access to the watch folder.

```sh
sudo mkdir -p /data/inbound/reports /data/outbound/reports /data/processed/reports
sudo chgrp xfer /data/inbound/reports /data/outbound/reports /data/processed/reports
sudo chmod 2770 /data/inbound/reports /data/outbound/reports /data/processed/reports   # 2 = setgid

# Give the monfile account access without adding it to the xfer group:
sudo setfacl -m g:monfile:rwx /data/inbound/reports /data/outbound/reports /data/processed/reports
```

The setgid bit (`2…`) on the destination matters. monfile always delivers files as mode `0640` owned by `monfile`, and setgid makes them take the folder's group (`xfer`), so the downstream system can read them.

> **RHEL (SELinux):** monfile runs as `unconfined_service_t`, so default labels such as `default_t` or `var_t` normally work. If access fails, check `sudo ausearch -m avc -ts recent`.

### 4.2 Write the config
Edit `/etc/monfile/monfile.toml`. Keep the `[global]` section from the starter file, and replace the example folders with the block below. The starter file's example folders are set to `enabled = false`, so until you do this the service runs but watches nothing. `[[folder]]` is a fixed section name; the folder's own name goes in `id`.

```toml
[[folder]]
id             = "reports"
path           = "/data/inbound/reports"
settle_seconds = 300                       # unchanged for 5 minutes before processing
manifest       = true                      # write a JSON record per file (recommended while setting up)
on_success     = "move"                    # archive the original after a successful copy
processed_dir  = "/data/processed/reports"

  [[folder.action]]
  type        = "copy"
  destination = "/data/outbound/reports"
  collision   = "suffix"                   # if the name exists, add a timestamp instead of overwriting
```

Rules that `validate-config` enforces:
- Paths are absolute and contain no symlinks.
- Destination and processed folders are **outside** the watch folder.
- `settle_seconds` is at least twice `scan_interval_seconds`, which is 30 s by default.
- Unknown keys are errors, so typos are caught.

### 4.3 Let the service write to those folders
The service runs inside a systemd sandbox that is read-only everywhere by default. List every folder monfile writes to (watch, destination and processed):

```sh
sudo systemctl edit monfile.service
```
```ini
[Service]
ReadWritePaths=/data/inbound/reports /data/outbound/reports /data/processed/reports
```
If you use timer mode (section 5), make the same change with `systemctl edit monfile-scan.service`.

### 4.4 Validate
```sh
sudo -u monfile /opt/monfile/bin/monfile validate-config
```
Fix anything it reports. It also refuses a config that isn't `root:monfile 0640`. Because it runs as `monfile`, it also reports any folder that the service account can't read or write.

> `validate-config` runs outside the systemd sandbox, so it can't detect a missing `ReadWritePaths=` entry. If files fail with `PERMISSION_DENIED` or "Read-only file system" while `validate-config` passes, check the drop-in from §4.3.

## 5. Start it

Pick **one** run mode:

| Mode | Command | Best for |
|---|---|---|
| Daemon | `sudo systemctl enable --now monfile.service` | Regular inflow, lowest latency |
| Timer | `sudo systemctl enable --now monfile-scan.timer` | Occasional drops; nothing runs between scans |

If you enable both, the second one logs `LOCK_BUSY` and exits.

## 6. Try it

Follow the log in one terminal:
```sh
journalctl -t monfile -f
```
Drop a test file as a writer would:
```sh
sudo -u <an xfer user> cp ~/test-report.pdf /data/inbound/reports/
```

Expect this sequence. The `FILE_READY` line appears about 5 minutes after the file stops changing.
```
event=FILE_DETECTED  folder=reports path="/data/inbound/reports/test-report.pdf" size=…
event=FILE_READY     folder=reports path="…" sha256=… settled_seconds=300
event=ACTION_OK      folder=reports path="…" action=copy dest="/data/outbound/reports/test-report.pdf" sha256=…
event=DISPOSITION_OK folder=reports path="…" mode=move target="/data/processed/reports/test-report.pdf"
event=FILE_DONE      folder=reports path="…" sha256=… manifest="/var/lib/monfile/manifests/reports/…json"
```
Then check the results:
```sh
ls -l /data/outbound/reports/        # test-report.pdf, -rw-r----- monfile xfer
ls -l /data/processed/reports/       # the original
sudo cat <manifest path from the FILE_DONE line>   # /var/lib/monfile is readable only by monfile and root
```

**Tip:** to see what would happen without changing anything, run `sudo -u monfile /opt/monfile/bin/monfile scan --once --dry-run`.

## 7. Common recipes

All of these are per-folder settings. Each `[[folder]]` block is independent.

### Writer pauses mid-upload → require a "done" marker
If a writer can stall for longer than the settle window, have it create `<name>.done` after the upload finishes:
```toml
ready_marker = ".done"
```
Writer side:
```sh
cp report.pdf /data/inbound/reports/ && touch /data/inbound/reports/report.pdf.done
```
monfile waits for **both** the marker and the normal settle window, then deletes the marker once processing succeeds. The watch folder needs write access for monfile, plus a `ReadWritePaths=` entry.

### Move instead of copy
```toml
on_success = "leave"            # required: the move action already removes the original

  [[folder.action]]
  type        = "move"
  destination = "/data/staging/reports"
  collision   = "fail"
```
By default a move copies, verifies, then deletes the original. This also gives the delivered file the destination's SELinux label.

### Leave the original where it is
```toml
on_success = "leave"
```
monfile remembers each processed file. If that file changes later, it is processed again as a new version (`FILE_NEW_VERSION`).

### Watch subfolders too
```toml
recursive = true
```
For a `copy` or `move` action, add `preserve_tree = true` to keep the subfolder structure at the destination.

### Very large files
```toml
verify_hash = false
```
This skips the extra SHA-256 pass during the settle window. The delivered copy is still verified. Files over 10 GiB log a `FILE_LARGE` warning but are processed normally.

### Ignore empty files
```toml
skip_empty = true               # zero-byte files are logged as FILE_SKIPPED and not processed
```

### Ignore partial files
These patterns are ignored by default: `.*`, `*.part`, `*.tmp`, `~$*`, `*.crdownload` and `*.filepart`. If your writer uploads to a temporary name and renames when finished, add its pattern:
```toml
exclude = [".*", "*.part", "*.tmp", "~$*", "*.crdownload", "*.filepart", "*.uploading"]
```
Setting `exclude` replaces the default list, so keep the defaults in it.

### Pause a folder without deleting it
```toml
enabled = false                 # default true; the block is still syntax-checked, but nothing is scanned
```
Restart the service to apply it. Startup logs `FOLDER_DISABLED` for the folder. Files already tracked keep their state. To resume, set it back to `true` (or remove the line) and restart.

### Add a second folder
Add another `[[folder]]` block with a unique `id`, then:
1. Add its writable folders to `ReadWritePaths=`.
2. Run `validate-config`.
3. Restart the service (section 8).

## 8. Everyday use

| Task | Command |
|---|---|
| Watch activity | `journalctl -t monfile -f` |
| Show failures | `journalctl -t monfile \| grep -E 'event=(ACTION_FAIL\|INTEGRITY_MISMATCH\|DISPOSITION_FAIL)'` |
| List tracked files and their states | `sudo -u monfile /opt/monfile/bin/monfile status` |
| Retry a failed file after fixing the cause | `sudo -u monfile /opt/monfile/bin/monfile retry <folder_id> <path relative to the watch folder>` |
| Apply a config change | `validate-config`, then `sudo systemctl restart monfile.service` (or `monfile-scan.timer`) |

Things to know:
- **Config changes need a restart.** monfile reads its config only at startup, and reload and SIGHUP do nothing.
- **A failed file is left alone.** The original stays in place and isn't retried automatically. It is retried when the file changes or when you run `retry`. Steps that already succeeded are not repeated.
- **Nothing is ever overwritten.** On a name collision, `suffix` adds a timestamp, `skip` accepts an identical existing file, and `fail` stops.
- **Records are kept forever.** monfile never deletes its state database or manifests. Watch disk use in `/var/lib/monfile`, and archive it according to your site's retention policy.
- **Processed originals are kept too.** With `on_success = "move"`, every original stays in `processed_dir` alongside the delivered copy, and monfile never removes it. If you don't need a second copy, use a `move` action or `on_success = "delete"` instead (the manifest and log still record the SHA-256). Automatic expiry is a roadmap item (DESIGN §14, R1).

## 9. Alerting

monfile alerts only through syslog. Set up SIEM alerts on these events:
- `ACTION_FAIL`
- `INTEGRITY_MISMATCH`
- `DISPOSITION_FAIL`
- `PERMISSION_DENIED`
- `CONFIG_INVALID`

`MARKER_ORPHAN` and `FILE_LARGE` are worth a lower-priority alert. The full event list is in [docs/audit/AUDIT_EVENTS.md](docs/audit/AUDIT_EVENTS.md).

## 10. When something goes wrong

| Symptom | First check |
|---|---|
| File never processed | Is its name excluded? Is it still changing (`FILE_CHANGED_DURING_SETTLE`)? Is it a symlink (`SYMLINK_REJECTED`)? Is a `ready_marker` missing? Run `status`. |
| `PERMISSION_DENIED` | The ACLs from section 4.1, the `ReadWritePaths=` drop-in, and SELinux denials (`ausearch -m avc -ts recent`) |
| Service won't start | `journalctl -u monfile.service`. Look for `CONFIG_INVALID` or `CONFIG_PERMS_INSECURE`. |

More is in [docs/OPERATIONS.md §6](docs/OPERATIONS.md). For upgrades and uninstalling, see OPERATIONS §7.
