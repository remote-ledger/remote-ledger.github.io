"""A decoder for the Sharp/Denon frame, written from the IRP and not from the encoder.

``D:5, F:8`` and two trailer bits, each bit a one-unit mark and a space of
three units (a zero) or seven (a one), then a stop mark and a gap: 32
durations. ``decode_frame`` returns ``(device, function, trailer)`` with the
trailer as the two-bit value ``N:2`` of the IRP (LSB first), or ``None`` for
anything that is not exactly such a frame.
"""

UNIT = 264
FRAME = 2 * 15 + 2


def decode_frame(frame, unit=UNIT):
    if len(frame) != FRAME or frame[-2] != unit:
        return None
    bits = []
    for i in range(15):
        mark, space = frame[2 * i], frame[2 * i + 1]
        if mark != unit or space not in (3 * unit, 7 * unit):
            return None
        bits.append(1 if space == 7 * unit else 0)
    value = lambda b: sum(x << i for i, x in enumerate(b))
    return value(bits[:5]), value(bits[5:13]), value(bits[13:])


def decode_signal(signal, unit=UNIT):
    """``[intro frame] + [repeat frames]`` as decoded tuples."""
    out = []
    for sequence in (signal.intro, signal.repeat):
        assert len(sequence) % FRAME == 0
        out.append([decode_frame(sequence[i:i + FRAME], unit)
                    for i in range(0, len(sequence), FRAME)])
    return out
