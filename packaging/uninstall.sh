#!/usr/bin/env bash
# monfile uninstaller (ADR-0008, ADR-0010). Removes what install.sh created, as listed in
# docs/audit/FILE_INVENTORY.md. The config, state, and manifests are always preserved,
# because they are audit data.
set -Eeuo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=packaging/lib/common.sh
. "$SRC/packaging/lib/common.sh"

usage() {
    cat <<EOF
Usage: sudo $0 [--purge] [--dry-run]

Remove monfile's code, systemd units, and fapolicyd trust.
$CONF_DIR and $STATE_DIR are always kept.

Options:
  --purge     also remove the $SVC_USER user and group. Refused while $CONF_DIR or
              $STATE_DIR still exist (archive and remove them first).
  --dry-run   print every change instead of making it
  -h, --help  show this help
EOF
}

PURGE=0
while (( $# )); do
    case $1 in
        --purge)   PURGE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        *)         usage >&2; exit 2 ;;
    esac
    shift
done

trap '_emit err "error: " "uninstall failed (exit $? at uninstall.sh line $LINENO)"' ERR

stop_and_disable() {
    local u
    for u in monfile-scan.timer monfile-scan.service monfile.service; do
        if [[ -e $UNIT_DIR/$u ]] || unit_active "$u"; then
            log "stopping and disabling $u"
            run systemctl disable --now "$u" || warn "could not stop/disable $u"
        fi
    done
}

remove_units() {
    local u
    for u in "${UNITS[@]}"; do
        [[ -e $UNIT_DIR/$u ]] && run rm -f -- "$UNIT_DIR/$u"
        if [[ -d $UNIT_DIR/$u.d ]]; then
            # Admin-maintained drop-ins (paths.conf). List them so the removal is on record.
            log "removing drop-ins: $(find "$UNIT_DIR/$u.d" -type f -printf '%f ')"
            run rm -rf -- "$UNIT_DIR/$u.d"
        fi
    done
    [[ -e $TMPFILES_CONF ]] && run rm -f -- "$TMPFILES_CONF"
    run systemctl daemon-reload
    run systemctl reset-failed "${UNITS[@]}" 2>/dev/null || true
}

remove_trust() {
    command -v fapolicyd-cli >/dev/null 2>&1 || return 0
    log "removing fapolicyd trust for $PREFIX/ (if any)"
    fapolicyd_untrust
    fapolicyd_reload
}

remove_code() {
    local d
    for d in "$PREFIX" "$PREFIX_NEW" "$PREFIX_OLD"; do
        if [[ -e $d ]]; then
            log "removing $d"
            run rm -rf -- "$d"
        fi
    done
}

purge_account() {
    local d removed=0 kept=()
    for d in "$CONF_DIR" "$STATE_DIR"; do
        [[ -e $d ]] && kept+=("$d")
    done
    if (( ${#kept[@]} )); then
        die "--purge refused: ${kept[*]} still exist(s) and belong to $SVC_USER/$SVC_GROUP. Archive and remove them, then re-run with --purge."
    fi
    if getent passwd "$SVC_USER" >/dev/null; then
        log "removing user $SVC_USER"
        run userdel "$SVC_USER"
        removed=1
    fi
    if getent group "$SVC_GROUP" >/dev/null; then
        log "removing group $SVC_GROUP"
        run groupdel "$SVC_GROUP"
        removed=1
    fi
    (( removed )) && warn "ACLs or group ownership granted to $SVC_GROUP on watch/destination folders now refer to a bare GID; remove them (HOWTO §4)"
    return 0
}

main() {
    syslog_init
    log "uninstall started by ${SUDO_USER:-$(id -un)}$( (( DRY_RUN )) && printf ' (dry run)')"
    require_root
    require_cmds systemctl find flock getent
    (( PURGE )) && require_cmds userdel groupdel
    require_systemd
    take_lock
    refuse_symlink "$PREFIX" "$PREFIX_NEW" "$PREFIX_OLD" "$TMPFILES_CONF" "${UNITS[@]/#/$UNIT_DIR/}"

    stop_and_disable
    remove_units
    remove_trust
    remove_code
    (( PURGE )) && purge_account

    log "uninstall finished$( (( DRY_RUN )) && printf ' (dry run: nothing was changed)')"
    cat <<EOF

Kept (audit data; remove manually per your retention policy):
  $CONF_DIR
  $STATE_DIR
Re-baseline AIDE (or your file-integrity tool) to drop $PREFIX.
EOF
}

main
