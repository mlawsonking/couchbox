# couchbox TESTPLAN (on the couch)

For Michael with the gamepad, after RUNBOOK sections 1-3. Each numbered item is one acceptance criterion from
GAMEPLAN.md section 1. Tick PASS or FAIL; for a FAIL, write what the TV said (kid line + "For grown-ups" line) and
run `/opt/couch/bin/couch-doctor` (or ask Fable to). Fable can pre-run the SSH parts (marked **[SSH]**).

Date: ________  NUC model: ________  TV: ________  Workstation: ________

## 1. Cold boot to launcher in <= 25 s, nothing else ever shows

1. Unplug the NUC's power for 10 s, plug it in, start a stopwatch when you press power.
2. Watch the TV: no login prompt, no desktop, no mouse cursor, no update message at any point.
3. Stop the stopwatch when the tiles appear. Time: ____ s

[ ] PASS  [ ] FAIL  notes: __________________

## 2. Gamepad on every screen; second pad; adapter replug

1. Move around all tiles with the D-pad, then the left stick. Start opens the menu, B closes it.
2. Turn on a second pad: it also moves the focus.
3. Pull the receiver out, count to 3, plug it back: the pad works again within 5 s. Switch the pad off and on:
   it works again without touching the box.
4. **[SSH]** `couch-doctor` gamepad line shows the pad as `045e:028e` ... `mapped yes`.

[ ] PASS  [ ] FAIL  notes: __________________

## 3. Steam Big Picture: from asleep <= 90 s, from awake <= 10 s

1. Put the PC to sleep (Start > Power > Sleep). Wait 2 minutes.
   (While streaming, the picture must fill the TV with no black bars: the ultrawide switches to the TV's mode and
   back when the stream ends.)
2. On the TV pick **Steam Big Picture**. TV shows "Waking up the game PC" with seconds counting.
3. Big Picture appears on the TV. Time from the press: ____ s (must be <= 90).
4. Quit to the launcher (item 5), pick **Steam Big Picture** again: ____ s (must be <= 10).
5. **[SSH]** `/opt/couch/bin/couch-streamtest --hold 60` prints PASS (the stream held 60 s with the kiosk's PID
   unchanged and quit cleanly). A FAIL "kiosk restarted during the stream" is the compositor/X-server crash class.

[ ] PASS  [ ] FAIL  notes: __________________

## 4. Per-game tiles with box art launch the game directly

1. Pick a game tile that shows its box art. The game itself starts (not Big Picture).
2. Repeat with a game without art: it shows a coloured card and still launches.

[ ] PASS  [ ] FAIL  notes: __________________

## 5. Quit returns to the launcher in <= 5 s, no leftovers

1. Quit the game normally: the launcher is back within 5 s.
2. Start a game again; press Moonlight's quit combo (Start + Back + LB + RB): launcher within 5 s.
3. Start a game again; hold the Xbox button 3 s: launcher within 5 s.
4. **[SSH]** `pgrep -fa moonlight` prints nothing after each quit.
5. **[SSH]** The last line of `/var/lib/couch/streams.jsonl` has `"exit_code": 0` and no `"killed"` (a quit that needed
   SIGKILL is exit -9 with `"sunshine_quit"`; the launcher has still ended the session on the PC, but that is a defect to
   report). Sunshine on the PC shows no running app. Worst case if moonlight-qt hangs: the launcher is back within 17 s.
6. While streaming, the picture fills the whole TV (not a window in the middle): `couch-streamtest` checks
   `xwininfo` geometry == X screen, or look.

[ ] PASS  [ ] FAIL  notes: __________________

## 6. Workstation network pulled mid-stream -> kid screen with Retry, no hang

1. Start a game. Pull the PC's network cable (or turn off its Wi-Fi).
2. Within ~30 s the TV shows "We lost the game PC." with **A Try again / B Home** and a grown-up line.
3. Plug the cable back in, press **A**: the game streams again.

[ ] PASS  [ ] FAIL  notes: __________________

## 7. Router reboot (new addresses) -> next launch still works

1. Reboot the Google Wifi (unplug the main point 30 s). Wait until everything is back online.
2. Pick a tile: it wakes/finds the PC and streams.
3. **[SSH]** `curl -s http://127.0.0.1:7777/status` shows how the PC was found (`name`, `arp` or `cache`).

[ ] PASS  [ ] FAIL  notes: __________________

## 8. couch-doctor all green and the LAN page agrees

1. **[SSH]** `/opt/couch/bin/couch-doctor` - every line `[ OK ]` (or `[SKIP]` for streams/drivers before first use).
2. **[SSH]** `curl -s -XPOST -H 'X-Couch: 1' -d '{"measure":true}' http://127.0.0.1:7777/doctor` - network line
   shows Mbps and a quality rung; write them: ____ Mbps, jitter ____ ms, loss ____ %, rung ________
3. On a phone/laptop on the Wi-Fi open `http://couchbox.local:7777/` - same verdict as the doctor.

[ ] PASS  [ ] FAIL  notes: __________________

## 9. Nothing on the box can be broken from the couch

1. Press every button on every screen (including X, Y, LB, RB, Back, sticks): only navigation, A/B, Start menu,
   and the Guide-hold quit do anything.
2. The Start menu has only **Restart the box**, **Pair with the game PC** and **Back**.
3. Pair needs the PIN typed on the PC; Restart reboots and comes back to the launcher (item 1 timing).

[ ] PASS  [ ] FAIL  notes: __________________

## 10. Repo greps clean

1. **[SSH or any checkout]** `scripts/check-secrets.sh` prints `secret gate: clean`.
2. The only place the real MAC and host name exist is `/etc/couch/couch.toml` on the NUC.

[ ] PASS  [ ] FAIL  notes: __________________

## Extra (not acceptance, worth trying once)

- Leave HOME untouched 10 min: screen dims; 60 min: goes black; any button wakes it without launching anything.
  [ ] PASS  [ ] FAIL
- Kernel update night: next morning `couch-doctor` drivers line is OK. [ ] PASS  [ ] FAIL
