# Privacy

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

- Requests go only to the StudyAtrium server you configured, with your API key. The display
  discovers servers on your local network (mDNS) and can advertise its web interface there
  with Avahi (deploy/avahi-service.sh); that stays on the LAN.
- **Optional update check**: only if you turn on `DISPLAY_UPDATE_CHECK` (off by default), the
  web interface asks `api.github.com` for the latest release at most once per six hours.
  This sends no study data, but GitHub sees the Pi's IP address.
- No analytics, no telemetry, no crash reporting.
- The web interface and JSON API serve over plain HTTP on your LAN, protected by the access
  token. Put it behind a VPN or TLS proxy if you expose it further.

## Retention and deletion

Everything above stays on the Pi until you remove it: delete `/var/lib/studylife-display`
and the boot-partition copy, or reflash the SD card. The API key stays valid on StudyAtrium
until you revoke or regenerate it there. Deleting your StudyAtrium account revokes it. The
display holds no data on any server, so there is no purge endpoint.

## Contact

Requests about your StudyAtrium data go to the operator of your StudyAtrium instance. Security
issues: see [SECURITY.md](SECURITY.md).