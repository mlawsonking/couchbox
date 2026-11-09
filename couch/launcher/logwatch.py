"""Reading moonlight-qt's own log while a stream starts: fatal connection messages and the Sunshine app id.

moonlight-qt does not exit when a connection fails: it shows an error dialog and keeps running (NUC, 2026-11-09:
"No video received from host" 17 s after the start, process alive for minutes). The only evidence is its log. Lines look
like `00:00:17 - Qt Critical: No video received from host.` / `00:00:17 - SDL Error (0): Connection terminated: -100`.

The patterns are deliberately NOT "any error-level line": healthy starts on the NUC log `SDL Error (0):
pl_vk_inst_create() failed` and `SDL Critical (0): libplacebo: ...` (the Vulkan renderer is tried first and falls back
to EGL), and every normal quit logs `Control stream connection failed: 4` at info level. Callers only look inside the
first seconds of a stream (state.FAIL_WATCH): after the stream is up, loss/RFI lines are normal."""
from __future__ import annotations

import os
import re

# (compiled regex, group "m" = the message). Case-insensitive where the wording is Qt's/ours rather than moonlight's.
FATAL = [
    re.compile(r" - Qt (?:Critical|Fatal): (?P<m>.+)"),                          # the user-visible error dialogs
    re.compile(r" - SDL Error \(\d+\): (?P<m>Connection terminated.*)"),
    re.compile(r" - (?:SDL|Qt) (?:Error|Warn|Warning|Critical)[^:]*: (?P<m>Starting .+ failed.*)"),   # stageFailed
    re.compile(r" - (?:SDL|Qt) (?:Error|Warn|Warning|Critical)[^:]*: "
               r"(?P<m>.*(?:failed to connect|unable to connect|host is not reachable|not reachable|connection timed out"
               r"|connection refused|no route to host).*)", re.I),
]
STAMP = re.compile(r"^\d\d:\d\d:\d\d - ")
APPID = re.compile(r"/launch\?[^\"\s]*?\bappid=(?P<id>\d+)")


def find_failure(text: str) -> str | None:
    """The first fatal connection message in `text` (log lines), with its continuation lines, or None."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for pat in FATAL:
            m = pat.search(line)
            if m:
                msg = [m.group("m").strip()]
                for nxt in lines[i + 1:i + 4]:                    # "...\n\nCheck your firewall and port forwarding..."
                    if STAMP.match(nxt):
                        break
                    if nxt.strip():
                        msg.append(nxt.strip())
                return " ".join(msg)[:240]
    return None


def find_app_id(text: str) -> str | None:
    m = APPID.search(text)
    return m.group("id") if m else None


class LogScanner:
    """Incremental reader of the moonlight log from a start offset: complete lines only, each byte read once."""

    def __init__(self, path: str, start: int = 0):
        self.path, self.pos = path, start
        self.failure: str | None = None
        self.app_id: str | None = None

    def poll(self) -> None:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.pos)
                data = fh.read(1 << 17)
        except OSError:
            return
        cut = data.rfind(b"\n")
        if cut < 0:
            return
        self.pos += cut + 1
        text = data[:cut].decode("utf-8", "replace")
        if self.app_id is None:
            self.app_id = find_app_id(text)
        if self.failure is None:
            self.failure = find_failure(text)


def size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
