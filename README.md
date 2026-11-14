# SyncWatch

Watch YouTube (or anything yt-dlp supports) together. Every peer streams the
video itself; the peers only exchange the queue and the playback state, so
pausing, seeking and skipping stay in sync for everyone (typically within a
few tens of milliseconds).

- Video plays in its own window that stays **on top of all other windows**.
  Turn this off with the checkbox, or press **T** in the video window.
- The control window has a URL field, the shared queue, transport controls,
  and the session panel.
- Peer to peer: there is no server and everyone has equal control. Each peer
  connects directly to every other peer, so the session keeps going when
  anyone leaves, including the person who started it.
- Sessions on your LAN show up automatically. You can also join directly by
  `host:port`, which works over Tailscale/ZeroTier/VPNs or with port
  forwarding.

## Install

You need Python 3.10+, **libmpv**, and (recommended) **Deno**, which yt-dlp
uses to solve YouTube's JavaScript challenges. Without it, some videos fail or
only offer low quality.

### Windows (ready-made bundle)

Unzip `SyncWatch-windows-x64.zip` anywhere and run `SyncWatch\SyncWatch.exe`.
It includes Python, Qt, libmpv, yt-dlp and Deno, so nothing else is needed.

YouTube changes often and old yt-dlp versions stop working. If videos stop
loading, update the bundled copy by running `SyncWatch\_internal\yt-dlp.exe -U`.

To build the bundle yourself from Linux with Wine:

```
nix shell nixpkgs#wineWow64Packages.stable nixpkgs#pkgsCross.mingwW64.buildPackages.gcc \
  nixpkgs#p7zip nixpkgs#unzip nixpkgs#zip nixpkgs#curl nixpkgs#xvfb-run \
  -c xvfb-run -a packaging/windows/build.sh
```

This writes `build/windows/SyncWatch-windows-x64.zip`.

### Windows (from source)

1. Install Python from python.org and Deno: `winget install DenoLand.Deno`
2. In this folder:
   ```
   py -m venv .venv
   .venv\Scripts\pip install .
   ```
3. Get libmpv. Download the latest `mpv-dev-x86_64-…7z` from
   <https://github.com/shinchiro/mpv-winbuild-cmake/releases> and extract
   `libmpv-2.dll` into this folder (or anywhere on `PATH`).
4. Run `.venv\Scripts\syncwatch` (or `.venv\Scripts\python -m syncwatch`
   from this folder).

Allow it through Windows Firewall when asked, on **private** networks at
least. Otherwise nobody can join you.

### Linux

Install libmpv from your distro: `libmpv2` (Debian/Ubuntu), `mpv-libs`
(Fedora), or `mpv` (Arch). Then:

```
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/syncwatch
```

Install Deno too (package manager or <https://deno.land>).

**NixOS / Nix:** `nix run .` to run it, or `nix develop -c python -m syncwatch`.

**Wayland:** SyncWatch runs through XWayland automatically. Wayland doesn't let
apps keep their own window on top, and libmpv embeds into X11 windows. Most
compositors (KDE, GNOME) honour "always on top" for XWayland windows. On
tiling compositors you may need a window rule instead (e.g. Hyprland `pin`).

## Use

- **Start a session:** just open the app. You're already "hosting". Others on
  your LAN see you under *Found on your network*. People elsewhere join you
  at one of the addresses shown (default port **47801**/TCP; discovery uses
  **47800**/UDP).
- **Join:** pick a session and click *Join selected*, or type `host:port`.
  Joining replaces your queue with the session's.
- **Add videos:** paste a URL and press Enter (*Play next* puts it right after
  the current video). Playlist URLs (`…/playlist?list=…`) add every entry.
- **Queue:** double-click to play an item, or use Up/Down/Remove. Played items
  are greyed out until you *Clear played*.
- **Captions:** tick **CC** and pick a language code (`en`, `es`, `ja`, …),
  or press **C** in the video window. The uploader's captions are used when
  they exist, otherwise YouTube's auto-generated ones. Changing the language
  reloads the video at the shared position. YouTube often rate-limits its
  machine-*translated* captions (e.g. Spanish captions on an English video),
  so those can take a while to appear or not appear at all. SyncWatch picks
  real caption tracks over translations whenever a video has them.
- **Caption style:** **Style…** next to CC sets font, size, bold, text colour
  and opacity, outline, shadow, a background box (colour and opacity) and the
  distance from the bottom, with presets such as "TV style". Changes show on
  the video immediately.
- **Video window shortcuts:** Space = play/pause, ←/→ = seek 10 s,
  F or double-click = fullscreen, T = toggle always-on-top, C = captions.
- Volume and captions are per viewer. Everything else is shared.

To run two instances on one machine (for testing), give them separate
profiles: `syncwatch --profile a` and `syncwatch --profile b`. The second
one picks the next free port automatically.

## How sync works

- **Shared state** is a small CRDT. Queue items are add-only, with sticky
  removal and last-writer-wins ordering. Playback (item, paused, position) is
  a single last-writer-wins register stamped with Lamport clocks. Any peer can
  change anything, and all copies converge, even when two people act at the
  same moment.
- **No clock sync needed.** A peer sends the playback position *as of the
  moment of sending*, and the receiver adds half the measured round-trip time
  to that peer.
- **Following the state:** each player compares mpv's position with the shared
  position 10× a second. Small drift is corrected by nudging playback speed
  (±8 %, with pitch correction). Large drift triggers a seek slightly ahead,
  then a short hold until everyone else catches up. The seek lead adapts to
  how long seeks take on that connection.
- New videos start after a 3-second countdown so every peer has time to
  load.

## Security note

Peers can only make each other play `http(s)` URLs through mpv/yt-dlp, but
anyone who can reach your port can join and control playback. Only expose the
port to networks you trust.

## Development

```
nix develop -c python -m unittest discover -s tests   # or with your venv's python
```

`tests/test_state.py` covers CRDT convergence (including randomized
concurrent edits). `tests/test_mesh.py` checks that three peers form a full
mesh, converge, and survive a member leaving.
