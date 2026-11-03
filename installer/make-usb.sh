#!/bin/bash
# Build an unattended couchbox installer ISO from the stock Ubuntu Server 24.04 live ISO.
#
#   ./make-usb.sh --iso ubuntu-24.04.x-live-server-amd64.iso --disk /dev/nvme0n1 --out couchbox.iso \
#                 [--admin-user couch-owner] [--hostname couchbox] [--timezone America/New_York] [--wifi-ssid NAME]
#   ./make-usb.sh --render-only DIR --disk only ...   (writes user-data/meta-data only; used by tests)
#
# --disk is REQUIRED: /dev/<name> of the NUC's internal disk, or "only" = the single internal disk (the install
# refuses if the NUC has more than one). The NUC's disk is WIPED by the resulting installer - that is the only thing
# in couchbox that partitions or formats. The admin password (and Wi-Fi passphrase, if any) are typed here, hashed
# or written only into the generated image, and never stored in the repo. Write the ISO to a USB stick with e.g.
# `sudo dd if=couchbox.iso of=/dev/sdX bs=4M conv=fsync status=progress` (double-check sdX).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ISO="" DISK="" OUT="" RENDER_ONLY="" ADMIN_USER="couch-owner" HOSTNAME_="couchbox" TZ_="Etc/UTC" WIFI_SSID=""
PASSWORD_HASH="${COUCH_ADMIN_HASH:-}"   # tests/CI only; interactive use prompts
WIFI_PSK="${COUCH_WIFI_PSK:-}"

die() { echo "make-usb: $*" >&2; exit 1; }
usage() { sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --iso) ISO="${2:-}"; shift ;;
        --disk) DISK="${2:-}"; shift ;;
        --out) OUT="${2:-}"; shift ;;
        --render-only) RENDER_ONLY="${2:-}"; shift ;;
        --admin-user) ADMIN_USER="${2:-}"; shift ;;
        --hostname) HOSTNAME_="${2:-}"; shift ;;
        --timezone) TZ_="${2:-}"; shift ;;
        --wifi-ssid) WIFI_SSID="${2:-}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# ---------------------------------------------------------------- validation
case "$DISK" in
    "") die "--disk is required (/dev/<name> of the NUC's internal disk, or 'only'); refusing to guess" ;;
    only) ;;
    /dev/*)
        [[ "$DISK" =~ ^/dev/[a-zA-Z0-9/_-]+$ ]] || die "--disk '$DISK' has unexpected characters" ;;
    *) die "--disk must be /dev/<name> or 'only' (got '$DISK')" ;;
esac
[[ "$ADMIN_USER" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || die "--admin-user must be a lowercase unix user name"
case "$ADMIN_USER" in couch|couchadmin|root) die "--admin-user '$ADMIN_USER' is reserved" ;; esac
[[ "$HOSTNAME_" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die "--hostname must be a simple host name"
[[ "$TZ_" =~ ^[A-Za-z0-9_+/-]+$ ]] || die "--timezone looks wrong"
if [ -n "$WIFI_SSID" ]; then
    if ! [[ "$WIFI_SSID" =~ ^[[:print:]]{1,32}$ ]] || [[ "$WIFI_SSID" == *'"'* ]]; then
        die "--wifi-ssid must be 1-32 printable chars, no quotes"
    fi
fi
if [ -z "$RENDER_ONLY" ]; then
    [ -n "$ISO" ] || die "--iso is required"
    [ -f "$ISO" ] || die "ISO not found: $ISO"
    [ -n "$OUT" ] || die "--out is required"
    [ ! -e "$OUT" ] || die "$OUT exists; refusing to overwrite"
    command -v xorriso >/dev/null || die "xorriso is required (sudo apt-get install xorriso)"
fi

# ---------------------------------------------------------------- secrets (typed, never stored)
if [ -z "$PASSWORD_HASH" ]; then
    [ -t 0 ] || die "no terminal to type the admin password on"
    command -v openssl >/dev/null || die "openssl is required to hash the admin password"
    read -r -s -p "Password for console admin '$ADMIN_USER' on the NUC: " pw1; echo
    read -r -s -p "Again: " pw2; echo
    [ "$pw1" = "$pw2" ] || die "passwords differ"
    [ "${#pw1}" -ge 8 ] || die "use at least 8 characters"
    PASSWORD_HASH="$(printf '%s' "$pw1" | openssl passwd -6 -stdin)"
    unset pw1 pw2
fi
[[ "$PASSWORD_HASH" == "\$6\$"* ]] || die "admin password hash must be SHA-512 crypt (\$6\$...)"
if [ -n "$WIFI_SSID" ] && [ -z "$WIFI_PSK" ]; then
    [ -t 0 ] || die "no terminal to type the Wi-Fi passphrase on"
    read -r -s -p "Wi-Fi passphrase for '$WIFI_SSID': " WIFI_PSK; echo
fi
if [ -n "$WIFI_SSID" ]; then
    if [ "${#WIFI_PSK}" -lt 8 ] || [ "${#WIFI_PSK}" -gt 63 ]; then die "Wi-Fi passphrase must be 8-63 characters"; fi
    case "$WIFI_PSK" in *'"'*|*"\\"*) die "Wi-Fi passphrase with quotes/backslashes is not supported here" ;; esac
fi

# ---------------------------------------------------------------- render user-data
render() {
    local dest="$1" disk_match wifi_block=""
    if [ "$DISK" = "only" ]; then disk_match="        size: largest"; else disk_match="        path: $DISK"; fi
    if [ -n "$WIFI_SSID" ]; then
        wifi_block="    wifis:
      couch-wifi:
        match:
          name: \"w*\"
        dhcp4: true
        dhcp6: false
        optional: true
        access-points:
          \"$WIFI_SSID\":
            password: \"$WIFI_PSK\""
    fi
    mkdir -p "$dest"
    local tpl line
    tpl="$SCRIPT_DIR/autoinstall/user-data"
    : >"$dest/user-data"
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            "@@WIFI_BLOCK@@") if [ -n "$wifi_block" ]; then printf '%s\n' "$wifi_block" >>"$dest/user-data"; fi ;;
            "@@DISK_MATCH@@") printf '%s\n' "$disk_match" >>"$dest/user-data" ;;
            *)
                line="${line//@@DISK@@/$DISK}"
                line="${line//@@HOSTNAME@@/$HOSTNAME_}"
                line="${line//@@ADMIN_USER@@/$ADMIN_USER}"
                line="${line//@@ADMIN_HASH@@/$PASSWORD_HASH}"
                line="${line//@@TIMEZONE@@/$TZ_}"
                printf '%s\n' "$line" >>"$dest/user-data" ;;
        esac
    done <"$tpl"
    : >"$dest/meta-data"
    chmod 0600 "$dest/user-data"
    if grep -qE '@@[A-Z_]+@@' "$dest/user-data"; then die "unrendered placeholder left in user-data"; fi
}

if [ -n "$RENDER_ONLY" ]; then
    render "$RENDER_ONLY"
    echo "make-usb: rendered $RENDER_ONLY/user-data"
    exit 0
fi

# ---------------------------------------------------------------- repack the ISO
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
render "$WORK/nocloud"
mkdir -p "$WORK/couchbox"
(cd "$REPO_DIR" && tar --exclude=./.git -cf - .) | (cd "$WORK/couchbox" && tar -xf -)

xorriso -osirrox on -indev "$ISO" -extract /boot/grub/grub.cfg "$WORK/grub.cfg" >/dev/null 2>&1 ||
    die "could not read /boot/grub/grub.cfg from $ISO (not an Ubuntu live-server ISO?)"
grep -q 'casper/vmlinuz' "$WORK/grub.cfg" || die "$ISO grub.cfg has no casper kernel entry"
chmod u+w "$WORK/grub.cfg"
# Boot the first entry automatically with autoinstall + our NoCloud seed on the ISO.
sed -i -e 's|^\(\s*linux\s\+/casper/vmlinuz\)\(.*\)---|\1\2 autoinstall ds=nocloud\\;s=/cdrom/nocloud/ ---|' \
    -e 's/^set timeout=.*/set timeout=5/' "$WORK/grub.cfg"
grep -q 'autoinstall ds=nocloud' "$WORK/grub.cfg" || die "could not add autoinstall to grub.cfg"

xorriso -indev "$ISO" -outdev "$OUT" \
    -map "$WORK/nocloud" /nocloud \
    -map "$WORK/couchbox" /couchbox \
    -map "$WORK/grub.cfg" /boot/grub/grub.cfg \
    -boot_image any replay >/dev/null 2>&1 || die "xorriso failed to write $OUT"
echo "make-usb: wrote $OUT (target disk: $DISK - it will be WIPED). Write it to a USB stick and boot the NUC from it."
