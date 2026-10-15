# shellcheck shell=bash
# Shared helpers for install.sh and uninstall.sh (ADR-0008, ADR-0010).
# Sourced, never executed. Every path here must be listed in docs/audit/FILE_INVENTORY.md.

readonly PREFIX=/opt/monfile
readonly PREFIX_NEW=/opt/monfile.new      # transient: the next tree, built before the swap
readonly PREFIX_OLD=/opt/monfile.old      # transient: the previous tree, during the swap
readonly CONF_DIR=/etc/monfile
readonly CONF_FILE=$CONF_DIR/monfile.toml
readonly STATE_DIR=/var/lib/monfile
readonly UNIT_DIR=/etc/systemd/system
readonly TMPFILES_CONF=/etc/tmpfiles.d/monfile.conf
readonly UNITS=(monfile.service monfile-scan.service monfile-scan.timer)
readonly SVC_USER=monfile
readonly SVC_GROUP=monfile
readonly SYSLOG_TAG=monfile-install

DRY_RUN=0
_SYSLOG=0

# --- logging ---------------------------------------------------------------
# Every message goes to stderr and, unless this is a dry run, to syslog
# (`logger -t monfile-install`), so installs show up in the journal and the SIEM.
# Never log file contents (AGENTS.md rule 9).

syslog_init() {
    if (( DRY_RUN )); then
        _SYSLOG=0
    elif command -v logger >/dev/null 2>&1; then
        _SYSLOG=1
    else
        _SYSLOG=0
        printf '%s: warning: logger not found; this run is not recorded in syslog\n' "$SYSLOG_TAG" >&2
    fi
}

_emit() {  # _emit <priority> <prefix> <message>
    printf '%s: %s%s\n' "$SYSLOG_TAG" "$2" "$3" >&2
    if (( _SYSLOG )); then
        logger -t "$SYSLOG_TAG" -p "user.$1" -- "$2$3" || true
    fi
}

log()  { _emit notice "" "$*"; }
warn() { _emit warning "warning: " "$*"; }
die()  { _emit err "error: " "$*"; exit 1; }

# --- actions ---------------------------------------------------------------

# run CMD ARGS...: run a command that changes the system, or just print it under --dry-run.
run() {
    if (( DRY_RUN )); then
        printf '%s: [dry-run] %s\n' "$SYSLOG_TAG" "$*" >&2
        return 0
    fi
    "$@"
}

# write_file PATH OWNER:GROUP MODE: write stdin to PATH via a temp file in the same dir,
# then rename it into place, so PATH is never left half-written.
write_file() {
    local path=$1 owner=$2 mode=$3 tmp
    if (( DRY_RUN )); then
        printf '%s: [dry-run] write %s (%s %s)\n' "$SYSLOG_TAG" "$path" "$owner" "$mode" >&2
        cat >/dev/null
        return 0
    fi
    tmp=$(mktemp "$(dirname "$path")/.$(basename "$path").XXXXXXXX")
    cat >"$tmp"
    chown "$owner" "$tmp"
    chmod "$mode" "$tmp"
    sync -- "$tmp"
    mv -f -- "$tmp" "$path"
}

# --- checks ----------------------------------------------------------------

require_root() {
    if (( EUID != 0 )); then
        if (( DRY_RUN )); then
            warn "not running as root; the dry run may differ from a real run"
        else
            die "must run as root (try: sudo $0)"
        fi
    fi
}

require_cmds() {
    local c missing=()
    for c in "$@"; do
        command -v "$c" >/dev/null 2>&1 || missing+=("$c")
    done
    (( ${#missing[@]} == 0 )) || die "required commands not found: ${missing[*]}"
}

require_systemd() {
    [[ -d /run/systemd/system ]] || die "systemd is not the init system on this host"
}

# refuse_symlink PATH...: AGENTS.md rule 4. A managed path that is a symlink could redirect
# root-owned writes elsewhere, so refuse it instead of following it.
refuse_symlink() {
    local p
    for p in "$@"; do
        [[ -L $p ]] && die "$p is a symlink; refusing to follow it"
    done
    return 0
}

# Hold an exclusive lock on the running script for the whole run, so two installs or
# uninstalls cannot interleave. Uses no extra path on disk.
take_lock() {
    exec 9<"$0"
    flock -n 9 || die "another install.sh or uninstall.sh is already running"
}

# --- systemd ---------------------------------------------------------------

unit_active() { systemctl is-active --quiet "$1" 2>/dev/null; }

# --- fapolicyd -------------------------------------------------------------

fapolicyd_active() { unit_active fapolicyd.service; }

# Push the trust DB to the running daemon. Without a running daemon there is nothing to push;
# it reads the trust files when it starts.
fapolicyd_reload() {
    if fapolicyd_active; then
        run fapolicyd-cli --update
    else
        log "fapolicyd is not running; its trust DB will be loaded when it starts"
    fi
}

# fapolicyd_untrust: drop every trust entry under $PREFIX. A missing entry is not an error.
fapolicyd_untrust() {
    if (( DRY_RUN )); then
        run fapolicyd-cli --file delete "$PREFIX/"
    else
        fapolicyd-cli --file delete "$PREFIX/" >/dev/null 2>&1 || true
    fi
}

# fapolicyd_trust: replace the trust entries for $PREFIX with the current file hashes.
# Delete-then-add also drops entries for files removed by an upgrade.
fapolicyd_trust() {
    fapolicyd_untrust
    run fapolicyd-cli --file add "$PREFIX/"
    fapolicyd_reload
}
