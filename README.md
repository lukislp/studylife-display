# StudyLife Display

[![CI](https://github.com/lukislp/studylife-display/actions/workflows/ci.yml/badge.svg)](https://github.com/lukislp/studylife-display/actions/workflows/ci.yml) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lukislp/studylife-display/badge)](https://scorecard.dev/viewer/?uri=github.com/lukislp/studylife-display) [![CodeQL](https://github.com/lukislp/studylife-display/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/lukislp/studylife-display/security/code-scanning)
[![Release](https://img.shields.io/github/v/release/lukislp/studylife-display)](https://github.com/lukislp/studylife-display/releases)
[![License: AGPL-3.0](https://img.shields.io/github/license/lukislp/studylife-display)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB)](https://www.python.org/)

A study dashboard for [StudyLife](https://github.com/lukislp/studylife) on a 7.5" e-paper
panel: a Raspberry Pi on the desk that shows, without a screen to unlock or a tab to find,
how today is going. It reads three read-only endpoints every five minutes and redraws the
panel; between refreshes the Pi and the panel sleep.

## What it shows

![Preview of the rendered dashboard](docs/preview.png)

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
| `DISPLAY_STATE_PATH` | `/var/lib/studylife-display/last.json` | Cached last snapshot |
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
   template `/etc/studylife-display.env` and enables the systemd timer.
3. Put the URL and the key into `/etc/studylife-display.env`, then:

   ```bash
   sudo systemctl start studylife-display.service
   journalctl -u studylife-display.service -n 50
   ```

   The first refresh appears within a few seconds. From then on the timer runs `studylife-display run`
   every five minutes.

### SD-card protection

A Pi that refreshes a panel for years should not be writing to its SD card at all. After the
installer has run and the first refresh has worked:

```bash
sudo raspi-config nonint enable_overlayfs   # root filesystem read-only, writes go to RAM
sudo sed -i 's/^#\?Storage=.*/Storage=volatile/' /etc/systemd/journald.conf
sudo reboot
```

With the overlay on, `/var/lib/studylife-display/last.json` lives in RAM too, which is fine:
the cache only needs to survive until the next successful fetch, not a reboot. To change the
configuration later, `sudo raspi-config nonint disable_overlayfs`, reboot, edit, re-enable.

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
| `studylife-display check` | Calls the three endpoints and prints what the dashboard would be built from, without touching the panel |

## Development

```bash
uv sync
uv run studylife-display preview --sample --out frame.png   # no instance needed
uv run studylife-display preview --out frame.png            # against your instance (.env)
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The `pi` extra is not installed by `uv sync` and is never imported outside
`WaveshareDisplay.__init__`, so everything - including the render tests - runs on a laptop.
`tests/golden/*.png` are the reference frames; after an intentional layout change regenerate
them with `uv run pytest --update-goldens` and commit the result together with
`docs/preview.png` (`uv run studylife-display preview --sample --out docs/preview.png`).

`tests/test_wire_fields.py` pins every JSON field name the code reads to the verified
StudyLife wire format. StudyLife never errors on an unknown field, so this test is what
turns a typo into a red build instead of a dashboard that silently shows zeros.

The fonts are IBM Plex Sans Regular and Bold (SIL Open Font License 1.1, see
`src/studylife_display/fonts/OFL.txt`).

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).
