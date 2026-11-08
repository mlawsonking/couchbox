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

ARGS=(--pins /tmp/pins.env)
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
BAD_ARGS=(--pins /tmp/badpins.env)
if [ "$MODE" = plain ]; then BAD_ARGS+=(--no-services); fi
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
check_mode /opt/couch/bin/couch-streamtest 755 root:root
check_mode /opt/couch/bin/couch-display-power 755 root:root
check_mode /etc/X11/Xwrapper.config 644 root:root
grep -q '^allowed_users=anybody' /etc/X11/Xwrapper.config || fail "Xwrapper.config does not allow the service user to start Xorg"
grep -q 'xinit /opt/couch/bin/kiosk -- /usr/bin/X :0 vt1' /etc/systemd/system/couch-kiosk.service || fail "kiosk unit does not start Xorg via xinit"
! grep -q '^ExecStart=.*cage' /etc/systemd/system/couch-kiosk.service || fail "kiosk unit still runs cage"
/opt/couch/bin/couch-streamtest --help >/dev/null || fail "couch-streamtest --help"
grep -q 'xvfb-run' /opt/couch/bin/moonlight-qt || fail "moonlight wrapper lacks the headless xvfb branch"
dpkg-query -W -f='${Status}' xserver-xorg-core xinit xserver-xorg-legacy | grep -c 'install ok installed' | grep -qx 3 || fail "Xorg/xinit/legacy wrapper packages missing"
check_mode /opt/couch/bin/moonlight-qt 755 root:root
check_mode /opt/couch/app/couch/launcher/app.py 644 root:root
check_mode /etc/couch 750 root:couch
check_mode /etc/couch/couch.toml 640 root:couch
check_mode /var/lib/couch/home 750 couch:couch
check_mode /var/log/couch 775 root:couch
check_mode /etc/systemd/system/couch-kiosk.service 644 root:root
check_mode /etc/systemd/system/couch-firstboot.service 644 root:root
check_mode /etc/systemd/system/couch-daemon.service 644 root:root
check_mode /etc/systemd/system/couch-maint.timer 644 root:root
check_mode /etc/kernel/postinst.d/zz-couch-xone-check 755 root:root
check_mode /opt/couch/bin/couch-update 755 root:root
check_mode /opt/couch/bin/couch-maint 755 root:root
check_mode /etc/systemd/journald.conf.d/couch.conf 644 root:root
check_mode /etc/apt/apt.conf.d/52couch-unattended-upgrades 644 root:root
check_mode /etc/apt/apt.conf.d/21couch-auto-upgrades 644 root:root
[ -s /var/log/couch/install.log ] || fail "no install log"
# shellcheck source=/dev/null
[ "$(readlink /opt/couch/moonlight/current)" = "$(. /tmp/pins.env; echo "$MOONLIGHT_VERSION")" ] || fail "moonlight current link"
/opt/couch/bin/moonlight-qt --version || fail "moonlight wrapper does not run"
[ -f /opt/couch/src/installer/install.sh ] || fail "source copy missing"
pass "files, modes and owners"

check_mode /etc/udev/rules.d/70-couch-gamepads.rules 644 root:root
check_mode /etc/couch/gamepads.txt 664 couch:couch
[ ! -e /etc/modprobe.d/couch-xone-blacklist.conf ] || fail "xpad blacklisted by default (would break 045E:028E receivers)"
dpkg-query -W -f='${Status}' intel-media-va-driver | grep -q 'install ok installed' || fail "intel-media-va-driver (iHD) missing"
pass "gamepads: udev rule, writable gamepads.txt, xpad NOT blacklisted; iHD VA-API driver installed"
printf 'blacklist xpad\n' >/etc/modprobe.d/couch-xone-blacklist.conf
/work/installer/install.sh "${ARGS[@]}" >/tmp/run-bl.log
[ ! -e /etc/modprobe.d/couch-xone-blacklist.conf ] || fail "stale xpad blacklist from an earlier --with-xone not removed"
/work/installer/install.sh "${ARGS[@]}" | grep -q '^changes: 0, ' || fail "not idempotent after blacklist removal"
pass "a stale xone blacklist is removed when xone is not requested"

/opt/couch/venv/bin/python -m pip list --format=freeze 2>/dev/null | grep -v '^pip==' >/tmp/pip.txt
[ "$(cat /tmp/pip.txt)" = "pygame-ce==2.5.2" ] || fail "venv holds more than pygame-ce: $(cat /tmp/pip.txt)"
(cd /opt/couch/app && setpriv --reuid=couch --regid=couch --init-groups env HOME=/var/lib/couch/home \
    /opt/couch/venv/bin/python -m couch.launcher --selftest) || fail "launcher selftest failed as user couch"
pass "venv has only pygame-ce; launcher renders every screen as user couch"

echo "---- couch-daemon as user couch (stdlib python3 of the target), real HTTP"
(cd /opt/couch/app && setpriv --reuid=couch --regid=couch --init-groups env HOME=/var/lib/couch/home \
    COUCH_DAEMON_PORT=17777 python3 -m couch.daemon >/tmp/daemon.log 2>&1 &)
for _ in $(seq 50); do
    python3 -c 'import urllib.request;urllib.request.urlopen("http://127.0.0.1:17777/health",timeout=1)' 2>/dev/null && break
    sleep 0.2
done
python3 - <<'PY' || fail "daemon checks failed: $(tail -5 /tmp/daemon.log)"
import json, urllib.request, urllib.error
def get(path, method="GET", hdr=True):
    r = urllib.request.Request("http://127.0.0.1:17777" + path, method=method, data=b"{}" if method == "POST" else None,
                               headers={"X-Couch": "1"} if hdr else {})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, None
assert get("/health")[0] == 200
assert get("/status")[0] == 200
assert get("/exec", "POST")[0] == 404
assert get("/wake", "POST", hdr=False)[0] == 403
print("daemon ok")
PY
pkill -f "couch.daemon" || true
pass "couch-daemon serves /health and enforces the allow-list in the target environment"

/opt/couch/bin/couch-update --status >/dev/null || fail "couch-update --status"
mkdir -p /tmp/lib/art && printf '{"games":[{"appid":504230,"name":"Celeste","installed":true}]}' >/tmp/lib/steam-library.json
printf '\377\330\377\340fakejpeg' >/tmp/lib/art/504230.jpg
/opt/couch/bin/couch-update --import /tmp/lib || fail "couch-update --import"
[ "$(stat -c '%U' /var/lib/couch/art/504230.jpg)" = couch ] || fail "imported art not owned by couch"
grep -q Celeste /var/lib/couch/library.json || fail "library not imported"
pass "couch-update --status / --import"

getent passwd couch | grep -q ':/usr/sbin/nologin$' || fail "couch has a login shell"
for g in video input render audio; do id -nG couch | tr ' ' '\n' | grep -qx "$g" || fail "couch not in $g"; done
getent passwd couchadmin >/dev/null || fail "couchadmin missing"
cmp -s /work/installer/authorized_keys /home/couchadmin/.ssh/authorized_keys || fail "couchadmin key differs"
check_mode /home/couchadmin/.ssh/authorized_keys 600 couchadmin:couchadmin
[ "$(getent shadow couchadmin | cut -d: -f2)" = '*' ] || fail "couchadmin shadow field must be '*' (no password, keys allowed), not a '!' lock that sshd refuses"
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
    [ -L /etc/systemd/system/multi-user.target.wants/couch-daemon.service ] || fail "daemon not enabled"
    [ -L /etc/systemd/system/timers.target.wants/couch-maint.timer ] || fail "maint timer not enabled"
    [ -e /var/lib/couch/firstboot-pending ] || fail "firstboot marker missing (deferred mode)"
    grep -q 'deferred to first boot' /tmp/run1.log || fail "deferred mode not announced"
    systemd-analyze verify /etc/systemd/system/couch-kiosk.service /etc/systemd/system/couch-firstboot.service \
        /etc/systemd/system/couch-daemon.service /etc/systemd/system/couch-maint.service \
        /etc/systemd/system/couch-maint.timer \
        2>&1 | tee /tmp/verify.log
    if grep -E 'couch-' /tmp/verify.log | grep -viE 'Failed to (connect|create|get|resolve).*bus|dbus|No such file or directory.*bus'; then
        fail "systemd-analyze verify reported problems in couch units"
    fi
    pass "systemd: getty masked, units enabled, first boot pending, units verify"
fi
echo "REHEARSAL PASSED ($MODE)"
