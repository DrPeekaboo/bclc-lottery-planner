"""Optional hooks that let a caller (the web app) watch progress and stop long calculations."""


class Cancelled(Exception):
    """Raised when the caller asked to stop. No partial result is returned."""


def check_stop(should_stop) -> None:
    if should_stop is not None and should_stop():
        raise Cancelled
