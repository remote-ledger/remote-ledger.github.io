"""Decode a Broadlink IR packet to alternating mark/space microseconds.

A Broadlink RM's "learned" IR command, once base64-decoded, is a small
binary packet: a one-byte type, a one-byte repeat count (unused for
playback -- SmartIR's own integration ignores it too), a little-endian
16-bit payload length, then the payload itself -- a stream of durations.
Each duration is one byte, in units of ~32.84 us (the RM's sampling
clock), unless that byte is ``0x00``, in which case the *next two* bytes
(big-endian) hold the duration in the same unit -- the escape lircd's own
``SPACE_ENC`` decoding has no equivalent of, since Broadlink's format has
no notion of protocol at all, only a captured waveform.

This is not a port of anything lircd does (see ``lirc/transmit.py``'s
docstring for why that machinery doesn't transfer): a Broadlink capture is
already just mark/space durations, so decoding it is arithmetic, not
protocol emulation. There is no compiled oracle to check this against the
way ``tools/lirc_oracle_compare.py`` checks the lircd port; instead this
tick constant and the truncating (not rounding) conversion below are
matched, byte for byte, against ``data_to_pulses`` in the community
``broadlink`` PyPI package (MIT-licensed, `pypi.org/project/broadlink
<https://pypi.org/project/broadlink/>`_) -- the closest thing this format
has to a reference decoder -- and ``tests/test_smartir_broadlink.py``
derives its vectors by hand from the format above, worked byte by byte.
"""

from __future__ import annotations

import base64
import binascii

from ..errors import ValidationError

#: The RM's sampling clock, ~32.84 us/tick -- the same constant and the
#: same truncating conversion the ``broadlink`` package's
#: ``data_to_pulses`` uses, so a duration this module produces matches
#: that reference decoder exactly, not just approximately.
UNIT_US = 32.84

#: Broadlink's IR packet type (as opposed to 0x00, RF433).
IR_TYPE = 0x26


class BroadlinkDecodeError(ValidationError):
    """A base64 command does not hold a well-formed Broadlink IR packet."""


def decode_packet(text: str) -> list[int]:
    """The packet's durations in microseconds, mark first, alternating.

    Raises :class:`BroadlinkDecodeError` if ``text`` is not valid base64,
    is not an IR packet (type byte, D42), declares a payload longer than
    what follows, or ends mid-duration (a truncated escape sequence).
    """
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BroadlinkDecodeError(f"not valid base64: {exc}") from exc
    if len(data) < 4:
        raise BroadlinkDecodeError(f"packet is {len(data)} bytes, too short for a header")
    if data[0] != IR_TYPE:
        raise BroadlinkDecodeError(f"packet type 0x{data[0]:02x} is not IR (0x{IR_TYPE:02x})")
    length = int.from_bytes(data[2:4], "little")
    payload = data[4:4 + length]
    if len(payload) < length:
        raise BroadlinkDecodeError(
            f"header declares a {length}-byte payload, but only {len(payload)} follow"
        )

    durations: list[int] = []
    i = 0
    while i < length:
        b = payload[i]
        if b == 0:
            if i + 3 > length:
                raise BroadlinkDecodeError(
                    f"escape byte at offset {i} has no 2-byte value after it"
                )
            units = int.from_bytes(payload[i + 1:i + 3], "big")
            i += 3
        else:
            units = b
            i += 1
        if units == 0:
            raise BroadlinkDecodeError(f"a zero-length duration at offset {i}")
        durations.append(int(units * UNIT_US))
    return durations
