"""StudyLife e-paper dashboard for the Waveshare 7.5 inch HAT V2."""

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    """The installed version (from the git tag the checkout sits on, see pyproject.toml);
    "0.0.0" when the package metadata is missing, e.g. when run straight from a source
    tree that was never installed."""
    try:
        return version("studylife-display")
    except PackageNotFoundError:
        return "0.0.0"
