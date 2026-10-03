"""Frame-building helpers shared by the Pioneer, JVC, Sharp and Denon encoders.

All four are pulse-distance protocols whose IRP writes every field
least-significant bit first and closes a frame with a one-unit stop mark and
an ``^extent`` that pads the frame to a fixed period. These helpers are only
that common arithmetic -- the frame *layouts* stay in each protocol's own
module, next to the IRP they implement.

IRP's ``^E`` extent is measured from the start of the frame it closes: in
IrpTransmogrifier's rendering of ``(A,^67m,(B,^67m,C,^67m)*)`` every one of
the three frames pads to its own 67 ms, so a repeat holding two frames is
134 ms long. That is why :func:`pad_to_extent` takes one frame, not a
sequence.
"""

from __future__ import annotations

from ..errors import EncodeError


def lsb_bits(value: int, count: int) -> list[int]:
    """``D:8`` and friends: the low ``count`` bits, least significant first."""
    return [(value >> i) & 1 for i in range(count)]


def pulse_distance(bits: list[int], unit: int, zero_units: int, one_units: int) -> list[int]:
    """``<1,-zero|1,-one>``: a one-unit mark, then a space chosen by the bit."""
    out: list[int] = []
    for bit in bits:
        out += [unit, (one_units if bit else zero_units) * unit]
    return out


def pad_to_extent(frame: list[int], extent_us: int, unit_us: int,
                  name: str, what: str) -> list[int]:
    """Append the space that makes ``frame`` last exactly ``extent_us`` (D31).

    ``frame`` ends on its stop mark. A gap shorter than one unit would mean
    the marks and spaces already fill the extent, and clamping would
    fabricate a waveform that fails the period it claims.
    """
    head = sum(frame)
    gap = extent_us - head
    if gap < unit_us:
        raise EncodeError(
            f"{name} {what}: marks and spaces already total {head} us against "
            f"an extent of {extent_us} us, leaving a gap of {gap} us which is "
            f"below one unit ({unit_us} us). Clamping would fabricate a "
            "waveform that fails the extent it claims (D31)"
        )
    return [*frame, gap]


def check_byte(label: str, value: int, name: str, field: str, bits: int = 8) -> None:
    """Refuse a field outside ``0 .. 2**bits - 1``, naming the IRP field."""
    if not 0 <= value < (1 << bits):
        raise EncodeError(
            f"{name} {label} is {value}; {field}:{bits} holds 0-{(1 << bits) - 1}"
        )
