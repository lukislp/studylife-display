from typing import Literal

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # The last successfully fetched payloads live here so that a network or API failure
    # re-renders the previous state with a "stale" marker instead of leaving the panel blank
    # or, worse, showing a Python traceback nobody can read from across the room.
    display_state_path: str = "/var/lib/studylife-display/last.json"

    # Which layout to draw (see studylife_display.layouts). "auto" picks per refresh:
    # the exam countdown when one is due within a week, the timer while it runs, classic
    # otherwise. A settings.json written by the web interface next to the cache
    # overrides this value.
    display_layout: Literal["auto", "classic", "focus", "exam", "week"] = "auto"

    # The web interface (`studylife-display serve`): where it listens and the access
    # token the person installing chooses. `serve` refuses to start with an empty or
    # short token, so an unconfigured install never exposes the panel controls to the
    # LAN by accident.
    display_web_bind: str = "0.0.0.0:8795"
    display_web_token: str = ""

    http_timeout_seconds: float = 10.0
