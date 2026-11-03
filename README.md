# couchbox

A NUC on one TV input that boots straight into a gamepad-only launcher and streams a Windows gaming workstation's
games via Moonlight (client, on the NUC) and Sunshine (host, on the workstation). Self-healing, no desktop, no mouse,
nothing a kid can break.

- `BRIEF.md` - what the cloud session builds, in phase order, with the guardrails and test gates. Start here.
- `GAMEPLAN.md` - the decisions (Ubuntu 24.04 + cage kiosk, pygame-ce launcher, moonlight-qt engine, PS5.1 workstation
  kit) and why, the acceptance list, the test framework, Michael's gates, the risks.
- `facts/lan-facts.json` - the few facts about the home network and machines you may rely on (read-only; nothing in it
  goes into code).
- `installer/authorized_keys` - the media server's SSH public key, installed for the `couchadmin` user so the author
  can validate the NUC remotely. Public key only.

The cloud session fills in the rest (`installer/`, `couch/`, `workstation/`, `tests/`, `docs/`, CI) and opens a PR to
`main` at the end of every phase.
