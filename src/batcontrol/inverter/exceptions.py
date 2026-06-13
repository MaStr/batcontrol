"""
Custom exceptions for the inverter module.

These let batcontrol distinguish three situations:

1. Configuration errors         -> fail immediately on the first run.
2. Transient communication loss -> skip the current control cycle and retry
   on the next scheduled run (InverterCommunicationError).
3. Permanent outage             -> terminate after the tolerance window
   (InverterOutageError).
"""


class InverterError(Exception):
    """Base class for inverter communication problems."""


class InverterCommunicationError(InverterError):
    """
    Raised when an inverter call fails after initialization.

    Signals the caller to abort the current control cycle. batcontrol retries
    on the next scheduled run, so no decision is ever made on stale data.
    """


class InverterOutageError(InverterError):
    """
    Raised when the inverter stays unreachable beyond the tolerance window.

    Terminates batcontrol - the inverter is considered permanently down.

    Attributes:
        message: Explanation of the error
        outage_duration_seconds: How long the inverter has been unreachable
    """

    def __init__(self, message: str, outage_duration_seconds: float = 0):
        super().__init__(message)
        self.message = message
        self.outage_duration_seconds = outage_duration_seconds

    def __str__(self):
        minutes = self.outage_duration_seconds / 60
        return f"{self.message} (outage duration: {minutes:.1f} minutes)"
