from typing import Literal

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from studylife_display.daily_clear import parse_clear_at
from studylife_display.quiet_hours import parse_quiet_hours

ROTATIONS = (0, 180)


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables / .env.

    On the Pi the systemd unit passes /etc/studylife-display.env as EnvironmentFile; during
    development a .env in the working directory does the same job.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # This single person's StudyLife instance and a READ-ONLY API key (Metrics.GetSummary,
    # Sessions.GetHistory, TimerState.Get). The display never writes anything back, so a key
    # with more scopes than that is a liability sitting on an SD card, not a convenience.
    studylife_base_url: AnyHttpUrl
    studylife_api_key: str

    # Every DateTime StudyLife sends is naive local time of the SERVER (no offset in the JSON).
    # Timestamps are interpreted in this zone explicitly, never with the Pi's own clock
    # setting - a freshly imaged Pi runs on UTC and would otherwise shift every session by an
    # hour or two and put late-evening sessions on the wrong calendar day.
    studylife_timezone: str = "Europe/Berlin"

    # Language of the rendered text. The default is German because that is the language of
    # the StudyLife instance this was built for; "en" swaps every label.
    display_language: Literal["de", "en"] = "de"

    # "waveshare" drives the real panel over SPI, "file" writes the frame as a PNG (used by
    # `preview`, by the tests and by anyone developing without the hardware attached).
    display_driver: Literal["waveshare", "file"] = "waveshare"
    display_output_path: str = "./frame.png"

    # 0 or 180: the panel mounted the other way round. Applied by the driver right before
    # the frame is shown (both drivers), never by the layouts, so a rotated panel draws the
    # same pixels as an upright one and the golden frames stay valid.
    display_rotate: int = 0

    # The last successfully fetched payloads live here so that a network or API failure
    # re-renders the previous state with a "stale" marker instead of leaving the panel blank
    # or, worse, showing a Python traceback nobody can read from across the room.
    display_state_path: str = "/var/lib/studylife-display/last.json"

    # How old the cached snapshot may get before the panel stops showing it with the stale
    # marker and shows the "data is stale" screen instead. A day of failed fetches is no
    # longer a blip; a dashboard that quietly shows yesterday's numbers is worse than one
    # that says so.
    display_stale_error_hours: float = 24.0

    # Quiet hours, `HH-HH` or `HH:MM-HH:MM`, wrapping past midnight allowed (`23-7`). Inside
    # the window the scheduled `run` does nothing (no fetch, no flicker in a dark room);
    # the web interface's buttons still work, they are an explicit request. Empty = off.
    display_quiet_hours: str = ""

    # Time of the one full clear per day against ghosting, `HH:MM` (or `HH`); the first
    # scheduled `run` at or after it clears the panel to white before drawing the frame.
    # It runs inside quiet hours too, being the one refresh that matters. Empty = off.
    display_clear_at: str = "04:00"

    # Whether the web interface may ask GitHub (once per six hours, cached in the state
    # directory) whether a newer release exists. Off by default: nothing on the Pi talks to
    # anything but the StudyLife instance unless the person installing opts in.
    display_update_check: bool = False

    # Which layout to draw (see studylife_display.layouts). "auto" picks per refresh:
    # the exam countdown when one is due within a week, the timer while it runs, classic
    # otherwise. A settings.json written by the web interface next to the cache
    # overrides this value.
    display_layout: Literal["auto", "classic", "focus", "exam", "week"] = "auto"

    # A second copy of settings.json on the boot partition, which stays writable by root
    # even when Raspberry Pi OS's overlay filesystem turns the rest of the SD card (the state
    # directory included) into RAM. `persist-export` writes it, `persist-import` restores it
    # at boot; both are run by root-only systemd units, the web service never touches it.
    # Empty disables the mirror (the choice then lives only in the state directory).
    display_persist_path: str = "/boot/firmware/studylife-display/settings.json"

    # The web interface (`studylife-display serve`): where it listens and the access
    # token the person installing chooses. `serve` refuses to start with an empty or
    # short token, so an unconfigured install never exposes the panel controls to the
    # LAN by accident.
    display_web_bind: str = "0.0.0.0:8795"
    display_web_token: str = ""

    http_timeout_seconds: float = 10.0

    @field_validator("display_rotate")
    @classmethod
    def _rotation(cls, value: int) -> int:
        if value not in ROTATIONS:
            raise ValueError(f"DISPLAY_ROTATE must be one of {ROTATIONS}, not {value}")
        return value

    @field_validator("display_stale_error_hours")
    @classmethod
    def _stale_hours(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("DISPLAY_STALE_ERROR_HOURS must be positive")
        return value

    @field_validator("display_quiet_hours")
    @classmethod
    def _quiet_hours(cls, value: str) -> str:
        parse_quiet_hours(value)  # raises ValueError with the reason
        return value.strip()

    @field_validator("display_clear_at")
    @classmethod
    def _clear_at(cls, value: str) -> str:
        parse_clear_at(value)  # raises ValueError with the reason
        return value.strip()
