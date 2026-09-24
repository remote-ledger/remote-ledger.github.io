"""What the lircd port raises, one class per way lircd itself can refuse.

Every class is a :class:`~remote_ledger.errors.LedgerError`, so callers that
already catch the package's base error keep working. The split matters to an
importer: a file lircd *rejects* is not the same finding as a file lircd
accepts but cannot transmit, and neither is the same as a file on which lircd
would do something undefined.
"""

from __future__ import annotations

from ..errors import LedgerError


class LircError(LedgerError):
    """Base for everything the lircd port raises deliberately."""


class LircConfigError(LircError):
    """lircd's parser rejects the file (``read_config`` returns an error).

    ``line`` is lircd's own line counter at the point of failure -- the value
    it prints in "error in configfile line N" -- and ``path`` the file.
    """

    def __init__(self, message: str, *, path: str | None = None,
                 line: int | None = None) -> None:
        where = f"{path or '<string>'}:{line}: " if line is not None else ""
        super().__init__(where + message)
        self.path = path
        self.line = line
        self.reason = message


class LircUndefinedBehaviour(LircError):
    """lircd would crash, or execute C undefined behaviour, on this input.

    Raised rather than guessed: the oracle's output in these cases is an
    accident of one build, not a rule a port can reproduce.
    """


class LircTransmitError(LircError):
    """lircd accepts the button but its send path refuses it.

    ``reason`` is the message lircd logs (``lib/transmit.c``), e.g.
    ``"too short gap"``, ``"buffer too small"``, ``"invalid send buffer"``.
    """

    def __init__(self, reason: str, *, send_index: int | None = None) -> None:
        where = f"send {send_index}: " if send_index is not None else ""
        super().__init__(where + reason)
        self.reason = reason
        self.send_index = send_index


class LircUnsupportedProtocol(LircTransmitError):
    """GRUNDIG, BO or SERIAL: lircd parses these but refuses to send them.

    ``lib/transmit.c:389-393``: "sorry, can't send this protocol yet".
    """


class LircNoTimings(LircTransmitError):
    """A remote with no bit timings: the file driver writes ``code N`` only.

    ``plugins/file.c:219-224``: when ``pzero == szero == 0`` and the remote is
    not ``RAW_CODES``, lircd has no waveform to emit, only the scancode.
    ``code`` holds that scancode exactly as the driver prints it.
    """

    def __init__(self, code: int) -> None:
        super().__init__(
            f"remote has no zero/one timings; lircd's file driver emits only "
            f"the scancode ({code})"
        )
        self.code = code


class LircTerminated(LircTransmitError):
    """A written value has the ``LIRC_EOF`` bit set, so the driver exits.

    ``plugins/file.c:206-209`` raises ``SIGUSR1`` after writing any line whose
    value has bit 27 (``0x08000000``) set -- a duration of 134 s or more, or a
    negative one -- and irsimsend dies on it. Output stops at that line.
    """
