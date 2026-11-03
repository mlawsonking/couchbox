#!/bin/bash
# couchbox installer for the NUC (Ubuntu Server 24.04 LTS only).
#
#   sudo ./install.sh [--dry-run] [--no-services] [--no-dkms] [--skip-packages] [--pins FILE]
#
# Idempotent: every step checks current state first; a second run reports "changes: 0".
# Touches ONLY: the packages listed below, /opt/couch, /etc/couch, /var/lib/couch, /var/log/couch, couch-* systemd
# units (+ the couch journald drop-in), the getty@tty1 mask, couch unattended-upgrades config, the users "couch" and
# "couchadmin", and - for the gamepad drivers it names (xone, xpadneo) - their DKMS trees under /usr/src, their
# modprobe blacklist and the dongle firmware in /lib/firmware. It never partitions or formats anything.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DRY_RUN=0
NO_SERVICES=0
NO_DKMS=0
SKIP_PACKAGES=0
FROM_FIRSTBOOT=0
PINS_FILE="$SCRIPT_DIR/pins.env"
OS_RELEASE="${COUCH_OS_RELEASE:-/etc/os-release}"

COUCH_OPT=/opt/couch
COUCH_ETC=/etc/couch
COUCH_VAR=/var/lib/couch
COUCH_LOG=/var/log/couch
UNIT_DIR=/etc/systemd/system
LOG_FILE="$COUCH_LOG/install.log"

CHANGES=0
WARNINGS=0
JOURNALD_CHANGED=0

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  --dry-run        print the plan (every change that would be made) and change nothing
  --no-services    do not call systemctl at all (container rehearsal)
  --no-dkms        skip the xone/xpadneo DKMS gamepad drivers and dongle firmware (container rehearsal)
  --skip-packages  do not apt-get anything (development only)
  --pins FILE      alternative pins file (tests use a fixture moonlight-qt)
  --from-firstboot used by couch-firstboot.service to finish an install begun in the installer chroot
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --no-services) NO_SERVICES=1 ;;
        --no-dkms) NO_DKMS=1 ;;
        --skip-packages) SKIP_PACKAGES=1 ;;
        --from-firstboot) FROM_FIRSTBOOT=1 ;;
        --pins) shift; PINS_FILE="${1:?--pins needs a file}" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "install.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# ---------------------------------------------------------------- output
say() {
    printf '%s\n' "$*"
    if [ "$DRY_RUN" -eq 0 ] && [ -d "$COUCH_LOG" ]; then
        printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG_FILE"
    fi
}
ok() { say "  ok      $*"; }
warn() { WARNINGS=$((WARNINGS + 1)); say "  WARN    $*"; }
die() { say "  FAIL    $*"; exit 1; }
# change "<description>" cmd args... : in dry-run prints the plan, otherwise runs the command.
change() {
    local desc="$1"; shift
    CHANGES=$((CHANGES + 1))
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  PLAN    $desc"
    else
        say "  change  $desc"
        "$@"
    fi
}
section() { say ""; say "== $*"; }

# ---------------------------------------------------------------- preflight
preflight() {
    section "preflight"
    [ -r "$OS_RELEASE" ] || die "cannot read $OS_RELEASE"
    local id version
    # shellcheck source=/dev/null
    id="$(. "$OS_RELEASE" && printf '%s' "${ID:-}")"
    # shellcheck source=/dev/null
    version="$(. "$OS_RELEASE" && printf '%s' "${VERSION_ID:-}")"
    if [ "$id" != "ubuntu" ] || [ "$version" != "24.04" ]; then
        die "this installer supports Ubuntu 24.04 only (found: ${id:-unknown} ${version:-unknown}); refusing"
    fi
    ok "Ubuntu 24.04"
    if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
        die "run as root (sudo ./install.sh), or use --dry-run"
    fi
    [ -r "$PINS_FILE" ] || die "pins file not found: $PINS_FILE"
    # shellcheck source=installer/pins.env
    . "$PINS_FILE"
    ok "pins: moonlight-qt $MOONLIGHT_VERSION, xone $XONE_VERSION, xpadneo $XPADNEO_VERSION"
    if [ "$DRY_RUN" -eq 0 ]; then
        [ -d "$COUCH_LOG" ] || install -d -m 0775 "$COUCH_LOG"
        say "  log     $LOG_FILE"
    fi
}

# True only when systemd is PID 1 of THIS root (not a chroot such as curtin in-target, which bind-mounts /run).
systemd_running() {
    [ "${COUCH_FORCE_DEFER:-0}" = 1 ] && return 1
    [ -d /run/systemd/system ] || return 1
    if command -v systemd-detect-virt >/dev/null && systemd-detect-virt --quiet --chroot; then return 1; fi
    return 0
}

# Deferred mode: running inside the Ubuntu installer's chroot (no systemd PID 1). The DKMS build and service start
# happen on first boot via couch-firstboot.service.
DEFER=0
decide_mode() {
    if [ "$NO_SERVICES" -eq 0 ] && ! systemd_running; then
        DEFER=1
        say "  note    systemd is not running (installer chroot?): DKMS build + service start deferred to first boot"
    fi
}

# ---------------------------------------------------------------- packages
PACKAGES_BASE=(
    cage xwayland seatd libinput-bin
    mesa-va-drivers intel-media-va-driver i965-va-driver vainfo libva-drm2 libegl1 libgl1 libgles2
    python3 python3-venv
    fonts-dejavu-core
    alsa-utils
    bluez
    avahi-daemon libnss-mdns
    iputils-ping iperf3 iproute2
    curl ca-certificates git libarchive-tools
    unattended-upgrades
)
PACKAGES_DKMS=(dkms linux-headers-generic build-essential)

pkg_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q '^install ok installed$'; }

ensure_packages() {
    section "packages"
    if [ "$SKIP_PACKAGES" -eq 1 ]; then
        warn "--skip-packages: not checking or installing packages"
        return
    fi
    local want=("${PACKAGES_BASE[@]}") missing=() p
    if [ "$NO_DKMS" -eq 0 ]; then want+=("${PACKAGES_DKMS[@]}"); fi
    for p in "${want[@]}"; do
        if pkg_installed "$p"; then :; else missing+=("$p"); fi
    done
    if [ "${#missing[@]}" -eq 0 ]; then
        ok "all ${#want[@]} packages present"
        return
    fi
    change "apt-get install ${missing[*]}" apt_install "${missing[@]}"
}

apt_install() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q >>"$LOG_FILE" 2>&1 || apt-get update -q
    apt-get install -y -q --no-install-recommends "$@" >>"$LOG_FILE" 2>&1 || {
        tail -n 30 "$LOG_FILE" >&2
        die "apt-get install failed (see $LOG_FILE)"
    }
}

# ---------------------------------------------------------------- users
ensure_users() {
    section "users"
    local g
    for g in video input render audio; do
        if ! getent group "$g" >/dev/null; then
            change "create missing system group $g" groupadd --system "$g"
        fi
    done
    if id couch >/dev/null 2>&1; then
        ok "user couch exists"
    else
        change "create system user couch (no login shell, home $COUCH_VAR/home)" \
            useradd --system --user-group --home-dir "$COUCH_VAR/home" --no-create-home \
            --shell /usr/sbin/nologin --comment "couchbox kiosk" couch
    fi
    local have
    for g in video input render audio; do
        have="$(id -nG couch 2>/dev/null || true)"
        case " $have " in
            *" $g "*) ;;
            *) change "add couch to group $g" usermod -aG "$g" couch ;;
        esac
    done

    local keys="$SCRIPT_DIR/authorized_keys"
    if [ ! -s "$keys" ] || ! grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+|sk-ssh-ed25519@openssh.com) ' "$keys"; then
        ok "installer/authorized_keys empty: skipping couchadmin"
        return
    fi
    if grep -qE 'PRIVATE KEY' "$keys"; then die "installer/authorized_keys contains a PRIVATE key; refusing"; fi
    if id couchadmin >/dev/null 2>&1; then
        ok "user couchadmin exists"
    else
        # Key-only admin for remote validation: password locked, read access to the journal, no sudo.
        change "create user couchadmin (key-only SSH, password locked, groups systemd-journal adm)" \
            useradd --create-home --shell /bin/bash --groups systemd-journal,adm --comment "couchbox admin" couchadmin
        if [ "$DRY_RUN" -eq 0 ]; then passwd -l couchadmin >/dev/null; fi
    fi
    local home=/home/couchadmin
    if [ "$DRY_RUN" -eq 0 ]; then home="$(getent passwd couchadmin | cut -d: -f6)"; fi
    ensure_dir "$home/.ssh" 0700 couchadmin:couchadmin
    ensure_file "$keys" "$home/.ssh/authorized_keys" 0600 couchadmin:couchadmin
}

# ---------------------------------------------------------------- files
# ensure_dir PATH MODE OWNER:GROUP
ensure_dir() {
    local path="$1" mode="$2" owner="$3"
    if [ ! -d "$path" ]; then
        change "mkdir $path ($mode $owner)" install -d -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$path"
        return
    fi
    local cur
    cur="$(stat -c '%a %U:%G' "$path")"
    if [ "$cur" != "${mode#0} $owner" ]; then
        change "fix $path to $mode $owner (was $cur)" fix_perms "$path" "$mode" "$owner"
    fi
}

fix_perms() { chmod "$2" "$1"; chown "$3" "$1"; }

# ensure_file SRC DEST MODE OWNER:GROUP  (content + mode + owner)
ensure_file() {
    local src="$1" dest="$2" mode="$3" owner="$4"
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        local cur
        cur="$(stat -c '%a %U:%G' "$dest")"
        if [ "$cur" != "${mode#0} $owner" ]; then
            change "fix $dest to $mode $owner (was $cur)" fix_perms "$dest" "$mode" "$owner"
        fi
        return
    fi
    local what="install"
    [ -e "$dest" ] && what="update"
    change "$what $dest" install -D -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$src" "$dest"
}

# ensure_file_once SRC DEST MODE OWNER : copy only if DEST is absent (user-edited config is never overwritten)
ensure_file_once() {
    if [ -e "$2" ]; then ok "$2 present (not overwritten)"; return; fi
    ensure_file "$@"
}

ensure_layout() {
    section "directories"
    ensure_dir "$COUCH_OPT" 0755 root:root
    ensure_dir "$COUCH_OPT/bin" 0755 root:root
    ensure_dir "$COUCH_OPT/moonlight" 0755 root:root
    ensure_dir "$COUCH_ETC" 0750 root:couch
    ensure_dir "$COUCH_VAR" 0755 couch:couch
    ensure_dir "$COUCH_VAR/home" 0750 couch:couch
    ensure_dir "$COUCH_VAR/art" 0755 couch:couch
    ensure_dir "$COUCH_VAR/cache" 0755 root:root
    ensure_dir "$COUCH_LOG" 0775 root:couch
}

ensure_source_copy() {
    section "source copy ($COUCH_OPT/src, used by first boot and couch-update)"
    local dest="$COUCH_OPT/src"
    if [ "$REPO_DIR" = "$dest" ]; then ok "running from $dest"; return; fi
    local a b
    a="$(tree_hash "$REPO_DIR")"
    b="$( [ -d "$dest" ] && tree_hash "$dest" || echo none)"
    if [ "$a" = "$b" ]; then ok "$dest up to date"; return; fi
    change "copy repo to $dest" copy_tree "$REPO_DIR" "$dest"
}

tree_hash() {
    (cd "$1" && find . -path ./.git -prune -o -type f -print0 | LC_ALL=C sort -z |
        xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
}

copy_tree() {
    local tmp="$2.new"
    rm -rf "$tmp"
    mkdir -p "$tmp"
    (cd "$1" && tar --exclude=./.git -cf - .) | (cd "$tmp" && tar -xf -)
    chown -R root:root "$tmp"
    rm -rf "$2"
    mv "$tmp" "$2"
}

ensure_files() {
    section "couch files"
    local f="$SCRIPT_DIR/files"
    ensure_file "$REPO_DIR/couch/bin/kiosk" "$COUCH_OPT/bin/kiosk" 0755 root:root
    ensure_file "$REPO_DIR/couch/doctor/couch-doctor" "$COUCH_OPT/bin/couch-doctor" 0755 root:root
    ensure_file "$f/etc/couch/couch.toml.example" "$COUCH_ETC/couch.toml.example" 0644 root:root
    ensure_file_once "$f/etc/couch/couch.toml.example" "$COUCH_ETC/couch.toml" 0640 root:couch

    section "systemd units and system config"
    ensure_file "$f/systemd/couch-kiosk.service" "$UNIT_DIR/couch-kiosk.service" 0644 root:root
    ensure_file "$f/systemd/couch-firstboot.service" "$UNIT_DIR/couch-firstboot.service" 0644 root:root
    if ! cmp -s "$f/journald/couch-journald.conf" /etc/systemd/journald.conf.d/couch.conf; then JOURNALD_CHANGED=1; fi
    ensure_file "$f/journald/couch-journald.conf" /etc/systemd/journald.conf.d/couch.conf 0644 root:root
    ensure_file "$f/apt/52couch-unattended-upgrades" /etc/apt/apt.conf.d/52couch-unattended-upgrades 0644 root:root
    ensure_file "$f/apt/21couch-auto-upgrades" /etc/apt/apt.conf.d/21couch-auto-upgrades 0644 root:root
}

# ---------------------------------------------------------------- moonlight-qt
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

fetch_verified() { # URL DEST SHA256
    local url="$1" dest="$2" sha_want="$3"
    if [ -f "$dest" ] && [ "$(sha256_of "$dest")" = "$sha_want" ]; then return 0; fi
    rm -f "$dest.part"
    case "$url" in
        file://*) cp "${url#file://}" "$dest.part" ;;
        *) curl -fsSL --retry 3 -o "$dest.part" "$url" ;;
    esac
    local got
    got="$(sha256_of "$dest.part")"
    if [ "$got" != "$sha_want" ]; then
        rm -f "$dest.part"
        die "sha256 mismatch for $url: expected $sha_want, got $got"
    fi
    mv "$dest.part" "$dest"
}

install_moonlight() {
    local appimage="$COUCH_VAR/cache/moonlight-$MOONLIGHT_VERSION.AppImage"
    local dir="$COUCH_OPT/moonlight/$MOONLIGHT_VERSION"
    fetch_verified "$MOONLIGHT_URL" "$appimage" "$MOONLIGHT_SHA256"
    chmod 0755 "$appimage"
    local tmp="$COUCH_VAR/cache/extract.$$"
    rm -rf "$tmp"
    mkdir -p "$tmp"
    (cd "$tmp" && "$appimage" --appimage-extract >/dev/null)
    [ -x "$tmp/squashfs-root/AppRun" ] || die "moonlight-qt AppImage did not extract an AppRun"
    printf '%s\n' "$MOONLIGHT_SHA256" >"$tmp/squashfs-root/.couch-sha256"
    chmod -R go-w "$tmp/squashfs-root"
    chmod 0755 "$tmp/squashfs-root"
    rm -rf "$dir"
    mv "$tmp/squashfs-root" "$dir"
    rm -rf "$tmp"
}

write_moonlight_wrapper() {
    cat >"$1" <<'EOF'
#!/bin/sh
# moonlight-qt wrapper (couchbox): runs the extracted, sha256-pinned AppImage selected by /opt/couch/moonlight/current.
exec /opt/couch/moonlight/current/AppRun "$@"
EOF
    chmod 0755 "$1"
}

ensure_moonlight() {
    section "moonlight-qt $MOONLIGHT_VERSION"
    local dir="$COUCH_OPT/moonlight/$MOONLIGHT_VERSION"
    if [ -f "$dir/.couch-sha256" ] && [ "$(cat "$dir/.couch-sha256")" = "$MOONLIGHT_SHA256" ] && [ -x "$dir/AppRun" ]; then
        ok "$dir extracted (sha256 $MOONLIGHT_SHA256)"
    else
        change "download + verify + extract moonlight-qt $MOONLIGHT_VERSION into $dir" install_moonlight
    fi
    local link="$COUCH_OPT/moonlight/current"
    if [ "$(readlink "$link" 2>/dev/null || true)" = "$MOONLIGHT_VERSION" ]; then
        ok "$link -> $MOONLIGHT_VERSION"
    else
        change "point $link at $MOONLIGHT_VERSION" ln -sfn "$MOONLIGHT_VERSION" "$link"
    fi
    local wrapper="$COUCH_OPT/bin/moonlight-qt" tmpw
    tmpw="$(mktemp)"
    write_moonlight_wrapper "$tmpw"
    ensure_file "$tmpw" "$wrapper" 0755 root:root
    rm -f "$tmpw"
}

# ---------------------------------------------------------------- gamepad drivers (DKMS)
fetch_pinned_git() { # REPO TAG COMMIT DEST
    local tmp="$4.clone"
    rm -rf "$tmp"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$2" "$1" "$tmp"
    local got
    got="$(git -C "$tmp" rev-parse HEAD)"
    [ "$got" = "$3" ] || { rm -rf "$tmp"; die "$1 tag $2 is $got, pinned $3; refusing"; }
    rm -rf "$tmp/.git"
    printf '%s\n' "$3" >"$tmp/.couch-commit"
    rm -rf "$4"
    mv "$tmp" "$4"
}

prepare_xone_src() {
    local src="/usr/src/xone-$XONE_VERSION"
    fetch_pinned_git "$XONE_REPO" "v$XONE_VERSION" "$XONE_COMMIT" "$src"
    find "$src" -type f \( -name dkms.conf -o -name '*.c' \) -exec sed -i "s/#VERSION#/$XONE_VERSION/" {} +
}

prepare_xpadneo_src() {
    local src="/usr/src/hid-xpadneo-$XPADNEO_VERSION" tmp="/usr/src/.xpadneo-$XPADNEO_VERSION"
    fetch_pinned_git "$XPADNEO_REPO" "v$XPADNEO_VERSION" "$XPADNEO_COMMIT" "$tmp"
    sed "s/\"@DO_NOT_CHANGE@\"/\"$XPADNEO_VERSION\"/g" "$tmp/hid-xpadneo/dkms.conf.in" >"$tmp/hid-xpadneo/dkms.conf"
    rm -rf "$src"
    cp -a "$tmp/hid-xpadneo" "$src"
    cp "$tmp/.couch-commit" "$src/.couch-commit"
    rm -rf "$tmp"
}

dkms_installed_for() { # MODULE VERSION KERNEL
    dkms status -m "$1" -v "$2" -k "$3" 2>/dev/null | grep -q 'installed'
}

install_firmware() { # PID URL FILE SHA
    local work="$COUCH_VAR/cache/fw.$1"
    rm -rf "$work"
    mkdir -p "$work"
    curl -fsSL --retry 3 -o "$work/driver.cab" "$2"
    (cd "$work" && bsdtar -xf driver.cab "$3")
    local got
    got="$(sha256_of "$work/$3")"
    [ "$got" = "$4" ] || { rm -rf "$work"; die "dongle firmware $3 (pid $1) sha256 $got != pinned $4"; }
    install -D -m 0644 "$work/$3" "/lib/firmware/xone_dongle_$1.bin"
    rm -rf "$work"
}

ensure_gamepad_drivers() {
    section "gamepad drivers (xone dongle + wired, xpadneo Bluetooth)"
    if [ "$NO_DKMS" -eq 1 ]; then
        warn "--no-dkms: xone/xpadneo not installed; wired pads use the in-kernel xpad driver"
        return
    fi
    local xsrc="/usr/src/xone-$XONE_VERSION" nsrc="/usr/src/hid-xpadneo-$XPADNEO_VERSION"
    if [ "$(cat "$xsrc/.couch-commit" 2>/dev/null || true)" = "$XONE_COMMIT" ]; then
        ok "$xsrc at pinned commit"
    else
        change "fetch xone v$XONE_VERSION (commit ${XONE_COMMIT:0:12}) into $xsrc" prepare_xone_src
    fi
    if [ "$(cat "$nsrc/.couch-commit" 2>/dev/null || true)" = "$XPADNEO_COMMIT" ]; then
        ok "$nsrc at pinned commit"
    else
        change "fetch xpadneo v$XPADNEO_VERSION (commit ${XPADNEO_COMMIT:0:12}) into $nsrc" prepare_xpadneo_src
    fi

    # xone needs xpad (and mt76x2u, which grabs the dongle's radio) out of the way. Wired Xbox One/Series pads are
    # then served by xone_wired; Xbox 360 wired pads would need xpad-noone (documented, not installed).
    local bl tmpb
    tmpb="$(mktemp)"
    printf '%s\n' "# couchbox: installed with xone (it replaces xpad for Xbox One/Series pads)" \
        "blacklist xpad" "blacklist mt76x2u" >"$tmpb"
    bl=/etc/modprobe.d/couch-xone-blacklist.conf
    ensure_file "$tmpb" "$bl" 0644 root:root
    rm -f "$tmpb"

    local pid url file sha
    while read -r pid url file sha; do
        [ -n "${pid:-}" ] || continue
        if [ -f "/lib/firmware/xone_dongle_$pid.bin" ] && [ "$(sha256_of "/lib/firmware/xone_dongle_$pid.bin")" = "$sha" ]; then
            ok "dongle firmware xone_dongle_$pid.bin"
        else
            change "fetch + verify dongle firmware for pid $pid" install_firmware "$pid" "$url" "$file" "$sha"
        fi
    done <<<"$XONE_FIRMWARE"

    if [ "$DEFER" -eq 1 ]; then
        ok "DKMS build deferred to first boot (kernel at install time != running kernel)"
        return
    fi
    local k
    k="$(uname -r)"
    if dkms_installed_for xone "$XONE_VERSION" "$k"; then
        ok "xone $XONE_VERSION built for $k"
    else
        change "dkms install xone $XONE_VERSION for $k" dkms install -m xone -v "$XONE_VERSION" -k "$k"
    fi
    if dkms_installed_for hid-xpadneo "$XPADNEO_VERSION" "$k"; then
        ok "xpadneo $XPADNEO_VERSION built for $k"
    else
        change "dkms install hid-xpadneo $XPADNEO_VERSION for $k" dkms install -m hid-xpadneo -v "$XPADNEO_VERSION" -k "$k"
    fi
}

# ---------------------------------------------------------------- services
ensure_services() {
    section "services"
    if [ "$NO_SERVICES" -eq 1 ]; then
        warn "--no-services: not masking getty@tty1, enabling or starting units"
        return
    fi
    if [ "$DRY_RUN" -eq 0 ] && systemd_running; then
        systemctl daemon-reload
        if [ "$JOURNALD_CHANGED" -eq 1 ]; then systemctl try-restart systemd-journald.service || true; fi
    fi
    if [ "$(readlink /etc/systemd/system/getty@tty1.service 2>/dev/null || true)" = "/dev/null" ]; then
        ok "getty@tty1 masked"
    else
        change "mask getty@tty1 (no login prompt on the TV)" systemctl mask getty@tty1.service
    fi
    local u
    for u in couch-kiosk.service couch-firstboot.service; do
        if [ -e "/etc/systemd/system/multi-user.target.wants/$u" ]; then
            ok "$u enabled"
        else
            change "enable $u" systemctl enable "$u"
        fi
    done
    if [ "$DEFER" -eq 1 ]; then
        if [ -e "$COUCH_VAR/firstboot-pending" ]; then
            ok "first-boot completion pending"
        else
            change "mark first-boot completion pending" touch "$COUCH_VAR/firstboot-pending"
        fi
        return
    fi
    if [ "$FROM_FIRSTBOOT" -eq 1 ]; then
        # couch-firstboot.service is ordered Before= the kiosk; systemd starts the kiosk after we exit.
        change "first-boot install finished" rm -f "$COUCH_VAR/firstboot-pending"
        return
    fi
    if systemctl is-active --quiet couch-kiosk.service; then
        ok "couch-kiosk active"
    else
        change "start couch-kiosk" systemctl start couch-kiosk.service
    fi
}

# ---------------------------------------------------------------- main
main() {
    say "couchbox install $(date -Is) dry_run=$DRY_RUN no_services=$NO_SERVICES no_dkms=$NO_DKMS"
    preflight
    decide_mode
    ensure_packages
    ensure_users
    ensure_layout
    ensure_source_copy
    ensure_files
    ensure_moonlight
    ensure_gamepad_drivers
    ensure_services
    section "summary"
    if [ "$DRY_RUN" -eq 1 ]; then
        say "dry run: changes planned: $CHANGES, warnings: $WARNINGS (nothing was changed)"
    else
        say "changes: $CHANGES, warnings: $WARNINGS"
    fi
}

main
