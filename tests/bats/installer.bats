#!/usr/bin/env bats
# Fast host-side checks of the installer and USB builder (no root changes; the container rehearsal covers real runs).

setup() {
    REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    TMP="$(mktemp -d)"
}
teardown() { rm -rf "$TMP"; }

@test "install.sh refuses a non-24.04 system" {
    printf 'ID=ubuntu\nVERSION_ID="22.04"\n' >"$TMP/os-release"
    COUCH_OS_RELEASE="$TMP/os-release" run "$REPO/installer/install.sh" --dry-run
    [ "$status" -ne 0 ]
    [[ "$output" == *"Ubuntu 24.04 only"* ]]
}

@test "install.sh refuses Debian even with the same version string" {
    printf 'ID=debian\nVERSION_ID="24.04"\n' >"$TMP/os-release"
    COUCH_OS_RELEASE="$TMP/os-release" run "$REPO/installer/install.sh" --dry-run
    [ "$status" -ne 0 ]
}

@test "install.sh rejects unknown options" {
    run "$REPO/installer/install.sh" --format-everything
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown option"* ]]
}

@test "install.sh --help documents --dry-run --no-services --with-xone --with-xpadneo" {
    run "$REPO/installer/install.sh" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"--dry-run"* && "$output" == *"--no-services"* && "$output" == *"--with-xone"* ]]
    [[ "$output" == *"--with-xpadneo"* ]]
}

@test "default plan installs no DKMS driver and never blacklists xpad; --with-xone does" {
    printf 'ID=ubuntu\nVERSION_ID="24.04"\n' >"$TMP/os-release"
    COUCH_OS_RELEASE="$TMP/os-release" run "$REPO/installer/install.sh" --dry-run --skip-packages
    [ "$status" -eq 0 ]
    [[ "$output" != *"xone"*"blacklist"* && "$output" != *"fetch xone"* && "$output" != *"xpadneo v"* ]]
    [[ "$output" == *"70-couch-gamepads.rules"* ]]
    COUCH_OS_RELEASE="$TMP/os-release" run "$REPO/installer/install.sh" --dry-run --skip-packages --with-xone
    [[ "$output" == *"fetch xone"* && "$output" == *"couch-xone-blacklist.conf"* && "$output" != *"fetch xpadneo"* ]]
}

@test "kiosk unit: Xorg via xinit on tty1, never cage; Xwrapper allows the service user" {
    grep -q '^ExecStart=/usr/bin/xinit /opt/couch/bin/kiosk -- /usr/bin/X :0 vt1 ' "$REPO/installer/files/systemd/couch-kiosk.service"
    ! grep -q '^ExecStart=.*cage' "$REPO/installer/files/systemd/couch-kiosk.service"
    grep -q '^allowed_users=anybody' "$REPO/installer/files/X11/Xwrapper.config"
    grep -q 'Xwrapper.config' "$REPO/installer/install.sh"
    grep -q 'xserver-xorg-core xserver-xorg-legacy' "$REPO/installer/install.sh"
    ! grep -qE '^ +cage ' "$REPO/installer/install.sh"
}

@test "kiosk script is an X11 client (xcb + SDL x11) and sets XDG_RUNTIME_DIR" {
    grep -q 'QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"' "$REPO/couch/bin/kiosk"
    grep -q 'SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"' "$REPO/couch/bin/kiosk"
    grep -q 'XDG_RUNTIME_DIR="/run/user/' "$REPO/couch/bin/kiosk"
}

@test "couch-streamtest --help and couch-display-power usage run" {
    run python3 "$REPO/couch/bin/couch-streamtest" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"--hold"* ]]
    run "$REPO/couch/bin/couch-display-power"
    [ "$status" -eq 2 ]
}

@test "the VA-API package list includes intel-media-va-driver (iHD)" {
    grep -qE '^\s+mesa-va-drivers intel-media-va-driver ' "$REPO/installer/install.sh"
}

@test "install.sh never partitions or formats" {
    run grep -nE '\b(mkfs|parted|sfdisk|fdisk|wipefs|sgdisk|dd if=)' "$REPO/installer/install.sh"
    [ "$status" -ne 0 ]
}

@test "install.sh dry run on an Ubuntu 24.04 host plans and changes nothing" {
    printf 'ID=ubuntu\nVERSION_ID="24.04"\n' >"$TMP/os-release"
    before="$(ls -la /opt /etc | sha256sum)"
    COUCH_OS_RELEASE="$TMP/os-release" run "$REPO/installer/install.sh" --dry-run --skip-packages
    [ "$status" -eq 0 ]
    [[ "$output" == *"dry run: changes planned:"* ]]
    [ "$before" = "$(ls -la /opt /etc | sha256sum)" ]
}

@test "make-usb.sh requires --disk" {
    COUCH_ADMIN_HASH='$6$s$h' run "$REPO/installer/make-usb.sh" --render-only "$TMP/out"
    [ "$status" -ne 0 ]
    [[ "$output" == *"--disk is required"* ]]
    [ ! -e "$TMP/out/user-data" ]
}

@test "make-usb.sh rejects a non-device --disk" {
    COUCH_ADMIN_HASH='$6$s$h' run "$REPO/installer/make-usb.sh" --render-only "$TMP/out" --disk largest
    [ "$status" -ne 0 ]
}

@test "make-usb.sh rejects a non-SHA512 password hash" {
    COUCH_ADMIN_HASH='plaintext' run "$REPO/installer/make-usb.sh" --render-only "$TMP/out" --disk only
    [ "$status" -ne 0 ]
}

@test "make-usb.sh rejects reserved admin names" {
    COUCH_ADMIN_HASH='$6$s$h' run "$REPO/installer/make-usb.sh" --render-only "$TMP/out" --disk only --admin-user couch
    [ "$status" -ne 0 ]
}

@test "make-usb.sh needs an ISO and refuses to overwrite the output" {
    touch "$TMP/existing.iso" "$TMP/in.iso"
    COUCH_ADMIN_HASH='$6$s$h' run "$REPO/installer/make-usb.sh" --iso "$TMP/missing.iso" --disk only --out "$TMP/o.iso"
    [ "$status" -ne 0 ]
    COUCH_ADMIN_HASH='$6$s$h' run "$REPO/installer/make-usb.sh" --iso "$TMP/in.iso" --disk only --out "$TMP/existing.iso"
    [ "$status" -ne 0 ]
    [[ "$output" == *"refusing to overwrite"* ]]
}

@test "make-usb.sh renders the disk into storage match and the guard" {
    COUCH_ADMIN_HASH='$6$s$h' run "$REPO/installer/make-usb.sh" --render-only "$TMP/out" --disk /dev/sda
    [ "$status" -eq 0 ]
    grep -q 'path: /dev/sda' "$TMP/out/user-data"
    grep -q 'disk-guard.sh, "/dev/sda"' "$TMP/out/user-data"
    [ "$(stat -c %a "$TMP/out/user-data")" = 600 ]
}

fake_lsblk() { # writes a fake lsblk printing $1 lines
    mkdir -p "$TMP/bin"
    printf '#!/bin/sh\nprintf "%s"\n' "$1" >"$TMP/bin/lsblk"
    chmod +x "$TMP/bin/lsblk"
}

@test "disk-guard: 'only' refuses when two internal disks exist" {
    fake_lsblk 'nvme0n1 disk 0 nvme\nsda disk 0 sata\nsdb disk 1 usb\n'
    PATH="$TMP/bin:$PATH" run sh "$REPO/installer/autoinstall/disk-guard.sh" only
    [ "$status" -ne 0 ]
    [[ "$output" == *"2 internal disks"* ]]
}

@test "disk-guard: 'only' accepts exactly one internal disk (USB stick ignored)" {
    fake_lsblk 'nvme0n1 disk 0 nvme\nsdb disk 1 usb\n'
    PATH="$TMP/bin:$PATH" run sh "$REPO/installer/autoinstall/disk-guard.sh" only
    [ "$status" -eq 0 ]
}

@test "disk-guard: explicit disk must exist and be internal" {
    fake_lsblk 'nvme0n1 disk 0 nvme\nsda disk 0 sata\nsdb disk 0 usb\n'
    PATH="$TMP/bin:$PATH" run sh "$REPO/installer/autoinstall/disk-guard.sh" /dev/sda
    [ "$status" -eq 0 ]
    PATH="$TMP/bin:$PATH" run sh "$REPO/installer/autoinstall/disk-guard.sh" /dev/sdb
    [ "$status" -ne 0 ]
    PATH="$TMP/bin:$PATH" run sh "$REPO/installer/autoinstall/disk-guard.sh" /dev/vdz
    [ "$status" -ne 0 ]
    PATH="$TMP/bin:$PATH" run sh "$REPO/installer/autoinstall/disk-guard.sh" ""
    [ "$status" -ne 0 ]
}

@test "kiosk falls back to moonlight-qt when the launcher is absent" {
    mkdir -p "$TMP/opt/bin" "$TMP/log"
    printf '#!/bin/sh\necho "MOONLIGHT $*"\n' >"$TMP/opt/bin/moonlight-qt"
    chmod +x "$TMP/opt/bin/moonlight-qt"
    COUCH_ROOT="$TMP/opt" COUCH_LOG_DIR="$TMP/log" run "$REPO/couch/bin/kiosk"
    [ "$status" -eq 0 ]
    [[ "$output" == "MOONLIGHT "* ]]
    grep -q 'moonlight-qt fallback' "$TMP/log/kiosk.log"
}

fake_launcher_root() { # $1 = exit code of the fake launcher
    mkdir -p "$TMP/opt/bin" "$TMP/opt/venv/bin" "$TMP/opt/app/couch" "$TMP/log" "$TMP/state"
    printf '#!/bin/sh\necho "MOONLIGHT $*"\n' >"$TMP/opt/bin/moonlight-qt"
    printf '#!/bin/sh\n[ "$1" = "-c" ] && exit 0\necho LAUNCHER; exit %s\n' "$1" >"$TMP/opt/venv/bin/python"
    chmod +x "$TMP/opt/bin/moonlight-qt" "$TMP/opt/venv/bin/python"
}

@test "kiosk prefers the launcher when the venv works" {
    fake_launcher_root 0
    COUCH_ROOT="$TMP/opt" COUCH_LOG_DIR="$TMP/log" COUCH_STATE_DIR="$TMP/state" run "$REPO/couch/bin/kiosk"
    [ "$status" -eq 0 ]
    [ "$output" = "LAUNCHER" ]
}

@test "kiosk falls back to moonlight-qt after 3 quick launcher crashes, then retries the launcher" {
    fake_launcher_root 1
    for i in 1 2 3; do
        COUCH_ROOT="$TMP/opt" COUCH_LOG_DIR="$TMP/log" COUCH_STATE_DIR="$TMP/state" run "$REPO/couch/bin/kiosk"
        [ "$output" = "LAUNCHER" ]
    done
    COUCH_ROOT="$TMP/opt" COUCH_LOG_DIR="$TMP/log" COUCH_STATE_DIR="$TMP/state" run "$REPO/couch/bin/kiosk"
    [[ "$output" == "MOONLIGHT"* ]]
    COUCH_ROOT="$TMP/opt" COUCH_LOG_DIR="$TMP/log" COUCH_STATE_DIR="$TMP/state" run "$REPO/couch/bin/kiosk"
    [ "$output" = "LAUNCHER" ]
}

@test "couch-doctor: fixture-controlled checks are green; output and exit code agree" {
    python3 "$REPO/tests/fixtures/make_sysroot.py" "$TMP/root" good
    PATH="$REPO/tests/fixtures/bin:$PATH" COUCH_SYSROOT="$TMP/root" COUCH_CONFIG="$TMP/root/etc/couch/couch.toml" \
        COUCH_MOONLIGHT="$REPO/tests/fixtures/bin/fake-moonlight-qt" FAKE_MOONLIGHT_MODE=list \
        run "$REPO/couch/doctor/couch-doctor"
    [[ "$output" == *"couch-doctor v1"* ]]
    [[ "$output" == *"[ OK ] display"* && "$output" == *"[ OK ] gamepad"* && "$output" == *"[ OK ] decode"* ]]
    [[ "$output" == *"[ OK ] network"* && "$output" == *"[ OK ] pairing"* ]]
    # sunshine/disk depend on the machine running the test; the exit code must match the overall verdict
    case "$output" in
        *"overall: GREEN"*) [ "$status" -eq 0 ] ;;
        *"overall: YELLOW"*) [ "$status" -eq 1 ] ;;
        *"overall: RED"*) [ "$status" -eq 2 ] ;;
        *) false ;;
    esac
}

@test "couch-doctor: no TV -> red, exit 2, JSON parses" {
    python3 "$REPO/tests/fixtures/make_sysroot.py" "$TMP/root" nodisplay
    PATH="$REPO/tests/fixtures/bin:$PATH" COUCH_SYSROOT="$TMP/root" run "$REPO/couch/doctor/couch-doctor" --json
    [ "$status" -eq 2 ]
    echo "$output" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["overall"]=="red"'
}
