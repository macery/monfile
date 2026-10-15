#!/usr/bin/env bash
# monfile installer (ADR-0008, ADR-0010). Every path touched is listed in
# docs/audit/FILE_INVENTORY.md. Run as root from the unpacked tarball root, after verifying
# the tarball with `sha256sum -c SHA256SUMS` (OPERATIONS §2).
#
# Also performs upgrades: the config and state are preserved, and units that were running
# are stopped for the swap and started again afterwards.
set -Eeuo pipefail
umask 022     # STIG root umask is 077; installed code must be world-readable (FILE_INVENTORY)

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=packaging/lib/common.sh
. "$SRC/packaging/lib/common.sh"

usage() {
    cat <<EOF
Usage: sudo $0 [options]

Install or upgrade monfile from this unpacked release tarball.

Options:
  --python PATH           interpreter to use (default: first of python3.12, python3.11,
                          python3 on PATH that is >= 3.11)
  --tarball-sha HEX       SHA-256 of the release tarball, recorded in $PREFIX/VERSION
  --fapolicyd             add fapolicyd trust even if fapolicyd is not running
  --no-fapolicyd          never add fapolicyd trust (default: add it if fapolicyd is running)
  --allow-unsupported-os  install on an OS other than RHEL 8/9 or Ubuntu 22.04/24.04
  --dry-run               print every change instead of making it
  -h, --help              show this help
EOF
}

PY_OPT=""
TARBALL_SHA=""
FAPOLICYD=auto
ALLOW_UNSUPPORTED_OS=0

while (( $# )); do
    case $1 in
        --python)               PY_OPT=${2:?--python needs a path}; shift ;;
        --python=*)             PY_OPT=${1#*=} ;;
        --tarball-sha)          TARBALL_SHA=${2:?--tarball-sha needs a value}; shift ;;
        --tarball-sha=*)        TARBALL_SHA=${1#*=} ;;
        --fapolicyd)            FAPOLICYD=yes ;;
        --no-fapolicyd)         FAPOLICYD=no ;;
        --allow-unsupported-os) ALLOW_UNSUPPORTED_OS=1 ;;
        --dry-run)              DRY_RUN=1 ;;
        -h|--help)              usage; exit 0 ;;
        *)                      usage >&2; exit 2 ;;
    esac
    shift
done

STAGE=""          # private, root-owned copy of the verified release files
STOPPED=()        # units this run stopped, to start again after the swap
PY=""
PY_VERSION=""
VERSION=""
PREV_VERSION=""

PREV_RUNNING=()   # copy of STOPPED, for the summary

# On any exit: remove the stage, and say loudly if units are still stopped.
cleanup() {
    [[ -n $STAGE && -d $STAGE ]] && rm -rf -- "$STAGE"
    if (( ${#STOPPED[@]} )); then
        _emit err "error: " "these units were stopped and NOT restarted: ${STOPPED[*]}"
    fi
    return 0
}

on_error() {
    local rc=$?
    _emit err "error: " "install failed (exit $rc at install.sh line $1)"
    exit "$rc"
}
trap cleanup EXIT
trap 'on_error $LINENO' ERR

# --- 1. Preflight -----------------------------------------------------------

check_os() {
    local id like ver
    [[ -r /etc/os-release ]] || die "/etc/os-release not found; cannot identify the OS"
    IFS='|' read -r id like ver < <(
        # shellcheck disable=SC1091
        . /etc/os-release && printf '%s|%s|%s\n' "${ID:-}" "${ID_LIKE:-}" "${VERSION_ID:-}"
    )
    case "$id:${ver%%.*}:$ver" in
        rhel:8:*|rhel:9:*|ubuntu:*:22.04|ubuntu:*:24.04)
            log "OS: $id $ver" ;;
        *)
            if [[ " $like " == *" rhel "* && ${ver%%.*} =~ ^(8|9)$ ]]; then
                warn "OS: $id $ver is RHEL-compatible but not a tested target"
            elif (( ALLOW_UNSUPPORTED_OS )); then
                warn "OS: $id $ver is not supported; continuing (--allow-unsupported-os)"
            else
                die "OS: $id $ver is not supported (RHEL 8/9, Ubuntu 22.04/24.04); see --allow-unsupported-os"
            fi ;;
    esac
}

# py_ok PATH: true if PATH is a root-owned, non-writable-by-others Python >= 3.11 that has
# the stdlib modules monfile needs, and SHA-256 works (FIPS mode).
py_ok() {
    local p=$1 real owner mode
    [[ $p =~ ^/[A-Za-z0-9._/+-]+$ && -x $p ]] || return 1
    real=$(readlink -f -- "$p") || return 1
    read -r owner mode < <(stat -c '%u %a' -- "$real")
    if [[ $owner != 0 ]] || (( 8#$mode & 8#022 )); then
        warn "skipping $p: $real is not root-owned or is group/other-writable"
        return 1
    fi
    "$p" -I -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null || return 1
    "$p" -I -c 'import hashlib, sqlite3, tomllib; hashlib.sha256(b"")' 2>/dev/null || {
        warn "skipping $p: it lacks hashlib/sqlite3/tomllib or SHA-256 failed"
        return 1
    }
}

find_python() {
    local c p
    if [[ -n $PY_OPT ]]; then
        [[ $PY_OPT == /* ]] || die "--python must be an absolute path"
        py_ok "$PY_OPT" || die "--python $PY_OPT is not a usable Python >= 3.11"
        PY=$PY_OPT
    else
        for c in python3.12 python3.11 python3; do
            p=$(command -v "$c" 2>/dev/null) || continue
            if py_ok "$p"; then PY=$p; break; fi
        done
        [[ -n $PY ]] || die "no Python >= 3.11 found (see OPERATIONS §1), or pass --python"
    fi
    PY_VERSION=$("$PY" -I -c 'import platform; print(platform.python_version())')
    log "Python: $PY ($PY_VERSION)"
}

preflight() {
    require_root
    require_cmds install sha256sum find sed cut sort comm mktemp stat readlink flock \
        getent groupadd useradd systemctl systemd-tmpfiles runuser
    require_systemd
    take_lock
    check_os
    [[ -z $TARBALL_SHA || $TARBALL_SHA =~ ^[0-9a-f]{64}$ ]] \
        || die "--tarball-sha must be 64 lowercase hex characters"
    refuse_symlink /opt "$PREFIX" "$PREFIX_NEW" "$PREFIX_OLD" "$CONF_DIR" "$CONF_FILE" \
        "$STATE_DIR" "$TMPFILES_CONF" "${UNITS[@]/#/$UNIT_DIR/}"
    find_python
    if [[ $FAPOLICYD == auto ]]; then
        if fapolicyd_active; then FAPOLICYD=yes; else FAPOLICYD=no; fi
    fi
    if [[ $FAPOLICYD == yes ]]; then
        require_cmds fapolicyd-cli
        log "fapolicyd: trust will be added for $PREFIX/"
    fi
}

# --- 2. Integrity: verify the release files against MANIFEST.sha256 ---------
# The tarball's own SHA256SUMS (checked by the operator) is the trust anchor. The manifest
# inside it catches files changed, added, or removed after extraction. Files are copied into
# a private root-owned stage and verified THERE, so nothing can change between the check and
# the install.

verify_and_stage() {
    local manifest=$SRC/MANIFEST.sha256 line p unlisted
    local -a paths=()
    [[ -f $manifest && ! -L $manifest ]] \
        || die "MANIFEST.sha256 not found in $SRC; install only from a release tarball (build-tarball.sh)"
    if [[ -n $(find "$SRC" -type l -print -quit) ]]; then
        die "the release tree contains symlinks; refusing (re-extract the tarball)"
    fi

    while IFS= read -r line; do
        [[ $line =~ ^[0-9a-f]{64}\ \ ([A-Za-z0-9._/+-]+)$ ]] || die "malformed MANIFEST.sha256 line: $line"
        p=${BASH_REMATCH[1]}
        [[ $p != /* && /$p/ != */../* && /$p/ != */./* && $p != *//* ]] || die "unsafe path in MANIFEST.sha256: $p"
        paths+=("$p")
    done <"$manifest"
    (( ${#paths[@]} )) || die "MANIFEST.sha256 is empty"

    # __pycache__ appears if someone ran the code from the extracted tree; it is never installed.
    unlisted=$(comm -13 <(printf '%s\n' "${paths[@]}" | LC_ALL=C sort) \
        <(find "$SRC" -type f ! -path '*/__pycache__/*' ! -name MANIFEST.sha256 -printf '%P\n' | LC_ALL=C sort))
    [[ -z $unlisted ]] || die "files not in MANIFEST.sha256 (tree was modified after extraction):"$'\n'"$unlisted"

    STAGE=$(mktemp -d -t monfile-install.XXXXXXXX)
    chmod 0700 "$STAGE"
    install -m 0644 "$manifest" "$STAGE/MANIFEST.sha256"
    for p in "${paths[@]}"; do
        [[ -f $SRC/$p ]] || die "missing from the release tree: $p"
        install -D -m 0644 "$SRC/$p" "$STAGE/$p"
    done
    (cd "$STAGE" && sha256sum --quiet --strict -c MANIFEST.sha256) \
        || die "checksum mismatch against MANIFEST.sha256; re-download and re-verify the tarball"

    for p in src/monfile/__init__.py src/monfile/cli.py config/monfile.example.toml \
             packaging/tmpfiles.d/monfile.conf HOWTO.md "${UNITS[@]/#/packaging/systemd/}"; do
        [[ -f $STAGE/$p ]] || die "release is missing $p"
    done

    VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$STAGE/src/monfile/__init__.py")
    [[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*$ ]] || die "cannot read version from the release"
    log "verified ${#paths[@]} files against MANIFEST.sha256 (monfile $VERSION)"
}

# --- 3. Service account ------------------------------------------------------

ensure_account() {
    local nologin uid gid shell group_gid
    for nologin in /usr/sbin/nologin /sbin/nologin ""; do
        [[ -x $nologin ]] && break
    done
    [[ -n $nologin ]] || die "nologin not found"

    if getent group "$SVC_GROUP" >/dev/null; then
        log "group $SVC_GROUP exists"
    else
        log "creating system group $SVC_GROUP"
        run groupadd -r "$SVC_GROUP"
    fi

    if getent passwd "$SVC_USER" >/dev/null; then
        IFS=: read -r _ _ uid gid _ _ shell < <(getent passwd "$SVC_USER")
        group_gid=$(getent group "$SVC_GROUP" | cut -d: -f3)
        log "user $SVC_USER exists (uid $uid)"
        [[ $gid == "$group_gid" ]] || warn "user $SVC_USER's primary group is not $SVC_GROUP"
        [[ $shell == */nologin || $shell == */false ]] || warn "user $SVC_USER has login shell $shell (STIG: use nologin)"
        (( uid < 1000 )) || warn "user $SVC_USER (uid $uid) is not a system account"
    else
        log "creating system user $SVC_USER"
        run useradd -r -g "$SVC_GROUP" -s "$nologin" -M -d / -c "monfile service account" "$SVC_USER"
    fi
}

# --- 4. Code tree: build $PREFIX_NEW, then swap it in --------------------------

recover_leftovers() {
    if [[ -e $PREFIX_NEW ]]; then
        warn "removing $PREFIX_NEW left by an interrupted install"
        run rm -rf -- "$PREFIX_NEW"
    fi
    if [[ -e $PREFIX_OLD ]]; then
        if [[ -e $PREFIX ]]; then
            warn "removing $PREFIX_OLD left by an interrupted install"
            run rm -rf -- "$PREFIX_OLD"
        else
            warn "restoring $PREFIX from $PREFIX_OLD left by an interrupted install"
            run mv -- "$PREFIX_OLD" "$PREFIX"
        fi
    fi
}

write_launcher() {
    write_file "$PREFIX_NEW/bin/monfile" root:root 0755 <<EOF
#!$PY -I
# monfile launcher, generated by install.sh (ADR-0008). Do not edit: re-run install.sh.
# -I (isolated mode) ignores PYTHON* env vars and user site-packages.
import sys
sys.path.insert(0, "$PREFIX/lib")
from monfile.cli import main
raise SystemExit(main())
EOF
}

write_version() {
    write_file "$PREFIX_NEW/VERSION" root:root 0644 <<EOF
version=$VERSION
tarball_sha256=${TARBALL_SHA:-unrecorded}
installed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
installed_by=${SUDO_USER:-root}
python=$PY
python_version=$PY_VERSION
EOF
}

build_tree() {
    local p dest
    [[ -f $PREFIX/VERSION ]] && PREV_VERSION=$(sed -n 's/^version=//p' "$PREFIX/VERSION")
    recover_leftovers
    log "building $PREFIX_NEW"
    run install -d -o root -g root -m 0755 "$PREFIX_NEW" "$PREFIX_NEW/bin" "$PREFIX_NEW/lib" "$PREFIX_NEW/docs"
    while IFS= read -r p; do
        case $p in
            src/monfile/*)               dest=$PREFIX_NEW/lib/${p#src/} ;;
            docs/*)                      dest=$PREFIX_NEW/docs/${p#docs/} ;;
            HOWTO.md)                    dest=$PREFIX_NEW/docs/HOWTO.md ;;
            config/monfile.example.toml) dest=$PREFIX_NEW/docs/monfile.example.toml ;;
            *)                           continue ;;
        esac
        run install -D -o root -g root -m 0644 "$STAGE/$p" "$dest"
    done < <(sed 's/^[0-9a-f]*  //' "$STAGE/MANIFEST.sha256")
    write_launcher
    write_version
    normalize_tree "$PREFIX_NEW"
}

# normalize_tree DIR: enforce the FILE_INVENTORY owners and modes on everything under DIR.
normalize_tree() {
    run chown -R root:root "$1"
    run find "$1" -type d -exec chmod 0755 {} +
    run find "$1" -type f -exec chmod 0644 {} +
    run chmod 0755 "$1/bin/monfile"
}

stop_units() {
    local u
    # Timer first, so it cannot start another scan while we stop the running one.
    for u in monfile-scan.timer monfile-scan.service monfile.service; do
        if unit_active "$u"; then
            log "stopping $u for the upgrade"
            run systemctl stop "$u"
            [[ $u == monfile-scan.service ]] || STOPPED+=("$u")
        fi
    done
}

swap_tree() {
    if [[ -e $PREFIX ]]; then
        log "replacing $PREFIX (${PREV_VERSION:-unknown version} -> $VERSION)"
        run mv -- "$PREFIX" "$PREFIX_OLD"
        run mv -- "$PREFIX_NEW" "$PREFIX"
        run rm -rf -- "$PREFIX_OLD"
    else
        log "installing $PREFIX ($VERSION)"
        run mv -- "$PREFIX_NEW" "$PREFIX"
    fi
}

# --- 5. Bytecode (and fapolicyd trust around it) ------------------------------
# The service cannot write to /opt (ProtectSystem=strict), so compile at install time.
# fapolicyd denies Python opening untrusted .py files, so trust the sources first, compile,
# then refresh the trust so it also covers the new __pycache__ files.

compile_bytecode() {
    [[ $FAPOLICYD == yes ]] && fapolicyd_trust
    log "compiling bytecode under $PREFIX/lib"
    run "$PY" -I -m compileall -q -f "$PREFIX/lib"
    normalize_tree "$PREFIX"
    [[ $FAPOLICYD == yes ]] && fapolicyd_trust
    return 0
}

# --- 6. Config and state ---------------------------------------------------------

install_config() {
    local owner group mode
    run install -d -o root -g "$SVC_GROUP" -m 0750 "$CONF_DIR"
    if [[ -e $CONF_FILE ]]; then
        read -r owner group mode < <(stat -c '%U %G %a' -- "$CONF_FILE")
        log "keeping existing $CONF_FILE"
        [[ $owner:$group:$mode == root:$SVC_GROUP:640 ]] \
            || warn "$CONF_FILE is $owner:$group $mode; expected root:$SVC_GROUP 0640 (monfile will refuse insecure configs)"
    else
        log "installing example config as $CONF_FILE"
        write_file "$CONF_FILE" "root:$SVC_GROUP" 0640 <"$STAGE/config/monfile.example.toml"
    fi
    run install -d -o "$SVC_USER" -g "$SVC_GROUP" -m 0750 "$STATE_DIR"
}

# --- 7. systemd ------------------------------------------------------------------

install_units() {
    local u
    for u in "${UNITS[@]}"; do
        write_file "$UNIT_DIR/$u" root:root 0644 <"$STAGE/packaging/systemd/$u"
    done
    write_file "$TMPFILES_CONF" root:root 0644 <"$STAGE/packaging/tmpfiles.d/monfile.conf"
    log "installed units: ${UNITS[*]} (none enabled)"
    run systemd-tmpfiles --create "$TMPFILES_CONF"
    run systemctl daemon-reload
}

# --- 8. Smoke test and restart ---------------------------------------------------

smoke_test() {
    local out
    (( DRY_RUN )) && { run runuser -u "$SVC_USER" -- "$PREFIX/bin/monfile" --version; return 0; }
    out=$(cd / && runuser -u "$SVC_USER" -- "$PREFIX/bin/monfile" --version 2>&1) \
        || die "smoke test failed: '$PREFIX/bin/monfile --version' as $SVC_USER: $out"
    [[ $out == "monfile $VERSION" ]] || die "smoke test: expected 'monfile $VERSION', got '$out'"
    log "smoke test passed: $out"
}

restart_units() {
    local u rc=0 pending=("${STOPPED[@]}")
    STOPPED=()
    for u in "${pending[@]}"; do
        log "starting $u again"
        if ! run systemctl start "$u"; then
            warn "$u failed to start; check: journalctl -u $u"
            rc=1
        fi
    done
    return "$rc"
}

next_steps() {
    if (( DRY_RUN )); then
        printf '\nDry run of the monfile %s install finished: nothing was changed.\n' "$VERSION"
    else
        printf '\nmonfile %s installed.\n' "$VERSION"
    fi
    cat <<EOF

Next steps:
  1. Edit $CONF_FILE (reference: $PREFIX/docs/monfile.example.toml).
  2. Allow the sandbox to write to your folders:  systemctl edit monfile.service
     (or monfile-scan.service), then add  [Service] ReadWritePaths=<dirs>
  3. Validate:  sudo -u $SVC_USER $PREFIX/bin/monfile validate-config
EOF
    if (( ${#PREV_RUNNING[@]} )); then
        echo "  4. Already running again: ${PREV_RUNNING[*]} (enablement was not changed)."
    else
        cat <<EOF
  4. Enable ONE run mode (OPERATIONS §4):
       systemctl enable --now monfile.service      # daemon, or
       systemctl enable --now monfile-scan.timer   # timer
EOF
    fi
    echo "  5. Re-baseline AIDE (or your file-integrity tool) for $PREFIX and $CONF_DIR."
    if [[ $FAPOLICYD == no ]] && command -v fapolicyd-cli >/dev/null 2>&1; then
        cat <<EOF
  !  fapolicyd is installed but no trust was added. Before enabling it, re-run install.sh
     or run: fapolicyd-cli --file add $PREFIX/ && fapolicyd-cli --update
EOF
    fi
}

main() {
    syslog_init
    log "install started by ${SUDO_USER:-$(id -un)} from $SRC$( (( DRY_RUN )) && printf ' (dry run)')"
    preflight
    verify_and_stage
    ensure_account
    build_tree
    stop_units
    PREV_RUNNING=("${STOPPED[@]}")
    swap_tree
    compile_bytecode
    install_config
    install_units
    smoke_test
    local rc=0
    restart_units || rc=1
    log "install of monfile $VERSION finished$( (( rc )) && printf ' with errors')"
    next_steps
    return "$rc"
}

main
