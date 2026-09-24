"""A pure-Python port of what lircd 0.10.2 transmits for a ``lircd.conf``.

Two halves, each a port of the C it names:

* :mod:`.conf` -- ``lib/config_file.c``: parse a ``lircd.conf`` into
  :class:`~.conf.Remote` objects holding exactly the values lircd's send
  path reads, each code tagged with its source line.
* :mod:`.transmit` -- ``lib/transmit.c``, ``lib/ir_remote.c``'s
  ``send_ir_ncode``, ``plugins/file.c``'s ``send_func`` and
  ``tools/irsimsend.cpp``'s ``send_code``: expand a button into the
  microsecond durations lircd emits.

The oracle is the compiled lirc 0.10.2 release; ``tests/vectors/lirc/``
records how to rebuild it, and ``tools/lirc_oracle_compare.py`` compares the
port against it over any set of files.
"""

from .conf import IrCode, LircConfig, Remote, parse, parse_file
from .errors import (
    LircConfigError,
    LircError,
    LircNoTimings,
    LircTerminated,
    LircTransmitError,
    LircUndefinedBehaviour,
    LircUnsupportedProtocol,
)
from .transmit import IrSimSend, Lircd, SendResult, transmit

__all__ = [
    "IrCode",
    "IrSimSend",
    "LircConfig",
    "LircConfigError",
    "LircError",
    "LircNoTimings",
    "LircTerminated",
    "LircTransmitError",
    "LircUndefinedBehaviour",
    "LircUnsupportedProtocol",
    "Lircd",
    "Remote",
    "SendResult",
    "parse",
    "parse_file",
    "transmit",
]
