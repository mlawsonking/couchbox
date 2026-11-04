#!/bin/bash
# Container rehearsal of installer/install.sh inside a pristine ubuntu:24.04.
#   tests/rehearsal/rehearse.sh [plain|systemd]
# plain   : --no-services (no optional DKMS drivers); asserts every file/dir/user lands, 2nd run = "changes: 0", dry-run = 0 planned.
# systemd : systemd installed but not PID 1 (like the autoinstall chroot); asserts getty mask, enabled units,
#           first-boot marker, systemd-analyze verify of the couch units, and idempotence.
# Uses a fake moonlight-qt AppImage (pinned by its own sha256) so no 54 MB download happens in CI.
set -euo pipefail
MODE="${1:-plain}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${REHEARSAL_IMAGE:-ubuntu:24.04}"
PROXY_ARGS=()
for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY; do
    if [ -n "${!v:-}" ]; then PROXY_ARGS+=(-e "$v=${!v}"); fi
done
# A TLS-intercepting proxy (development sandboxes) needs its CA inside the container for pip.
if [ -n "${PIP_CERT:-}" ] && [ -f "$PIP_CERT" ]; then
    PROXY_ARGS+=(-v "$PIP_CERT:/etc/couch-test-ca.crt:ro" -e PIP_CERT=/etc/couch-test-ca.crt)
fi
exec docker run --rm --network host "${PROXY_ARGS[@]}" -v "$REPO:/src:ro" -e MODE="$MODE" "$IMAGE" \
    bash /src/tests/rehearsal/in-container.sh
