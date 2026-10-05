# SD-card image

Maintainer notes for the ready-made image (the user-facing steps are in the top-level
[README](../README.md#flash-the-ready-made-image)).

## What the build does

`build-image.sh` (run as root; CI uses a native arm64 runner, so no emulation; on an x86-64
host it falls back to `qemu-user-static`):

1. Downloads the stock Raspberry Pi OS Lite arm64 image named in `base-image.env` and checks
   its SHA-256 against the pin in that file (a mismatch aborts the build).
2. Grows the root partition by 1 GiB (venv and build leftovers need room; the stock image
   expands to the card on first boot regardless), loop-mounts boot at `/boot/firmware` and
   root, prints `cmdline.txt`, `fstab`, `issue.txt` and the stock machine-id/SSH-key state for
   the record.
3. Chroots in (DNS from the host, `policy-rc.d` so no package starts a daemon) and runs
   `deploy/install.sh --image --panel <key> (--tag vX.Y.Z | --local)`. The installer's image
   mode is documented in its header: no `systemctl start`, no `raspi-config` (SPI is set in
   `config.txt`), no TLS key, token or mDNS file, the services run as the system account
   `studylife-display` through drop-ins, units are enabled by symlink.
4. Verifies inside the chroot: the installed version equals the tag, the package imports, the
   sample dashboard and the setup screen render through `DISPLAY_DRIVER=file`, seven units
   report `enabled` under `systemctl --root`, SPI is on, `update.sh` and the git remote are
   there. Saves `pip freeze` and the apt package list as release assets.
5. Cleans (apt lists/caches, logs, journal, caches, history, machine-id emptied, SSH host
   keys removed, build plumbing undone), then runs `scan-rootfs.sh` and fails on private
   keys, a machine id, a credentials/state file, a token in the env file, the TLS pair or the
   mDNS file, SSH host keys, shell history, saved Wi-Fi connections, and an uncommented value
   in `setup.env`.
6. `fstrim`s, unmounts, `e2fsck`s, checksums the raw image (for the Imager's
   `extract_sha256`), compresses with `xz -6` and writes the `.sha256`.

`make-os-list.py` writes the Raspberry Pi Imager repository file (`os-list.json`) from the
real artifact's sizes and hashes. `init_format` is `cloudinit-rpi`, the value the official
Raspberry Pi OS Trixie entry uses, which is what makes the Imager offer its OS customisation.

## First boot

`firstboot/studylife-display-firstboot.service` (root, hardened like the other root units)
runs `firstboot/firstboot.sh` on every boot, ordered after cloud-init's stages (so the
hostname from the Imager is set) and before the restore unit, the web service and the timer.
Every step is idempotent: certificate for `<hostname>.local` if missing, the import of
`/boot/firmware/studylife-display/setup.env` (whitelisted keys, validated values, applied
lines commented out afterwards), a random web token if the env file has none (never logged;
written to `web-token.txt` on the boot partition), and the mDNS service file once
`/etc/machine-id` is initialised. `tests/installer-smoke.sh` exercises all of it.

Raspberry Pi OS Trixie configures first boot with cloud-init (the Imager writes `user-data`
and `network-config` to the boot partition); the Bookworm-style `firstrun.sh` is not used.
Neither is touched by this image.

## Tests

- `tests/installer-smoke.sh`: image-mode install, first boot, scanner (including that it
  fails on a booted system), panel/extra mapping, inky guard. Runs in an arm64 Debian
  container with the Raspberry Pi apt archive added (CI job `smoke`; locally
  `docker run --platform linux/arm64 -v "$PWD:/repo:ro" debian:trixie bash
  /repo/image/tests/installer-smoke.sh`, with `-e APT_FORCE_HTTPS=1` where port 80 is
  blocked).
- The build itself is the end-to-end test of the chroot half.

## Bumping the base image

Pick the newest dated directory under
<https://downloads.raspberrypi.com/raspios_lite_arm64/images/>, copy the SHA-256 from the
`.img.xz.sha256` beside it into `base-image.env` together with the URL and date, and open a
PR: the build logs `cmdline.txt`, `fstab` and the stock identity files, which is where a
changed first-boot mechanism would show.

## Security notes

- **Downloaded at build time:** the base image (hash-pinned), apt packages from the image's
  own sources (Debian and the Raspberry Pi archive, signature-checked by apt), Python
  packages from PyPI/piwheels and the Waveshare driver from its git repository (as in every
  manual install; `pyproject.toml` pins it to the repository, `uv.lock` to a revision for
  development, but `pip install` of the `pi` extra takes the repository's current head).
- **Trust anchor for the base image** is the pinned SHA-256, taken from the official download
  site at the time of the bump; the `.sig` next to the image is not checked by CI.
- **Egress** of the build job is restricted to the hosts it needs by `harden-runner`; all
  third-party actions are pinned by SHA.
- **Provenance:** release images carry a build-provenance attestation
  (`actions/attest-build-provenance`), and the release has the Python and apt package lists.
  There is no full SBOM.
- **In the image:** public code at a release tag, the venv, a service account without a
  login shell, no secrets. Everything per device is created on the device.
