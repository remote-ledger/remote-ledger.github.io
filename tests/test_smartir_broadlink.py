"""``remote_ledger.smartir.broadlink``: decode against hand-derived vectors.

Cross-checked separately (``tools/smartir_oracle_compare.py``) against the
community ``broadlink`` PyPI package's ``data_to_pulses`` over a live
SmartIR checkout: 966 commands compared, 0 mismatches. These tests don't
depend on that package being installed; they work the bytes out by hand.
"""

from __future__ import annotations

import base64

import pytest

from remote_ledger.smartir.broadlink import UNIT_US, BroadlinkDecodeError, decode_packet


def _packet(*body: int) -> str:
    """base64 of a well-formed IR packet wrapping ``body``."""
    data = bytes([0x26, 0x00, len(body) & 0xFF, len(body) >> 8, *body])
    return base64.b64encode(data).decode()


def test_a_real_capture_matches_the_reference_decoder_by_hand():
    """Philips 26PFL560H's ``off`` command (SmartIR codes/media_player/1000.json).

    23 single-byte durations + one 0x00-escaped 2-byte duration (``0d 05``
    = 3333 units) = 24 durations, matching the reference ``broadlink``
    package's own ``data_to_pulses`` bit for bit (verified live, not just
    asserted here).
    """
    sample = ("JgAaAB0dOx4cHhweHR4cHhw8HR0dHhweOzsdAA0FAAAAAAAAAAAAAAAAAAA=")
    units = [29, 29, 59, 30, 28, 30, 28, 30, 29, 30, 28, 30, 28, 60,
             29, 29, 29, 30, 28, 30, 59, 59, 29, 3333]
    assert decode_packet(sample) == [int(u * UNIT_US) for u in units]
    assert len(decode_packet(sample)) == 24


def test_single_byte_and_escaped_durations():
    assert decode_packet(_packet(1)) == [int(1 * UNIT_US)]
    assert decode_packet(_packet(255)) == [int(255 * UNIT_US)]
    # 0x00 escapes to a big-endian 2-byte value: 0x01, 0x00 -> 256.
    assert decode_packet(_packet(0, 0x01, 0x00)) == [int(256 * UNIT_US)]
    assert decode_packet(_packet(1, 0, 0x02, 0x00)) == [
        int(1 * UNIT_US), int(512 * UNIT_US)
    ]


def test_not_base64_is_rejected():
    with pytest.raises(BroadlinkDecodeError, match="not valid base64"):
        decode_packet("not base64 at all!!")


def test_a_non_ir_type_byte_is_rejected_not_guessed_at():
    """Real upstream data has commands whose leading byte isn't 0x26 (D40) --
    refused, not decoded as if it were IR, since nothing says what format it
    actually is."""
    data = bytes([0xB2, 0x00, 0x01, 0x00, 0x05])
    with pytest.raises(BroadlinkDecodeError, match="not IR"):
        decode_packet(base64.b64encode(data).decode())


def test_a_declared_length_longer_than_the_payload_is_rejected():
    data = bytes([0x26, 0x00, 0x05, 0x00, 0x01, 0x02])  # declares 5, gives 2
    with pytest.raises(BroadlinkDecodeError, match="only 2 follow"):
        decode_packet(base64.b64encode(data).decode())


def test_a_truncated_escape_sequence_is_rejected():
    data = bytes([0x26, 0x00, 0x02, 0x00, 0x00, 0x01])  # escape with 1 byte, not 2
    with pytest.raises(BroadlinkDecodeError, match="no 2-byte value"):
        decode_packet(base64.b64encode(data).decode())


def test_a_zero_duration_is_rejected():
    data = bytes([0x26, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00])  # escape to value 0
    with pytest.raises(BroadlinkDecodeError, match="zero-length"):
        decode_packet(base64.b64encode(data).decode())


def test_too_short_to_hold_a_header_is_rejected():
    with pytest.raises(BroadlinkDecodeError, match="too short"):
        decode_packet(base64.b64encode(bytes([0x26, 0x00])).decode())
