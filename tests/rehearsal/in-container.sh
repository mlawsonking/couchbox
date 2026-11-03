#!/bin/bash
# Runs INSIDE the rehearsal container (see rehearse.sh). Fails loudly on the first broken assertion.
set -euo pipefail
fail() { echo "REHEARSAL FAIL: $*" >&2; exit 1; }
pass() { echo "REHEARSAL ok: $*"; }

export DEBIAN_FRONTEND=noninteractive
if [ -n "${http_proxy:-}${HTTP_PROXY:-}" ]; then
    printf 'Acquire::http::Proxy "%s";\n' "${http_proxy:-$HTTP_PROXY}" >/etc/apt/apt.conf.d/99proxy
fi
apt-get update -qq >/dev/null
if [ "$MODE" = systemd ]; then
    apt-get install -y -qq --no-install-recommends systemd systemd-sysv >/dev/null
fi

# A writable copy of the repo (the installer copies itself to /opt/couch/src) with a fixture moonlight pin.
cp -a /src /work
rm -rf /work/.git
FAKE=/work/tests/fixtures/fake-moonlight.AppImage
SHA="$(sha256sum "$FAKE" | cut -d' ' -f1)"
sed -e "s|^MOONLIGHT_URL=.*|MOONLIGHT_URL=\"file://$FAKE\"|" -e "s|^MOONLIGHT_SHA256=.*|MOONLIGHT_SHA256=\"$SHA\"|" \
    /work/installer/pins.env >/tmp/pins.env

ARGS=(--no-dkms --pins /tmp/pins.env)
if [ "$MODE" = plain ]; then ARGS+=(--no-services); fi

echo "---- dry run on a pristine system"
/work/installer/install.sh --dry-run "${ARGS[@]}" | tee /tmp/dry1.log
[ ! -e /opt/couch ] || fail "dry run created /opt/couch"
[ ! -e /etc/couch ] || fail "dry run created /etc/couch"
! id couch >/dev/null 2>&1 || fail "dry run created user couch"
grep -q 'PLAN    apt-get install' /tmp/dry1.log || fail "dry run did not plan the packages"
pass "dry run changed nothing and printed a plan"

echo "---- bad pin is refused"
sed "s|^MOONLIGHT_SHA256=.*|MOONLIGHT_SHA256=\"$(printf '0%.0s' $(seq 64))\"|" /tmp/pins.env >/tmp/badpins.env
BAD_ARGS=("${ARGS[@]}"); BAD_ARGS[2]=/tmp/badpins.env
if /work/installer/install.sh "${BAD_ARGS[@]}" >/tmp/bad.log 2>&1; then fail "install accepted a wrong sha256"; fi
grep -q 'sha256 mismatch' /tmp/bad.log || fail "wrong sha256 not reported"
[ ! -e /opt/couch/moonlight/current ] || fail "moonlight installed despite bad sha"
pass "wrong moonlight sha256 refused"

echo "---- first run"
/work/installer/install.sh "${ARGS[@]}" | tee /tmp/run1.log
echo "---- second run"
/work/installer/install.sh "${ARGS[@]}" | tee /tmp/run2.log
grep -q '^changes: 0, ' /tmp/run2.log || fail "second run was not a no-op: $(grep -E '^  (change|PLAN)' /tmp/run2.log | head)"
pass "second run is a no-op"
/work/installer/install.sh --dry-run "${ARGS[@]}" | tee /tmp/dry2.log
grep -q 'changes planned: 0' /tmp/dry2.log || fail "dry run after install still plans changes"
pass "dry run after install plans nothing"

check_mode() { # path mode owner
    [ -e "$1" ] || fail "missing $1"
    local cur; cur="$(stat -c '%a %U:%G' "$1")"
    [ "$cur" = "$2 $3" ] || fail "$1 is $cur, want $2 $3"
}
check_mode /opt/couch/bin/kiosk 755 root:root
check_mode /opt/couch/bin/couch-doctor 755 root:root
check_mode /opt/couch/bin/moonlight-qt 755 root:root
check_mode /etc/couch 750 root:couch
check_mode /etc/couch/couch.toml 640 root:couch
check_mode /var/lib/couch/home 750 couch:couch
check_mode /var/log/couch 775 root:couch
check_mode /etc/systemd/system/couch-kiosk.service 644 root:root
check_mode /etc/systemd/system/couch-firstboot.service 644 root:root
check_mode /etc/systemd/journald.conf.d/couch.conf 644 root:root
check_mode /etc/apt/apt.conf.d/52couch-unattended-upgrades 644 root:root
check_mode /etc/apt/apt.conf.d/21couch-auto-upgrades 644 root:root
[ -s /var/log/couch/install.log ] || fail "no install log"
# shellcheck source=/dev/null
[ "$(readlink /opt/couch/moonlight/current)" = "$(. /tmp/pins.env; echo "$MOONLIGHT_VERSION")" ] || fail "moonlight current link"
/opt/couch/bin/moonlight-qt --version || fail "moonlight wrapper does not run"
[ -f /opt/couch/src/installer/install.sh ] || fail "source copy missing"
pass "files, modes and owners"

getent passwd couch | grep -q ':/usr/sbin/nologin$' || fail "couch has a login shell"
for g in video input render audio; do id -nG couch | tr ' ' '\n' | grep -qx "$g" || fail "couch not in $g"; done
getent passwd couchadmin >/dev/null || fail "couchadmin missing"
cmp -s /work/installer/authorized_keys /home/couchadmin/.ssh/authorized_keys || fail "couchadmin key differs"
check_mode /home/couchadmin/.ssh/authorized_keys 600 couchadmin:couchadmin
passwd -S couchadmin | awk '{print $2}' | grep -qx L || fail "couchadmin password not locked"
! id -nG couchadmin | tr ' ' '\n' | grep -qx sudo || fail "couchadmin must not have sudo"
pass "users"

echo "local edit" >>/etc/couch/couch.toml
/work/installer/install.sh "${ARGS[@]}" >/tmp/run3.log
grep -q 'local edit' /etc/couch/couch.toml || fail "installer overwrote an edited couch.toml"
pass "edited couch.toml preserved"

echo "---- installer touched only its own paths"
python3 /work/tests/rehearsal/stray_writes.py /tmp/pins.env || fail "installer wrote outside its paths"
pass "no stray writes"

if [ "$MODE" = systemd ]; then
    [ "$(readlink /etc/systemd/system/getty@tty1.service)" = /dev/null ] || fail "getty@tty1 not masked"
    [ -L /etc/systemd/system/multi-user.target.wants/couch-kiosk.service ] || fail "kiosk not enabled"
    [ -L /etc/systemd/system/multi-user.target.wants/couch-firstboot.service ] || fail "firstboot not enabled"
    [ -e /var/lib/couch/firstboot-pending ] || fail "firstboot marker missing (deferred mode)"
    grep -q 'deferred to first boot' /tmp/run1.log || fail "deferred mode not announced"
    systemd-analyze verify /etc/systemd/system/couch-kiosk.service /etc/systemd/system/couch-firstboot.service \
        2>&1 | tee /tmp/verify.log
    if grep -E 'couch-' /tmp/verify.log | grep -viE 'Failed to (connect|create|get|resolve).*bus|dbus|No such file or directory.*bus'; then
        fail "systemd-analyze verify reported problems in couch units"
    fi
    pass "systemd: getty masked, units enabled, first boot pending, units verify"
fi
echo "REHEARSAL PASSED ($MODE)"
