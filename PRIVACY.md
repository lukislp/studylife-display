# Privacy

**Controller:** the operator of your instance. **Publisher:** see the imprint at <https://lktec.org/impressum>.

StudyAtrium Display runs on a Raspberry Pi in your own network and draws a dashboard on an
e-paper panel. The maintainers of this repository operate no server and receive no data from
it. Statements below are based on the code in `src/studylife_display`.

## What is stored on the Pi

- **API key** for your StudyAtrium account, in the root-owned environment file
  `/etc/studylife-display.env` (`STUDYLIFE_API_KEY`). When you connect through the web
  interface, the key passes through `credentials.pending.json` (mode 0600) in the state
  directory, is moved into that file by a root-only unit and the pending file is deleted.
- **Web access token** (`DISPLAY_WEB_TOKEN`) for the display's own web interface, in the same
  environment file. Web sessions use a key drawn at process start and end on restart.
- **`web-token.txt` on the boot partition**: if you flash the ready-made image and configure no
  `DISPLAY_WEB_TOKEN` (in `setup.env` or the environment file), the first boot generates one and
  also writes it to `studylife-display/web-token.txt` on the boot partition (the FAT partition you
  can read without SSH) so you can find it. The file contains a short explanation and the token in
  **plaintext**; anyone holding the SD card can read it. Note the token down and delete the file.
  Choosing your own token in `setup.env` means no such file is written (an existing one is removed).
- **Settings** you change in the web interface (layout, server, quiet hours and so on) in
  `settings.json` in the state directory, with a second copy on the boot partition
  (`/boot/firmware/studylife-display/settings.json`).
- **Cached snapshot** `last.json` in `/var/lib/studylife-display`: the last API responses (your
  study data, such as sessions, courses, goals, statistics), so the panel can show them with a
  "stale" marker when the server is unreachable. It is replaced on each successful refresh and
  not shown once it is older than `DISPLAY_STALE_ERROR_HOURS` (24 h by default); the file itself
  stays until the next refresh overwrites it or you delete it.
- **Current frame**: `current.png` and `current.json` (the image now on the panel, layout and
  time) in the state directory, and `status.json` (outcome and error text of the last fetch).

The panel itself shows the same data to anyone who can see it.

## Where data goes

- Requests for study data go only to the StudyAtrium server you configured, with your API key.
- **mDNS**: the display advertises its web interface on your local network with Avahi
  (`deploy/avahi-service.sh`, service type `_studylife-display._tcp`, carrying port, TLS flag,
  version and an instance id derived from the machine id). When you start a search on the *Server* page (or through the JSON API) it
  searches the network for `_studylife._tcp` announcements for a few seconds, and sends one
  anonymous `GET /api/instance` to each announced address (at most eight, no credentials, no
  redirects) to check the announcement is genuine. This traffic stays on the LAN, but the probe
  reaches whichever host announces itself there.
- **Optional update check**: only if you turn on `DISPLAY_UPDATE_CHECK` (off by default), the
  web interface asks `api.github.com` for the latest release at most once per six hours.
  This sends no study data, but GitHub sees the Pi's IP address.
- **Auto-update** (`DISPLAY_AUTO_UPDATE`, off by default): if you turn it on, a daily systemd timer
  (03:00, spread over an hour) runs `deploy/update.sh`, and you can run the same script by hand
  (`--check` only looks). The script contacts `api.github.com` (latest release lookup),
  `github.com` (`git fetch` of the release tags from the repository), and the Python package
  index(es) configured for pip (`pip install`; on Raspberry Pi OS typically PyPI and
  piwheels.org), and for the e-paper driver the `github.com/waveshareteam/e-Paper` repository.
  No study data is sent, but those hosts see the Pi's IP address. The update runs unattended and
  installs whatever the release contains.
- No analytics, no telemetry, no crash reporting.
- The web interface and JSON API serve over plain HTTP on your LAN, protected by the access
  token. Put it behind a VPN or TLS proxy if you expose it further.

## Retention and deletion

Everything above stays on the Pi until you remove it: delete `/var/lib/studylife-display`
and the boot-partition copy, or reflash the SD card. The API key stays valid on StudyAtrium
until you revoke or regenerate it there. Deleting your StudyAtrium account revokes it. The
display holds no data on any server, so there is no purge endpoint.

## Contact

Requests about your StudyAtrium data go to the operator of your StudyAtrium instance (the controller).
Security issues: see [SECURITY.md](SECURITY.md). Publisher details: https://lktec.org/impressum.