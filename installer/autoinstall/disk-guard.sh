#!/bin/sh
# couchbox autoinstall early-command: refuse to install unless the target disk is unambiguous.
#   disk-guard.sh /dev/nvme0n1   -> that disk must exist and be an internal (non-USB, non-removable) disk
#   disk-guard.sh only           -> there must be EXACTLY one internal disk; otherwise refuse
# Exits non-zero (the installer stops with an error) instead of ever picking a disk silently.
set -eu
want="${1:-}"
internal="$(lsblk -dn -o NAME,TYPE,RM,TRAN 2>/dev/null | awk '$2=="disk" && $3=="0" && $4!="usb" {print "/dev/"$1}')"
count="$(printf '%s\n' "$internal" | grep -c . || true)"
echo "couchbox disk-guard: internal disks: $(echo "$internal" | tr '\n' ' ') (requested: $want)"
case "$want" in
    "")
        echo "couchbox disk-guard: no target disk given; refusing" >&2; exit 1 ;;
    only)
        if [ "$count" -ne 1 ]; then
            echo "couchbox disk-guard: $count internal disks found; rebuild the USB with --disk /dev/<name>" >&2
            exit 1
        fi ;;
    /dev/*)
        if ! printf '%s\n' "$internal" | grep -qx "$want"; then
            echo "couchbox disk-guard: $want is not an internal disk here; refusing" >&2
            exit 1
        fi ;;
    *)
        echo "couchbox disk-guard: bad target '$want'; refusing" >&2; exit 1 ;;
esac
echo "couchbox disk-guard: OK, installing to ${want}"
