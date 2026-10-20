"""Transport contract. This package never opens a serial port or network socket."""
from typing import Protocol


class Transport(Protocol):
    def exchange(self, request: bytes, timeout: float) -> bytes:
        """Return one bounded ACK or raise; implement a real bounded timeout.

        Never retry automatically: an exception can mean input was delivered
        but its acknowledgment was lost.
        """
        ...

    def close(self) -> None:
        ...
