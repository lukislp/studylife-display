# StudyLife Display

[![CI](https://github.com/lukislp/studylife-display/actions/workflows/ci.yml/badge.svg)](https://github.com/lukislp/studylife-display/actions/workflows/ci.yml) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lukislp/studylife-display/badge)](https://scorecard.dev/viewer/?uri=github.com/lukislp/studylife-display) [![CodeQL](https://github.com/lukislp/studylife-display/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/lukislp/studylife-display/security/code-scanning)
[![Release](https://img.shields.io/github/v/release/lukislp/studylife-display)](https://github.com/lukislp/studylife-display/releases)
[![License: AGPL-3.0](https://img.shields.io/github/license/lukislp/studylife-display)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB)](https://www.python.org/)

A study dashboard for [StudyLife](https://github.com/lukislp/studylife) on a 7.5" e-paper
panel: a Raspberry Pi on the desk that shows, without a screen to unlock or a tab to find,
how today is going. It reads three read-only endpoints every five minutes and redraws the
panel; between refreshes the Pi and the panel sleep. Four layouts are built in, an "auto"
mode picks between them, and a small web interface on the Pi switches them from a phone.

## What it shows

![Preview of the rendered dashboard](docs/preview.png)

This is the `classic` layout; the others are under [Layouts](#layouts).

| Element | Source |
| --- | --- |
| Hours studied **today** (`H:MM`) | Summed from `GET /api/sessions/history` per local calendar day |
| **Serie** (streak) | `metrics/summary` → `streak.current` |
| **Nächste Prüfung** with an inverted countdown block and the course | `metrics/summary` → `upcomingCourseGoals[0]` |
| **Wochenziel** bar with the min/max target ticks | `metrics/summary` → `weekQuota` |
| **Letzte 4 Wochen** heatmap, 28 days ending today, four fill levels | `sessions/history` |
| Programme name and hours per 7 days | `metrics/summary` → `program.name`, `hours.week` |
| **Timer läuft · Fokus/Pause · endet HH:MM** while a session runs | `GET /api/timerstate` |
| `aktualisiert HH:MM · vor N min` | Set when the last fetch failed and a cached snapshot is shown |

The text is German by default; `DISPLAY_LANGUAGE=en` switches every label. Heatmap levels:
empty = no session, light hatch = under 1 h, dense hatch = under 2.5 h, solid = 2.5 h and more.

## Layouts

| Key | Shows | Preview |
| --- | --- | --- |
| `classic` | Everything above at a glance: today's hours, streak, countdown, week target, heatmap, timer line | ![classic](docs/preview-classic.png) |
| `focus` | The running timer as the hero: remaining time of the phase (`MM:SS`, a snapshot as of the refresh, not a live tick), "Fokus"/"Pause" and the round; without a timer, today's hours and "kein Timer aktiv". One line with streak and next exam underneath | ![focus](docs/preview-focus.png) |
| `exam` | The countdown as the hero: inverted "in N Tagen" block, course and date; below it hours per course over the last 28 days (top 5, from the session history) and a streak/today line | ![exam](docs/preview-exam.png) |
| `week` | The week target as a large bar with hours, target range and percent; the 4-week heatmap large with weekday initials and per-week sums; today's hours and streak at the bottom | ![week](docs/preview-week.png) |

Every layout keeps the header line (date, "aktualisiert HH:MM" and the stale marker), because
that line is the only way to tell an old frame from a fresh one.

`auto` (the default) picks per refresh:

1. `exam` when the next course goal is due in **7 days or fewer** (today, overdue and
   negative counts included);
2. otherwise `focus` while a timer is running;
3. otherwise `classic`.

The choice comes from, in order of precedence, `settings.json` next to the cached snapshot
(written by the web interface) and the `DISPLAY_LAYOUT` variable. Switching layouts is a full
refresh of the panel like every other update. `studylife-display preview --sample --layout
<key|auto> --out frame.png` renders any of them without a panel or an instance.

## Web interface

`studylife-display serve` runs a small page on the Pi (port **8795**, `DISPLAY_WEB_BIND`)
that shows a preview of every layout plus `auto`, lets you pick one ("Übernehmen" saves the
choice and refreshes the panel right away) and has a "Jetzt aktualisieren" button for a
refresh without a change. It is reachable wherever the Pi is: on the LAN as
`http://<hostname>.local:8795/`, or over Tailscale/WireGuard if the Pi is in such a network.
It is plain HTTP for the LAN; do not port-forward it to the internet.

- **The token is yours to choose.** `DISPLAY_WEB_TOKEN` (at least 12 characters) is asked for
  on the login page; the installer suggests a random one and writes it into the root-owned
  environment file, and `serve` refuses to start when the variable is empty or short. The
  code never ships a default.
- A correct token (compared in constant time; a wrong one costs a one-second delay and a 403)
  sets the cookie `studylife_display_session`: `HttpOnly`, `SameSite=Strict`, an HMAC of the
  token under a key drawn at process start. Sessions therefore end when the service restarts,
  and the token itself is never stored in the browser. Every state-changing request also
  requires a same-origin `Sec-Fetch-Site`/`Origin` header.
- **What it never does:** nothing on the browser path calls the StudyLife API. Previews are
  drawn from the cached payloads (or from sample data, marked as such, before the first
  successful fetch), and a refresh runs the same pipeline as the five-minute timer: fetch
  with cache fallback, render, show. The timer's `run` and the web service share the panel
  through a lock file (`panel.lock` in the state directory), so a click never collides with
  the scheduled refresh; the loser waits up to 60 s.
- Standard library only (`http.server`), inline CSS, no external resources, usable on a phone.
  One log line per request on stderr, never containing the token or the cookie.

## Hardware

- Raspberry Pi 3 Model A+ (any Pi with the 40-pin header works; the 3A+ is small, fanless and
  has Wi-Fi)
- [Waveshare 7.5inch e-Paper HAT (V2)](https://www.waveshare.com/7.5inch-e-paper-hat.htm),
  800 x 480, black/white. The V2 is the current panel with the 24-pin FPC connector.
- Official micro-USB power supply (5.1 V / 2.5 A); the panel draws almost nothing but the Pi
  browns out on a phone charger during Wi-Fi bursts
- A microSD card (8 GB is plenty) and, optionally, a frame

### Wiring

None to speak of: the driver board plugs onto the 40-pin header as a HAT, and the panel's
FPC cable goes into the driver board's connector (contacts facing the board, latch closed).
Set the driver board's switches to **B** (0.47R, the setting for the V2 panel) and **0**
(4-line SPI). No soldering anywhere.

## StudyLife setup

Register a client on your StudyLife instance through
[studylife-developers](https://github.com/lukislp/studylife-developers) and issue an API key
with exactly these read-only scopes:

| Scope | Endpoint |
| --- | --- |
| `Metrics.GetSummary` | `GET /api/metrics/summary` |
| `Sessions.GetHistory` | `GET /api/sessions/history?days=28&onlyCompleted=true` |
| `TimerState.Get` | `GET /api/timerstate` |

`Auth.Whoami` is implied. Nothing here writes: the display cannot start, stop or change a
session, so a key that ends up on a lost SD card can only ever read your study statistics.

Configuration (environment, or `/etc/studylife-display.env` on the Pi):

| Variable | Default | Meaning |
| --- | --- | --- |
| `STUDYLIFE_BASE_URL` | – | Your instance, e.g. `https://studylife.example.com` |
| `STUDYLIFE_API_KEY` | – | The key from above |
| `STUDYLIFE_TIMEZONE` | `Europe/Berlin` | Time zone of the **server**; its timestamps carry no offset |
| `DISPLAY_LANGUAGE` | `de` | `de` or `en` |
| `DISPLAY_DRIVER` | `waveshare` | `waveshare` (the panel) or `file` (a PNG) |
| `DISPLAY_OUTPUT_PATH` | `./frame.png` | Where the `file` driver writes |
| `DISPLAY_STATE_PATH` | `/var/lib/studylife-display/last.json` | Cached last snapshot; `settings.json` and `panel.lock` live in the same directory |
| `DISPLAY_LAYOUT` | `auto` | `auto`, `classic`, `focus`, `exam` or `week`; overridden by the choice made in the web interface |
| `DISPLAY_WEB_BIND` | `0.0.0.0:8795` | Where `serve` listens |
| `DISPLAY_WEB_TOKEN` | – | Access token of the web interface, at least 12 characters; `serve` refuses to start without one |
| `HTTP_TIMEOUT_SECONDS` | `10` | Per request |

`STUDYLIFE_TIMEZONE` matters more than it looks: StudyLife serialises every DateTime as naive
local time of the server, and a freshly imaged Pi runs on UTC. "Today" is the calendar day in
that zone, and a session from 23:30 to 00:30 counts half an hour on each of the two days.

## Install on the Pi

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In the Imager's
   settings set the hostname, the `pi` user and password, Wi-Fi and SSH ("headless"); no
   monitor is ever needed.
2. SSH in and run the installer:

   ```bash
   sudo apt-get install -y git
   git clone https://github.com/lukislp/studylife-display.git
   sudo bash studylife-display/deploy/install.sh
   ```

   It installs the system packages Pillow needs, enables SPI, creates a virtualenv in
   `/opt/studylife-display`, installs this package with the `pi` extra (the Waveshare
   library straight from its git repository plus `spidev`, `gpiozero`, `lgpio`), writes a
   template `/etc/studylife-display.env`, asks for the web interface's access token
   (Enter accepts the suggested random one), and enables the systemd timer and the web
   service. Re-running it updates the code and never overwrites an existing env file.
3. Put the URL and the key into `/etc/studylife-display.env`, then:

   ```bash
   sudo systemctl start studylife-display.service
   journalctl -u studylife-display.service -n 50
   ```

   The first refresh appears within a few seconds. From then on the timer runs `studylife-display run`
   every five minutes, and `http://<hostname>.local:8795/` switches layouts (see
   [Web interface](#web-interface)).

### SD-card protection

A Pi that refreshes a panel for years should not be writing to its SD card at all. After the
installer has run and the first refresh has worked:

```bash
sudo raspi-config nonint enable_overlayfs   # root filesystem read-only, writes go to RAM
sudo sed -i 's/^#\?Storage=.*/Storage=volatile/' /etc/systemd/journald.conf
sudo reboot
```

With the overlay on, `/var/lib/studylife-display/last.json` lives in RAM too, which is fine:
the cache only needs to survive until the next successful fetch, not a reboot. Note that the
layout chosen in the web interface (`settings.json` in the same directory) is then also lost
on reboot and falls back to `DISPLAY_LAYOUT`; set that variable to your usual choice. To change
the configuration later, `sudo raspi-config nonint disable_overlayfs`, reboot, edit, re-enable.

### Refresh cadence, and why full refresh only

The timer fires every five minutes (`OnUnitActiveSec=5min`, first run one minute after boot)
and every refresh is a **full** refresh: `init()`, one complete frame, `sleep()`. Waveshare's
documentation and the panel vendor both warn against continuous partial refreshes - they
accumulate ghosting and, done for months, damage the panel - and recommend a full refresh at
least every few partial ones and never more often than a few times a minute. At one full
refresh per five minutes the panel is well inside its rated lifetime and the ~5 s flicker is a
non-event. Updating only the timer line with a partial refresh between full ones is a
possible follow-up; it is deliberately not in this version.

The dashboard is drawn 800 x 480 with the panel in landscape orientation. If yours is mounted
the other way round, rotate in `driver.py` (`image.rotate(180)`) rather than in the renderer.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `No module named waveshare_epd` | The `pi` extra did not install: `sudo /opt/studylife-display/venv/bin/pip install '/opt/studylife-display/src[pi]'` |
| `FileNotFoundError: /dev/spidev0.0` | SPI is off: `sudo raspi-config nonint do_spi 0` and reboot |
| `Permission denied: /dev/spidev0.0` or GPIO errors | The `pi` user is not in `spi`/`gpio`: `sudo usermod -aG spi,gpio pi`, then log in again |
| Panel stays white, service exits 0 | Driver-board switches (B / 0) and the FPC cable's orientation |
| Header shows `· vor N min` | The last fetch failed; `journalctl -u studylife-display.service` names the reason (403 = a scope is missing on the key) |
| Exit code 1 and `no cached snapshot` | The very first fetch failed and there is nothing to fall back to; `studylife-display check` shows the API error |
| Session times off by an hour or two | `STUDYLIFE_TIMEZONE` must be the server's zone, not the Pi's |
| Web interface does not answer | `journalctl -u studylife-display-web.service -n 20`; `DISPLAY_WEB_TOKEN` missing or shorter than 12 characters makes `serve` exit immediately |
| Refresh from the browser reports "busy" or waits | The timer's refresh holds `panel.lock`; it is over within seconds, a stuck one times out after 60 s |
| `studylife-display check` | Calls the three endpoints and prints what the dashboard would be built from, without touching the panel |

## Development

```bash
uv sync
uv run studylife-display preview --sample --out frame.png   # no instance needed
uv run studylife-display preview --sample --layout exam --out frame.png
uv run studylife-display preview --out frame.png            # against your instance (.env)
DISPLAY_DRIVER=file DISPLAY_STATE_PATH=./state/last.json DISPLAY_WEB_TOKEN=local-dev-token \
  uv run studylife-display serve                            # http://127.0.0.1:8795/
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The `pi` extra is not installed by `uv sync` and is never imported outside
`WaveshareDisplay.__init__`, so everything - including the render tests - runs on a laptop.
`tests/golden/<layout>_<language>.png` are the reference frames; after an intentional layout
change regenerate them with `uv run pytest --update-goldens` and commit the result together
with the previews in `docs/` (`uv run studylife-display preview --sample --layout <key> --out
docs/preview-<key>.png` for each of the four; `docs/preview.png` is the classic one).
Layouts live in `src/studylife_display/layouts/`, one module each, registered in
`layouts/__init__.py`; the drawing helpers they share are in `layouts/common.py`.

`tests/test_wire_fields.py` pins every JSON field name the code reads to the verified
StudyLife wire format. StudyLife never errors on an unknown field, so this test is what
turns a typo into a red build instead of a dashboard that silently shows zeros.

The fonts are IBM Plex Sans Regular and Bold (SIL Open Font License 1.1, see
`src/studylife_display/fonts/OFL.txt`).

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).
