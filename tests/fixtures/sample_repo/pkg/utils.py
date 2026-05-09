"""Pure helper functions used across services."""

DEFAULT_RETRIES = 3


def add(a: int, b: int) -> int:
    """Add two ints."""
    return a + b


def retry(fn, attempts: int = DEFAULT_RETRIES):
    """Run `fn` up to `attempts` times, returning the first success."""
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
    raise last
