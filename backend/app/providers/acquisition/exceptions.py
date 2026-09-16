class AcquisitionError(RuntimeError):
    """Base for every acquisition-provider failure surfaced to a route."""


class AcquisitionUnavailable(AcquisitionError):
    """The provider isn't reachable, refused the connection, or is otherwise down."""


class AcquisitionRateLimited(AcquisitionError):
    """The provider's own rate limit / cooldown is active."""

    def __init__(self, wait_seconds: float) -> None:
        super().__init__(f"rate-limited; try again in {wait_seconds:.0f}s")
        self.wait_seconds = wait_seconds
