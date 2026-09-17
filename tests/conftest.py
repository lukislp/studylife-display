from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from studylife_display.sample import sample_payloads

BERLIN = ZoneInfo("Europe/Berlin")

# A Thursday afternoon; every golden and every "today" assertion is anchored here.
FIXED_NOW = datetime(2026, 9, 17, 16, 45, tzinfo=BERLIN)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="rewrite tests/golden/*.png from the current renderer instead of comparing",
    )


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-goldens"))


@pytest.fixture
def tz() -> ZoneInfo:
    return BERLIN


@pytest.fixture
def fixed_now() -> datetime:
    return FIXED_NOW


@pytest.fixture
def sample(
    fixed_now: datetime, tz: ZoneInfo
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    return sample_payloads(fixed_now, tz)
