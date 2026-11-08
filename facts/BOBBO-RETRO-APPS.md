# BOBBO retro frontends for the couchbox Sunshine app list (from the "Remote gaming stream setup" session, 2026-11-05)
Source of record on BOBBO: C:\Users\bobdo\projects\station-control\facts\RETRO-BASELINE.md (counts + systems only).

- App "Retro": ES-DE 3.5.0 portable, launch the exe DIRECTLY:
  C:\Users\bobdo\AppData\Roaming\EmuDeck\EmulationStation-DE\ES-DE.exe   (working dir = same folder)
  NOT D:\Emulation\tools\launchers\esde\EmulationStationDE.ps1 - that launcher git-fetches + hard-resets the EmuDeck backend on every start.
- App "PC classics": Playnite 10.55 Fullscreen (eXoDOS 7200 / eXoWin3x 1138 / eXoScummVM 387) - command line still to come from that session.
- Gamepad: XInput via ViGEm; every frontend/emulator reads XInput.
- Wired (22 systems, NTFS junctions D:\Emulation\roms\<sys> -> H:, no copies): nes snes genesis n64 gc wii mame fbneo dreamcast saturn + 12 FB Neo subsets.
  Alt emulators: mame = MAME 2003-Plus (0.78 set), switch = Ryujinx, saturn = YabaSanshiro (HLE).
- NOT playable yet: ps2 (643 zips -> CHD), xbox (1076 7z -> extract-xiso + xemu blank HDD), switch (408 xci zips; Michael's own keys), 3do (632, no BIOS);
  dreamcast/saturn multi-file discs need a one-game test.
