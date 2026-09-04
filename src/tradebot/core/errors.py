"""Core error taxonomy used to fail closed with actionable context."""


class TradebotError(Exception):
    """Base class for expected platform failures."""


class InvalidTimestampError(TradebotError, ValueError):
    """A timestamp is naïve or is not expressed in UTC."""


class InvalidEventError(TradebotError, ValueError):
    """An immutable event violates its domain invariants."""


class LookAheadError(TradebotError):
    """An event is not yet available to the current clock."""


class BusHaltedError(TradebotError):
    """Publication was attempted after dispatch failed closed."""


class EventDispatchError(TradebotError):
    """A subscriber failed and caused the bus to halt."""


class ClockDiscontinuityError(TradebotError):
    """Wall time diverged materially from monotonic elapsed time."""


class ClockMovedBackwardError(ClockDiscontinuityError):
    """A clock source attempted to regress."""


class AmbiguousLocalTimeError(InvalidTimestampError):
    """A local time maps to two UTC instants and needs an explicit fold."""


class NonexistentLocalTimeError(InvalidTimestampError):
    """A local time falls inside a daylight-saving transition gap."""


class ConfigurationError(TradebotError, ValueError):
    """A configuration file cannot be parsed or validated safely."""
