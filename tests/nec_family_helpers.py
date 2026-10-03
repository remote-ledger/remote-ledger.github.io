"""Shared by the NEC2 and NECx1 invariant tests: read a frame back out of its
durations without going through the encoder under test."""

UNIT_US = 564
EXTENT_US = 108_000


def read_frame(durations, lead_mark_units, lead_space_units, unit=UNIT_US):
    """The four bytes of a 32-bit NEC-family frame, LSB first, from its
    durations: lead-in, 32 (mark, space) pairs, stop mark. Asserts every
    duration it reads is exactly the unit multiple the IRP names."""
    assert durations[0] == lead_mark_units * unit
    assert durations[1] == lead_space_units * unit
    bits = []
    for i in range(2, 66, 2):
        assert durations[i] == unit, f"bit mark {i}"
        space = durations[i + 1]
        assert space in (unit, 3 * unit), f"bit space {i + 1} is {space}"
        bits.append(1 if space == 3 * unit else 0)
    assert durations[66] == unit, "stop mark"
    return [
        sum(bit << k for k, bit in enumerate(bits[start : start + 8]))
        for start in (0, 8, 16, 24)
    ]
