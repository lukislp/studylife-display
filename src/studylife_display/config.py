from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from studylife_display.auto_rules import parse_rule_window
from studylife_display.daily_clear import parse_clear_at
from studylife_display.quiet_hours import parse_quiet_hours

ROTATIONS = (0, 180)
LANGUAGES = ("de", "en")
LAYOUT_CHOICES = ("auto", "classic", "focus", "exam", "week", "semester", "agenda", "review")

Language = Literal["de", "en"]
LayoutChoice = Literal["auto", "classic", "focus", "exam", "week", "semester", "agenda", "review"]


# The validators are plain functions so that the environment settings below and the
# overrides written by the web interface (WebOverrides) apply exactly the same rules; a
# value the settings page accepts is one the environment would have accepted too.


def check_rotation(value: int) -> int:
    if value not in ROTATIONS:
        raise ValueError(f"DISPLAY_ROTATE must be one of {ROTATIONS}, not {value}")
    return value


def check_quiet_hours(value: str) -> str:
    parse_quiet_hours(value)  # raises ValueError with the reason
    return value.strip()


def check_clear_at(value: str) -> str:
    parse_clear_at(value)  # raises ValueError with the reason
    return value.strip()


def check_auto_window(value: str) -> str:
    parse_rule_window(value)  # raises ValueError with the reason
    return value.strip()


def check_public_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and not value.lower().startswith("https://"):
        raise ValueError("DISPLAY_PUBLIC_BASE_URL must be an https:// URL (or empty)")
    return value


def check_setup_url(value: str) -> str:
    value = value.strip()
    if value and not value.lower().startswith(("http://", "https://")):
        raise ValueError("DISPLAY_SETUP_URL must be an http:// or https:// URL (or empty)")
    return value


def parse_bind(bind: str) -> tuple[str, int]:
    """ "0.0.0.0:8795" -> ("0.0.0.0", 8795); a bare port binds every interface."""
    host, sep, port = bind.rpartition(":")
    if not sep:
        return "0.0.0.0", int(bind)
    return host.strip("[]") or "0.0.0.0", int(port)


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables / .env.

    On the Pi the systemd unit passes /etc/studylife-display.env as EnvironmentFile; during
    development a .env in the working directory does the same job. The values the web
    interface may override (language, rotation, quiet hours, clear time, update check,
    layout) are read through `effective_settings` in `settings_store`, which layers
    `settings.json` on top of these.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # This single person's StudyLife instance and a READ-ONLY API key (Metrics.GetSummary,
    # Sessions.GetAll, Sessions.GetHistory, TimerState.Get; see studylife_client.SCOPES).
    # The display never writes anything back, so a key with more scopes than that is a
    # liability sitting on an SD card, not a convenience.
    # The key may be empty until the account is connected from the web interface, which
    # writes it into the environment file through `credentials-apply`.
    studylife_base_url: AnyHttpUrl
    studylife_api_key: str = ""

    # Every DateTime StudyLife sends is naive local time of the SERVER (no offset in the JSON).
    # Timestamps are interpreted in this zone explicitly, never with the Pi's own clock
    # setting - a freshly imaged Pi runs on UTC and would otherwise shift every session by an
    # hour or two and put late-evening sessions on the wrong calendar day.
    studylife_timezone: str = "Europe/Berlin"

    # Language of the rendered text. The default is German because that is the language of
    # the StudyLife instance this was built for; "en" swaps every label.
    display_language: Language = "de"

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

    # Which layout to draw (see studylife_display.layouts). "auto" picks per refresh, in this
    # order: the weekly review inside its window, the exam countdown when one is due within a
    # week, the timer while it runs, the agenda while a session planned for today still lies
    # ahead (inside its window), classic otherwise ("semester" is never picked
    # automatically). A settings.json written by the web interface next to the cache
    # overrides this value.
    display_layout: LayoutChoice = "auto"

    # The two windows of the auto rules: `[weekdays] HH-HH` or `HH:MM-HH:MM` (end may be
    # 24 for midnight, no wrap past midnight; see auto_rules.py). Empty switches the rule
    # off. The review rule comes first of all, the agenda rule last before classic.
    display_auto_review: str = "sun 18-24"
    display_auto_agenda: str = "06-12"

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

    # Optional: an https URL under which this web interface is reachable (a Tailscale name,
    # say). When set, the connect page lets StudyLife redirect straight back to
    # `<url>/connect/callback`; without it the browser lands on a localhost URL the person
    # pastes into the page instead. StudyLife refuses a plain-http LAN address as a redirect
    # URI (RFC 8252 allows only https or a loopback), hence the two modes.
    display_public_base_url: str = ""

    # Optional: the exact URL the first-run setup screen shows and encodes in its QR code.
    # Empty means "derive it": `<DISPLAY_PUBLIC_BASE_URL>/connect` when that is set, else
    # `http://<hostname>.local:<web port>/connect`. Set it when the Pi is reached under a
    # name mDNS does not give it (a DHCP reservation, a reverse proxy).
    display_setup_url: str = ""

    http_timeout_seconds: float = 10.0

    @field_validator("display_rotate")
    @classmethod
    def _rotation(cls, value: int) -> int:
        return check_rotation(value)

    @field_validator("display_stale_error_hours")
    @classmethod
    def _stale_hours(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("DISPLAY_STALE_ERROR_HOURS must be positive")
        return value

    @field_validator("display_quiet_hours")
    @classmethod
    def _quiet_hours(cls, value: str) -> str:
        return check_quiet_hours(value)

    @field_validator("display_clear_at")
    @classmethod
    def _clear_at(cls, value: str) -> str:
        return check_clear_at(value)

    @field_validator("display_auto_review", "display_auto_agenda")
    @classmethod
    def _auto_window(cls, value: str) -> str:
        return check_auto_window(value)

    @field_validator("display_public_base_url")
    @classmethod
    def _public_base_url(cls, value: str) -> str:
        return check_public_base_url(value)

    @field_validator("display_setup_url")
    @classmethod
    def _setup_url(cls, value: str) -> str:
        return check_setup_url(value)


class WebOverrides(BaseModel):
    """What the web interface may persist in `settings.json`: every field optional, an
    absent one meaning "use the environment". The keys are the JSON names in the file;
    each maps onto the Settings field of the same meaning (see OVERRIDE_FIELDS)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    layout: LayoutChoice | None = None
    language: Language | None = None
    rotate: int | None = None
    quiet_hours: str | None = None
    clear_at: str | None = None
    update_check: bool | None = None
    auto_review: str | None = None
    auto_agenda: str | None = None

    @field_validator("rotate")
    @classmethod
    def _rotation(cls, value: int | None) -> int | None:
        return None if value is None else check_rotation(value)

    @field_validator("quiet_hours")
    @classmethod
    def _quiet_hours(cls, value: str | None) -> str | None:
        return None if value is None else check_quiet_hours(value)

    @field_validator("clear_at")
    @classmethod
    def _clear_at(cls, value: str | None) -> str | None:
        return None if value is None else check_clear_at(value)

    @field_validator("auto_review", "auto_agenda")
    @classmethod
    def _auto_window(cls, value: str | None) -> str | None:
        return None if value is None else check_auto_window(value)

    def as_json(self) -> dict[str, Any]:
        """Only the fields that are set, in a stable order."""
        return {key: value for key, value in self.model_dump().items() if value is not None}


# settings.json key -> Settings field it overrides.
OVERRIDE_FIELDS: dict[str, str] = {
    "layout": "display_layout",
    "language": "display_language",
    "rotate": "display_rotate",
    "quiet_hours": "display_quiet_hours",
    "clear_at": "display_clear_at",
    "update_check": "display_update_check",
    "auto_review": "display_auto_review",
    "auto_agenda": "display_auto_agenda",
}
