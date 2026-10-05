#!/usr/bin/env python3
"""Writes the Raspberry Pi Imager repository file (os-list.json) for one image build.

    make-os-list.py --meta dist/image-meta.env --tag v1.4.0 --out dist/os-list.json

The field names follow rpi-imager's os-list schema
(https://github.com/raspberrypi/rpi-imager/blob/main/doc/json-schema/os-list-schema.json):
`extract_size` and `extract_sha256` describe the decompressed image, `image_download_size`
the .img.xz, `init_format: cloudinit-rpi` makes the Imager offer its OS customisation
(Wi-Fi, user, SSH, locale) the way it does for Raspberry Pi OS Trixie. All values come from
the real build artifact (image-meta.env, written by build-image.sh).

Use it with `rpi-imager --repo <url of this file>`, e.g. the "latest release" asset URL.
This is not an official Raspberry Pi Imager listing.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

REPO = "lukislp/studylife-display"
DEVICES = ["pi5-64bit", "pi4-64bit", "pi3-64bit"]


def read_meta(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            meta[key.strip()] = value.strip()
    return meta


def build(meta: dict[str, str], tag: str, release_date: str) -> dict[str, object]:
    name = meta["IMAGE_NAME"]
    return {
        "os_list": [
            {
                "name": f"StudyLife Display {tag}",
                "description": (
                    "E-paper dashboard for the StudyLife study tracker. Raspberry Pi OS Lite "
                    f"(64-bit, {meta['BASE_IMAGE_DATE']} base) with StudyLife Display "
                    "preinstalled for the Waveshare 7.5 inch V2 panel. Set Wi-Fi, user and "
                    "SSH in the OS customisation; the panel shows its setup screen on first boot."
                ),
                "url": f"https://github.com/{REPO}/releases/download/{tag}/{name}.img.xz",
                "extract_size": int(meta["EXTRACT_SIZE"]),
                "extract_sha256": meta["EXTRACT_SHA256"],
                "image_download_size": int(meta["DOWNLOAD_SIZE"]),
                "release_date": release_date,
                "init_format": "cloudinit-rpi",
                "devices": DEVICES,
                "website": f"https://github.com/{REPO}",
            }
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", type=Path, required=True)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    parser.add_argument("--date", default=today)
    args = parser.parse_args()
    document = build(read_meta(args.meta), args.tag, args.date)
    args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
