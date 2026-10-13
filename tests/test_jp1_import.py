"""``rl import jp1`` (DESIGN D118 to D122): the reader of an upgrade, the decoding rules, the
master index, and the import over a small checkout and over the committed tree."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.jp1 import importer as jp
from remote_ledger.jp1 import rmdu
from remote_ledger.jp1.rmdu import rev8
from remote_ledger.remote import load_remote
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "87b0ac05bc3b73eb5d392b9f468ae24a875ff83e"


def upgrade(name: str, parms: str, *functions: tuple[str | None, str | None], fixed: str = "",
            variant: str = "") -> rmdu.Upgrade:
    lines = [f"Protocol.name={name}", f"ProtocolParms={parms}"]
    if variant:
        lines.append(f"Protocol.variantName={variant}")
    if fixed:
        lines.append(f"FixedData={fixed}")
    for i, (fname, hx) in enumerate(functions):
        if fname is not None:
            lines.append(f"Function.{i}.name={fname}")
        if hx is not None:
            lines.append(f"Function.{i}.hex={hx}")
    return rmdu.parse("\r\n".join(lines) + "\r\n")


def signal(up: rmdu.Upgrade, n: int = 0):
    return rmdu.signal_of(up, up.functions[n])


# --- the reader --------------------------------------------------------------------------------


def test_an_upgrade_is_its_fields_and_functions_and_a_wrapped_note_is_ignored():
    text = ("Description=Maximum XO-506C\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n"
            "Notes=a long note that\r\nwraps onto a line with no key\r\n"
            "Function.1.name=num_0\r\nFunction.1.hex=FF\r\nFunction.0.name=Power\r\nFunction.0.hex=AF\r\n"
            "Function.0.notes=hold it\r\nExtFunction.0.name=TV AV\r\nExtFunction.0.hex=7F\r\n"
            "ExtFunction.1.name=TV Component\r\n")
    up = rmdu.parse(text)
    assert up.protocol_name == "NEC1" and up.parms == ["6", "null", "null"]
    assert up.fields["Description"] == "Maximum XO-506C" and up.ext_functions == 2
    assert [(f.index, f.name, f.hex, f.notes) for f in up.functions] == [
        (0, "Power", "AF", "hold it"), (1, "num_0", "FF", None)]


def test_windows_1252_is_read_where_the_file_is_not_utf8():
    assert rmdu.decode_text("Description=Café".encode("utf-8")) == "Description=Café"
    assert rmdu.decode_text("Description=Caf\xe9".encode("cp1252")) == "Description=Café"


@pytest.mark.parametrize("hx,expected", [("AF", 0xAF), ("0f", 0x0F), ("FF BF", None), ("", None),
                                         ("G1", None), (None, None), ("A", None)])
def test_an_obc_is_exactly_one_hex_byte(hx, expected):
    assert rmdu.obc_byte(hx) == expected


def test_a_device_parameter_is_a_small_number_or_nothing():
    parms = "7 null 3000 x 12".split()
    assert [rmdu.parm(parms, i) for i in range(6)] == [7, None, None, None, 12, None]


# --- the decoding rules, anchored to codes the ledger holds from other sources ---------------------


def test_nec_is_the_complemented_reversed_obc_and_a_null_sub_device_is_the_complement():
    # the Samsung TV of the ledger's authored BN59-01199F: NECx2 7,7 and Power 2, Source 1, key 1 is 4
    up = upgrade("NECx2", "7 7 null", ("POWER", "BF"), ("SOURCE", "7F"), ("1", "DF"))
    assert [signal(up, i) for i in range(3)] == [
        rmdu.Signal("NECx2", 7, 7, 0x02), rmdu.Signal("NECx2", 7, 7, 0x01), rmdu.Signal("NECx2", 7, 7, 0x04)]
    # NEC1 with one device number: the sub device is its complement, as the Topping RC-15A's pair is
    one = upgrade("NEC1", "6 null null", ("Power", "AF"))
    assert signal(one) == rmdu.Signal("NEC1", 6, 0xF9, 0x0A)
    assert signal(upgrade("NEC1", "136 119 null", ("Power", "E7"))) == rmdu.Signal("NEC1", 0x88, 0x77, 0x18)


def test_no_repeats_and_nec2_are_the_ledgers_nec1_and_nec2_and_nec_x_needs_a_sub_device():
    assert signal(upgrade("NEC1 (No Repeats)", "204 2", ("0", "FF"))).protocol == "NEC1"
    assert signal(upgrade("NEC2", "128 null null", ("Power", "A7"))).protocol == "NEC2"
    assert signal(upgrade("NECx1", "5 null null", ("0", "77"))) == "the sub device parameter is missing"


def test_a_null_device_number_is_read_from_the_second_byte_of_fixed_data():
    # `null 223` with FixedData 20 FF 04 is the common NEC address 0x00DF
    up = upgrade("NEC1", "null 223 null", ("Power", "AF"), fixed="20 FF 04")
    assert signal(up) == rmdu.Signal("NEC1", 0, 223, 0x0A)
    assert signal(upgrade("NEC1", "null 223 null", ("Power", "AF"))) == "the device parameter is missing"


def test_sony_12_15_takes_the_device_from_the_lowest_bit_and_the_frame_from_its_size():
    # the Sony TV of the ledger's hifi-remote import: device 1 is 12-bit, device 151 15-bit; key 1 is
    # command 0, key 2 command 1, key 3 command 2 on both
    up = upgrade("Sony 12/15", "1 0 151 0", ("1", "00"), ("2", "80"), ("3", "40"), ("1b", "01"), ("2b", "81"))
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("Sony12", 1, None, 0), rmdu.Signal("Sony12", 1, None, 1), rmdu.Signal("Sony12", 1, None, 2),
        rmdu.Signal("Sony15", 151, None, 0), rmdu.Signal("Sony15", 151, None, 1)]
    assert signal(upgrade("Sony 12/15", "17 0 null 0", ("x", "01"))) == "the device the function selects is not set"


def test_sony_20_is_device_sub_device_and_the_reversed_command():
    # Sony Blu-ray 26.226: Power is 21 (OBC A8 reversed), 1 is 0, 2 is 1
    up = upgrade("Sony20", "26 226 null", ("Power", "A8"), ("1", "00"), ("2", "80"), ("bad", "01"))
    assert [signal(up, i) for i in range(3)] == [
        rmdu.Signal("Sony20", 26, 226, 0x15), rmdu.Signal("Sony20", 26, 226, 0), rmdu.Signal("Sony20", 26, 226, 1)]
    assert signal(up, 3) == "the command does not fit Sony's seven bits"


def test_rc5_is_a_device_slot_a_complemented_command_and_a_flag_for_the_seventh_bit():
    """``Rc5Translator``: the OBC's low two bits pick one of three device slots, its top six are the command
    complemented, and the slot's OBC>63 flag adds the seventh bit. Arcam AVR100's parameters, `16 0 16 1 17 0`:
    the second slot is device 16 again with the flag, which is how it sends 16-124 and 16-123."""
    up = upgrade("RC-5", "16 0 16 1 17 0", ("Power ON/OFF", "CC"), ("Power OFF", "0D"), ("Power ON", "11"),
                 ("Tuner", "F2"), ("a slot past the third is the first", "03"))
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("RC5", 16, None, 12), rmdu.Signal("RC5", 16, None, 124), rmdu.Signal("RC5", 16, None, 123),
        rmdu.Signal("RC5", 17, None, 3), rmdu.Signal("RC5", 16, None, 63)]


def test_a_slot_with_no_device_is_the_nearest_earlier_one_and_no_device_at_all_is_said_so():
    # slot 2 (OBC low bits 10) has no device: it is slot 1, device 16, whose flag is 1: 63 - 0x30 = 15, with 64
    assert signal(upgrade("RC-5", "8 0 16 1 null 0", ("x", "C2")), 0) == rmdu.Signal("RC5", 16, None, 63 - 0x30 + 64)
    # slot 1 has no device and slot 0 has: slot 0, no flag
    assert signal(upgrade("RC-5", "8 0 null 0 null 0", ("x", "C1")), 0) == rmdu.Signal("RC5", 8, None, 63 - 0x30)
    assert signal(upgrade("RC-5", "null 0 16 0 null 0", ("x", "C0")), 0) == "the device the function selects is not set"
    assert signal(upgrade("RC-5", "40 0 null 0 null 0", ("x", "C0")), 0) == "the RC-5 device is not five bits"


SONY_COMBO = "Sony Combo (12/15/20)"


def test_a_sony_combo_function_is_two_bytes_a_flag_and_a_device_by_the_frame():
    """``SonyComboTranslator() Translator(lsb,3,7)``: the first byte's top seven bits are the command reversed
    and its lowest bit says Sony15; the second byte is the device reversed (8 bits) for Sony15, else the device
    reversed in its top five bits, an index of the four sub devices in the next two, and Sony20 in its lowest.
    The parameters are Sony STR-DN860's, `8 40 35 104`."""
    up = upgrade(SONY_COMBO, "8 40 35 104", ("POWER", "A9 0C"), ("Master Volume +", "49 0C"), ("Tools/Options", "CE 09"),
                 ("TV power", "A8 80"), ("Sony20 index 2", "A8 5D"), ("Sony20 index 3", "A8 5F"))
    assert [signal(up, i) for i in range(6)] == [
        rmdu.Signal("Sony15", 48, None, 21), rmdu.Signal("Sony15", 48, None, 18), rmdu.Signal("Sony20", 16, 8, 115),
        rmdu.Signal("Sony12", 1, None, 21), rmdu.Signal("Sony20", 26, 35, 21), rmdu.Signal("Sony20", 26, 104, 21)]
    unset = upgrade(SONY_COMBO, "8 null null null", ("x", "A8 5D"))
    assert signal(unset, 0) == "the Sony20 sub device the function selects is not set"
    assert signal(upgrade(SONY_COMBO, "8", ("x", "A8")), 0) == "the function has no two OBC bytes"


def test_an_nec1_combo_function_is_the_sub_device_then_the_function_and_a_null_device_is_in_fixed_data():
    """``Translator(lsb,comp) Translator(lsb,comp,1,8,8)`` over ``CmdParms=Sub Device,OBC``: the first byte is the
    sub device. Adcom GTP-602's `power`, `58 FE` with device 26, is 26.229 function 128. A null device is the
    executor's effective one in FixedData: `E2` is 184, the default (Onkyo Master File)."""
    up = upgrade("NEC1 Combo", "26", ("power", "58 FE"), fixed="A7")
    assert signal(up, 0) == rmdu.Signal("NEC1", 26, 229, 128)
    onkyo = upgrade("NEC1 Combo", "null", ("PLII Movie", "BF AF"), fixed="E2")
    assert signal(onkyo, 0) == rmdu.Signal("NEC1", 184, 2, 10)
    assert signal(upgrade("NEC1 Combo", "null", ("x", "BF AF")), 0) == "the device parameter is missing"


def test_an_nec_4dev_combo_function_is_a_function_a_device_slot_and_a_style():
    """The second byte's top two bits are the device slot, the bits at offsets 11 and 15 the style (NEC1, NEC2,
    NECx1, NECx2). Emotiva UMC-1's parameters: slot 0 is 32.95, slot 1 is 2 with the complement as sub device."""
    parms = "32 95 2 null null null null null 0"
    fixed = "FB 05 BF 40 FF 00 FF 00"
    up = upgrade("NEC 4DEV Combo", parms, ("back sur-", "E5 20"), ("cancel", "65 60"), ("nec2", "E5 21"), ("necx1", "E5 30"),
                 fixed=fixed)
    assert [signal(up, i) for i in range(3)] == [
        rmdu.Signal("NEC1", 32, 95, 88), rmdu.Signal("NEC1", 2, 253, 89), rmdu.Signal("NEC2", 32, 95, 88)]
    # NECx wants a sub device, and slot 0 has none here
    assert signal(upgrade("NEC 4DEV Combo", "32 null", ("x", "E5 30")), 0) == "the sub device parameter is missing"
    # a null device is in FixedData, each byte complemented and reversed: FB is 32, and a null sub device is the complement
    consistent = "FB 04 BF 40 FF 00 FF 00"
    assert signal(upgrade("NEC 4DEV Combo", "null null 2 null", ("x", "E5 20"), fixed=consistent), 0) == rmdu.Signal("NEC1", 32, 223, 88)
    assert signal(upgrade("NEC 4DEV Combo", "32 95", ("x", "E5 60")), 0) == "the device the function selects is not set"


def test_the_yamaha_4dev_combo_is_nec_in_its_plain_style_and_the_relaxed_form_in_the_three_that_are_not():
    """The Yamaha variant's Y style (offsets 13 and 14, high bit first) is NEC, Y1, Y2 or Y3. The last three send a
    fourth byte that is the third with bits complemented, which NEC1-f16 and NEC2-f16 hold (DESIGN D158, D164):
    E = F ^ 0x7F for Y1, 0xFE for Y2 and 0x7E for Y3. F is 131 here (``3E``)."""
    up = upgrade("NEC 4DEV Yamaha Combo", "122 133 126 null", ("plain", "3E 21"), ("Y1", "3E 23"), ("Y2", "3E 25"),
                 ("Y3", "3E 27"), ("Y1 in NECx1", "3E 12"))
    assert signal(up, 0) == rmdu.Signal("NEC2", 122, 133, 131)
    assert [signal(up, i) for i in (1, 2, 3)] == [
        rmdu.Signal("NEC2-f16", 122, 133, 131 << 8 | 0xFC), rmdu.Signal("NEC2-f16", 122, 133, 131 << 8 | 0x7D),
        rmdu.Signal("NEC2-f16", 122, 133, 131 << 8 | 0xFD)]
    assert signal(up, 4) == ("the Yamaha style is sent in an NECx frame, whose relaxed form the ledger does not "
                             "register")


def test_the_yamaha_receiver_irptransmogrifier_captured_is_nec1_f16_with_e_39():
    """FM of the captured Yamaha receiver is NEC1-Yamaha {D=127,S=1,F=88,Y=1} (NEC1-y1_nofinalA.exp), whose fourth
    byte is 88 ^ 0x7F = 39. ``E5`` is the OBC of F = 88 (``rev8(~E5)``), and ``22`` says NEC1 and Y1."""
    up = upgrade("NEC 4DEV Yamaha Combo", "127 1 null null", ("FM", "E5 22"))
    assert signal(up) == rmdu.Signal("NEC1-f16", 127, 1, 88 * 256 + 39)


def test_nec1_f16_official_is_four_obc_bytes_device_sub_device_f_and_e_and_nec1_rnc_is_the_swapped_complement():
    """``NEC1-f16 Official``: four bytes, each complemented and reversed (``9E D8 BF F9`` is IrpTransmogrifier's
    NEC1-f16 capture D=134, S=228, F=2, E=96). ``NEC1-rnc`` is ``Translator(lsb)``: F is the OBC reversed and E its
    complement with the nibbles swapped (the NEC2-rnc capture's D=1, F=1 is E=239 and F=41 is E=109); a ``null``
    parameter is in FixedData, each byte reversed."""
    assert signal(upgrade("NEC1-f16 Official", "", ("x", "9E D8 BF F9"))) == rmdu.Signal("NEC1-f16", 134, 228, 608)
    assert signal(upgrade("NEC1-f16 Official", "", ("x", "9E D8 BF"))) == "the function has no four OBC bytes"
    rnc = upgrade("NEC1-rnc", "1 254", ("one", "80"), ("zero", "94"))
    assert [signal(rnc, i) for i in (0, 1)] == [rmdu.Signal("NEC1-f16", 1, 254, 1 * 256 + 239),
                                                rmdu.Signal("NEC1-f16", 1, 254, 41 * 256 + 109)]
    assert signal(upgrade("NEC1-rnc", "null null", ("one", "80"), fixed="80 7F")) == rmdu.Signal("NEC1-f16", 1, 254, 495)
    assert signal(upgrade("NEC1-rnc", "null null", ("one", "80"))) == "the device or the sub device parameter is missing"


def test_the_one_byte_executors_keep_the_obc_as_their_translator_does():
    """RC-6 is ``Translator()``, the OBC as it is; Aiwa and Denon ``Translator(lsb)``, reversed; JVC and
    Panasonic ``Translator(lsb,comp)``, reversed and complemented. The device is a parameter."""
    assert signal(upgrade("RC-6", "16", ("standby", "0C"))) == rmdu.Signal("RC6", 16, None, 12)
    assert signal(upgrade("RC-6", "null", ("x", "0C"))) == "the device parameter is missing"
    assert signal(upgrade("JVC", "3", ("a", "3F"), ("b", "FF"), ("c", "00"))) == rmdu.Signal("JVC", 3, None, 3)
    jvc = upgrade("JVC", "3", ("a", "3F"), ("b", "FF"), ("c", "00"))
    assert [signal(jvc, i) for i in (1, 2)] == [rmdu.Signal("JVC", 3, None, 0), rmdu.Signal("JVC", 3, None, 255)]
    assert signal(upgrade("Aiwa", "1 2", ("x", "01"))) == rmdu.Signal("Aiwa", 1, 2, 128)
    # null device and sub device are the protocol's defaults, 0 and 0
    assert signal(upgrade("Aiwa", "null null", ("x", "80"))) == rmdu.Signal("Aiwa", 0, 0, 1)
    assert signal(upgrade("Aiwa", "1 32", ("x", "80"))) == "the Aiwa sub device is not five bits"
    assert signal(upgrade("Denon", "8", ("x", "80"))) == rmdu.Signal("Denon", 8, None, 1)
    assert signal(upgrade("Denon", "32", ("x", "80"))) == "the device parameter is missing or is not five bits"


def test_panasonic_is_device_sub_device_and_the_function_reversed_and_complemented_with_oem_bytes_2_and_32():
    up = upgrade("Panasonic", "144 0 2 32", ("a", "7F"))
    assert signal(up) == rmdu.Signal("Panasonic", 144, 0, 1)
    assert signal(upgrade("Panasonic", "144 null null null", ("a", "7F"))) == rmdu.Signal("Panasonic", 144, 0, 1)
    assert signal(upgrade("Panasonic", "144 0 162 32", ("a", "7F"))) == "the OEM byte 162 is not Panasonic's 2, which the ledger's frame fixes"
    # the combo: the sub device is the first byte and the function the second, each complemented and reversed
    combo = upgrade("Panasonic Combo", "160 2 32", ("a", "7F 7F"), fixed="BF FB FA")
    assert signal(combo) == rmdu.Signal("Panasonic", 160, 1, 1)
    # a null main device is in FixedData: FA is 160, the default
    assert signal(upgrade("Panasonic Combo", "null 2 32", ("a", "7F 7F"), fixed="BF FB FA")) == rmdu.Signal("Panasonic", 160, 1, 1)
    assert signal(upgrade("Panasonic Combo", "160 2 20", ("a", "7F 7F"))) == "the OEM byte 20 is not Panasonic's 32, which the ledger's frame fixes"


def test_the_jvc_and_denon_combos_put_the_device_in_the_first_byte_and_the_function_in_the_second():
    """JVC Combo: ``CmdParms=OBC,Device`` over ``Translator(lsb,comp,0,8,8) Translator(lsb,comp,1,8,0)``, so the
    device is the first byte and the function the second. Denon Combo: the device is the first byte's top five
    bits (complemented, reversed), a bit that says Sharp or Denon, and the function is the second byte."""
    assert signal(upgrade("JVC Combo", "", ("x", "3F FF"))) == rmdu.Signal("JVC", 3, None, 0)
    denon = upgrade("Denon Combo (Official)", "", ("d", "E8 7F"), ("s", "EC 7F"))
    assert [signal(denon, i) for i in (0, 1)] == [rmdu.Signal("Denon", 8, None, 1), rmdu.Signal("Sharp", 8, None, 1)]
    assert signal(upgrade("Denon Combo (Official)", "", ("x", "E8"))) == "the function has no two OBC bytes"


def test_sharp_samsung36_proton_f12_and_recs80_keep_the_obc_as_their_translators_do():
    """Sharp ``Translator(lsb)``; Samsung36 ``Translator(lsb,0)`` with its four-bit E in the high bits of the
    function (D65); Proton ``Translator(lsb,comp)``; F12 ``Translator(lsb)`` with a four-bit device whose low
    three bits are D and the next S; RECS80 (45) the top six bits of the byte, complemented."""
    assert signal(upgrade("Sharp", "8", ("x", "80"))) == rmdu.Signal("Sharp", 8, None, 1)
    assert signal(upgrade("Sharp", "32", ("x", "80"))) == "the device parameter is missing or is not five bits"
    assert signal(upgrade("Samsung36", "48 15 0", ("POWER", "0E"))) == rmdu.Signal("Samsung36", 48, 15, 112)
    assert signal(upgrade("Samsung36", "48 15 1", ("POWER", "0E"))) == rmdu.Signal("Samsung36", 48, 15, 256 + 112)
    # null parameters are in FixedData: 0C is 48 reversed, F0 is 15, and the high nibble of the third byte is E
    assert signal(upgrade("Samsung36", "null null null", ("POWER", "0E"), fixed="0C F0 00")) == rmdu.Signal("Samsung36", 48, 15, 112)
    assert signal(upgrade("Samsung36", "null null null", ("POWER", "0E"))) == "the device or the sub device parameter is missing"
    assert signal(upgrade("Proton", "5", ("x", "3F"))) == rmdu.Signal("Proton", 5, None, 3)
    f12 = upgrade("F12", "7", ("x", "01"))
    assert signal(f12) == rmdu.Signal("F12_relaxed", 7, 0, 128)
    assert signal(upgrade("F12", "12", ("x", "01"))) == rmdu.Signal("F12_relaxed", 4, 1, 128)
    assert signal(upgrade("F12", "16", ("x", "01"))) == "the device parameter is missing or is not four bits"
    recs = upgrade("RECS80 (45)", "3", ("a", "00"), ("b", "FC"))
    assert [signal(recs, i) for i in (0, 1)] == [rmdu.Signal("RECS80", 3, None, 63), rmdu.Signal("RECS80", 3, None, 0)]
    assert signal(upgrade("RECS80 (45)", "8", ("a", "00"))) == "the device parameter is missing or is not three bits"


def test_denon_k_is_a_four_bit_field_and_a_twelve_bit_function_with_oem_bytes_84_and_50():
    """``Translator(lsb,comp,0,4) Translator(lsb,comp,1,12,4)`` over two bytes: the top nibble is the sub device, the
    other twelve bits the function, each complemented and reversed. The ledger's frame fixes the OEM bytes at 84
    and 50, the executor's defaults; a null device is in FixedData (FD is 4)."""
    assert signal(upgrade("Denon-K", "4 84 50", ("x", "77 FF"))) == rmdu.Signal("Denon-K", 4, 1, 1)
    assert signal(upgrade("Denon-K", "4 null null", ("x", "77 FF"))) == rmdu.Signal("Denon-K", 4, 1, 1)
    assert signal(upgrade("Denon-K", "null null null", ("x", "77 FF"), fixed="D5 B3 FD")) == rmdu.Signal("Denon-K", 4, 1, 1)
    assert signal(upgrade("Denon-K", "4 85 50", ("x", "77 FF"))) == "the OEM byte 85 is not Denon's 84, which the ledger's frame fixes"
    assert signal(upgrade("Denon-K", "16 84 50", ("x", "77 FF"))) == "the device parameter is missing or is not four bits"
    assert signal(upgrade("Denon-K", "4 84 50", ("x", "83"))) == "the function has no two OBC bytes"


def test_the_nec_2dev_combo_is_the_4dev_with_a_one_bit_device_slot_and_sharp_combo_is_the_denon_combo():
    two = upgrade("NEC 2DEV Combo", "32 95 2 null", ("a", "E5 20"), ("b", "E5 A0"), ("c", "E5 21"))
    assert [signal(two, i) for i in range(3)] == [
        rmdu.Signal("NEC1", 32, 95, 88), rmdu.Signal("NEC1", 2, 253, 88), rmdu.Signal("NEC2", 32, 95, 88)]
    sharp = upgrade("Sharp Combo (Official)", "", ("d", "E8 7F"), ("s", "EC 7F"))
    assert [signal(sharp, i) for i in (0, 1)] == [rmdu.Signal("Denon", 8, None, 1), rmdu.Signal("Sharp", 8, None, 1)]


def test_a_protocol_the_ioblaster_import_gives_extras_gets_them_here_too():
    """Samsung36's unit is 500 us in the ledger (D52), so a JP1 Samsung36 file carries the block the IR Blaster
    ones do, and one code compiles to one signal whichever source it came from."""
    from remote_ledger.irblaster.importer import PROTOCOL_EXTRAS

    block = jp.Importer.block("Samsung36")
    assert block["unitUs"] == 500 and block["claims"] == PROTOCOL_EXTRAS["Samsung36"]["claims"]
    assert "unitUs" not in jp.Importer.block("NEC1")


def test_pioneer_is_one_frame_written_as_the_ledgers_two_equal_parts():
    """``Translator(lsb,comp)`` for the device and the function: a single frame, which the ledger writes as its
    two parts equal (``D0:D`` and ``F0:F``). The function ``FF`` is ``rev8(~0xFF)``, 0; ``BF`` is ``rev8(0x40)``, 2;
    ``3F`` is ``rev8(0xC0)``, 3. A ``null`` device is in FixedData: ``BA`` is ``~0xA2`` reversed."""
    up = upgrade("Pioneer", "162", ("a", "FF"), ("b", "BF"), ("c", "3F"))
    assert [signal(up, i) for i in range(3)] == [
        rmdu.Signal("Pioneer-2Part", 0xA2A2, None, 0x0000), rmdu.Signal("Pioneer-2Part", 0xA2A2, None, 0x0202),
        rmdu.Signal("Pioneer-2Part", 0xA2A2, None, 0x0303)]
    assert signal(upgrade("Pioneer", "null", ("a", "FF"), fixed="BA")) == rmdu.Signal("Pioneer-2Part", 0xA2A2, None, 0)
    assert signal(upgrade("Pioneer", "256", ("a", "FF"))) == "the device parameter is missing"


def test_pioneer_mix_is_a_function_and_a_flag_byte_and_a_second_part_where_the_flag_says_so():
    """``Translator(lsb,comp,3) PioneerMixTranslator()``: the first byte is the function, ``rev8(~OBC)``; in the
    second the lowest bit says a two-part signal and the next two pick the prefix command. ``DB`` is 36, and
    device 170, prefix command 91, device 175 and function 36 are IrpTransmogrifier's decode of a real Pioneer
    receiver's capture (``device=0xAAAF, function=0x5B24``, pioneer.py)."""
    v2 = upgrade("Pioneer MIX", "170 175 90 91", ("guide", "DB 03"), ("one", "DB 00"), ("first", "DB 01"),
                 ("third", "DB 05"), ("wide", "DB 08"), variant="2")
    assert [signal(v2, i) for i in range(5)] == [
        rmdu.Signal("Pioneer-2Part", 0xAAAF, None, 0x5B24), rmdu.Signal("Pioneer-2Part", 0xAAAA, None, 0x2424),
        rmdu.Signal("Pioneer-2Part", 0xAAAF, None, 0x5A24),
        "the function selects a prefix command the variant does not have",
        "the flag byte has bits the variant does not use"]
    v3 = upgrade("Pioneer MIX", "170 175 90 91 94 null", ("third", "DB 05"), ("fourth", "DB 07"), variant="3")
    assert signal(v3, 0) == rmdu.Signal("Pioneer-2Part", 0xAAAF, None, 0x5E24)
    assert signal(v3, 1) == "the device or the prefix command parameter is missing"
    # a null parameter is in FixedData, at the byte the DeviceTranslator's offset gives (variant 2: 0, 2, 1, 3)
    nulls = upgrade("Pioneer MIX", "null null null null", ("guide", "DB 03"), fixed="AA A5 0A 25", variant="2")
    assert signal(nulls) == rmdu.Signal("Pioneer-2Part", 0xAAAF, None, 0x5B24)
    for variant in ("", "4", "5"):
        assert signal(upgrade("Pioneer MIX", "170 175 90 91", ("a", "DB 03"), variant=variant)) == \
            "the variant is not read (its devices are chosen by more flag bits)"


def test_panasonic_multi_device_is_the_panasonic_combo_with_its_two_bytes_the_other_way_round():
    """``Translator(lsb,comp,0,8,8) Translator(lsb,comp,1)``: the function is the first byte and the sub device the
    second, each complemented and reversed (``67`` is 25, ``DF`` is 4); the OEM bytes are parameters 2 and 3, and a
    null device is in FixedData (``FA`` is 160)."""
    up = upgrade("Panasonic Multi-Device (Hacked)", "160 null null null", ("a", "67 DF"), ("b", "FF FF"))
    assert [signal(up, i) for i in (0, 1)] == [rmdu.Signal("Panasonic", 160, 4, 25), rmdu.Signal("Panasonic", 160, 0, 0)]
    nulls = upgrade("Panasonic Multi-Device (Hacked)", "null null null null", ("a", "67 DF"), fixed="bf fb fa ff")
    assert signal(nulls) == rmdu.Signal("Panasonic", 160, 4, 25)
    assert signal(upgrade("Panasonic Multi-Device (Hacked)", "160 null 3 32", ("a", "67 DF"))) == \
        "the OEM byte 3 is not Panasonic's 2, which the ledger's frame fixes"


def test_recs80_68_jvc_48_and_teac_k_are_their_translators():
    """RECS80 (68) keeps its device complemented (``Translator(0,3,2,comp)``) and the ledger's frame sends what is
    kept: parameter 0 is device 7, and ``D8`` is 63 - 54 = 9. JVC-48 is device, sub device and ``rev8(~OBC)``
    (``F7`` is 16). Teac-K is the first byte as ``rev8(~OBC)`` (``7F`` is 1), a four-bit device, the sub device
    (or ``DF`` of FixedData, 4) and the OEM bytes 67 and 83 the ledger's frame fixes."""
    assert signal(upgrade("RECS80 (68)", "0", ("a", "D8"))) == rmdu.Signal("RECS80-0068", 7, None, 9)
    assert signal(upgrade("RECS80 (68)", "8", ("a", "D8"))) == "the device parameter is missing or is not three bits"
    assert signal(upgrade("JVC-48", "34 26", ("a", "F7"))) == rmdu.Signal("JVC-48", 34, 26, 16)
    assert signal(upgrade("JVC-48", "34 null", ("a", "F7"))) == "the device or the sub device parameter is missing"
    assert signal(upgrade("Teac-K", "0 4 null null", ("a", "7F FF"))) == rmdu.Signal("Teac-K", 0, 4, 1)
    assert signal(upgrade("Teac-K", "0 null null null", ("a", "7F FF"), fixed="3D 35 FF DF")) == rmdu.Signal("Teac-K", 0, 4, 1)
    assert signal(upgrade("Teac-K", "16 4 null null", ("a", "7F FF"))) == "the device parameter is missing or is not four bits"
    assert signal(upgrade("Teac-K", "0 4 68 null", ("a", "7F FF"))) == "the OEM byte 68 is not Teac's 67, which the ledger's frame fixes"


def test_rca_is_rca_at_56_khz_or_rca_38_at_38_by_executor_and_carrier_parameter():
    """``RCA-38`` and the first variant of ``RCA`` are ``Translator()``: the function is the OBC as it is, at 38 kHz for
    the first and 56 kHz for the second. ``RCA`` (variant 2) and ``RCA-56`` are ``Translator(comp)`` (``B5`` is 0x4A) and
    say their carrier in parameter 1: 1 is 38 kHz, 0 is 57 or 56 (D152, D189)."""
    assert signal(upgrade("RCA-38", "15", ("a", "4A"))) == rmdu.Signal("RCA-38", 15, None, 0x4A)
    assert signal(upgrade("RCA", "15", ("a", "4A"))) == rmdu.Signal("RCA", 15, None, 0x4A)
    assert signal(upgrade("RCA-56", "15 1", ("a", "B5"))) == rmdu.Signal("RCA-38", 15, None, 0x4A)
    assert signal(upgrade("RCA", "15 1", ("a", "B5"), variant="2")) == rmdu.Signal("RCA-38", 15, None, 0x4A)
    assert signal(upgrade("RCA-56", "15 0", ("a", "B5"))) == rmdu.Signal("RCA", 15, None, 0x4A)
    assert signal(upgrade("RCA", "5 0", ("a", "B5"), variant="2")) == rmdu.Signal("RCA", 5, None, 0x4A)
    assert signal(upgrade("RCA", "15 2", ("a", "B5"), variant="2")) == "the carrier parameter is missing"
    assert signal(upgrade("RCA-56", "15 null", ("a", "B5"))) == "the carrier parameter is missing"
    assert signal(upgrade("RCA", "15", ("a", "4A"), variant="3")) == "the variant is not read"
    assert signal(upgrade("RCA", "16", ("a", "4A"))) == "the device parameter is missing or is not four bits"
    assert signal(upgrade("RCA-38", "16", ("a", "4A"))) == "the device parameter is missing or is not four bits"
    assert signal(upgrade("RCA-38 Official", "null", ("a", "4A"), fixed="00")) == "the device parameter is missing or is not four bits"


def test_rca_combo_is_the_device_and_the_function_in_two_bytes_and_a_prefix_is_not_read():
    """The first variant, ``Translator(0,4,0) Translator(1,8,8)``: the device is the top nibble of the first byte, whose
    low nibble is the executor's fixed 15, and the function the second byte (``5F 0C`` is device 5, function 12). The
    second, ``Translator(0,4,0,comp) Translator(1,2,6) Translator(2,8,8,comp)``: the device and the function complemented
    and the two lowest bits of the first byte a prefix choice (``A0 F3`` is device 5, function 12)."""
    assert signal(upgrade("RCA Combo (Official)", "", ("a", "5F 0C"))) == rmdu.Signal("RCA", 5, None, 12)
    assert signal(upgrade("RCA Combo (Official)", "", ("a", "50 0C"))) == "the first byte's low four bits are not the executor's fixed 15"
    assert signal(upgrade("RCA Combo (Official)", "null null", ("a", "A0 F3"), variant="2")) == rmdu.Signal("RCA", 5, None, 12)
    assert signal(upgrade("RCA Combo (Official)", "null null", ("a", "A1 F3"), variant="2")) == (
        "the function sends a prefix code first or sets a bit the variant does not use")
    assert signal(upgrade("RCA Combo (Official)", "null null", ("a", "A0 F3"), variant="3")) == "the variant is not read"
    assert signal(upgrade("RCA Combo (Official)", "", ("a", "5F"))) == "the function has no two OBC bytes"


def test_tdc_is_the_variant_device_and_sub_device_and_the_obc_shifted_right_by_one():
    """``Translator(0,7)`` keeps the seven bits of the OBC byte from the top (``04`` is 2, ``0A`` is 5); the parameters
    are Variant, Device and Sub Device, and the variant is the ledger's ``TDC-38`` (0) or ``TDC-56`` (1)."""
    up = upgrade("TDC", "0 6 10", ("1", "04"), ("2", "0A"), ("select", "3C"))
    assert [signal(up, i) for i in range(3)] == [rmdu.Signal("TDC-38", 6, 10, 2), rmdu.Signal("TDC-38", 6, 10, 5),
                                                 rmdu.Signal("TDC-38", 6, 10, 30)]
    assert signal(upgrade("TDC", "1 14 10", ("2", "06"))) == rmdu.Signal("TDC-56", 14, 10, 3)
    assert signal(upgrade("TDC", "0 6 10", ("a", "FE"))) == rmdu.Signal("TDC-38", 6, 10, 127)
    assert signal(upgrade("TDC", "0 6 10", ("a", "05"))) == "the OBC byte has its lowest bit set, which a seven-bit command does not use"
    assert signal(upgrade("TDC", "2 6 10", ("a", "04"))) == "the variant, device or sub device parameter is missing"
    assert signal(upgrade("TDC", "0 null 10", ("a", "04"))) == "the variant, device or sub device parameter is missing"
    assert signal(upgrade("TDC", "0 32 10", ("a", "04"))) == "the device or the sub device is not five bits"
    assert signal(upgrade("TDC", "0 6 32", ("a", "04"))) == "the device or the sub device is not five bits"


def test_gi_cable_is_device_0_and_the_obc_reversed():
    """``Translator(lsb)``, and the executor has no device parameter (IrpTransmogrifier says ``D==0`` of it): ``80`` is
    the digit 1, ``A0`` the digit 5 and ``2C`` is 52, the up arrow of the box IrpTransmogrifier captured."""
    up = upgrade("GI Cable", "", ("0", "00"), ("1", "80"), ("5", "A0"), ("up", "2C"))
    assert [signal(up, i) for i in range(4)] == [rmdu.Signal("GI_Cable", 0, None, f) for f in (0, 1, 5, 52)]
    assert signal(upgrade("GI Cable", "null null", ("1", "80"))) == rmdu.Signal("GI_Cable", 0, None, 1)


def test_mce_is_the_obc_as_it_is_with_a_seven_bit_device_and_the_second_oem_byte():
    """``Translator(0)``: the function is the OBC as it is (``0C`` is Power, ``28`` the Xbox 360 remote's OPEN);
    ``Device:7`` and ``OEM2:8`` are parameters 0 and 1, and the first OEM byte is the executor's 128."""
    assert signal(upgrade("MCE", "116 15", ("OPEN", "28"), ("Power", "0C"))) == rmdu.Signal("MCE", 116, 15, 0x28)
    assert signal(upgrade("MCE", "4 15", ("Power", "0C"))) == rmdu.Signal("MCE", 4, 15, 12)
    assert signal(upgrade("MCE", "128 15", ("x", "0C"))) == "the device is not seven bits"
    assert signal(upgrade("MCE", "null 15", ("x", "0C"))) == "the device or the second OEM byte parameter is missing"


def test_nokia32_is_the_obc_as_it_is_with_the_device_sub_device_and_the_low_seven_bits_of_x():
    """``Translator()``: F is the OBC; the parameters are Device, Sub Device, X and the ``High X-bit`` mode, and a
    ``null`` one is the byte of FixedData the device translators give it (mode, device, sub device, X). The top bit of
    X is the toggle's place: 166 is 0x80 | 38, read with the mode Toggle as X = 38; with the mode Constant it is a
    toggle bit that a form cannot send, and the mode Release is not read."""
    assert signal(upgrade("Nokia32", "33 160 38 0", ("a", "80"))) == rmdu.Signal("Nokia32", 33, 160, 38 << 8 | 0x80)
    assert signal(upgrade("Nokia32", "36 128 166 1", ("a", "0C"))) == rmdu.Signal("Nokia32", 36, 128, 38 << 8 | 12)
    assert signal(upgrade("Nokia32", "36 128 166 0", ("a", "0C"))) == (
        "the high X bit is set and constant, which a form cannot send (its toggle is not in a file)")
    assert signal(upgrade("Nokia32", "36 128 38 2", ("a", "0C"))) == "the high X bit is sent at release, which a form cannot say"
    assert signal(upgrade("Nokia32", "null null null null", ("a", "80"), fixed="00 21 A0 26")) == \
        rmdu.Signal("Nokia32", 33, 160, 38 << 8 | 0x80)
    assert signal(upgrade("Nokia32", "null 160 38 0", ("a", "80"))) == "the device, sub device, X or high X-bit parameter is missing"


def test_panasonic_old_is_f_reversed_in_the_top_six_bits_and_the_device_the_low_two_bits_select():
    """``Translator(comp,0,2,6) Translator(lsb,1,6,0)``: ``9B`` is F = 25 (the key 0 of the cable box IrpTransmogrifier
    holds Pronto strings of, D=27, F=25) and ``0B`` is 16 (the key 1), both for Device 1; low bits 10 and 01 pick Device
    2 and 3 and 00 is OBC/2. A null device is in FixedData (``90`` is 27)."""
    up = upgrade("Panasonic (old)", "27 28 30", ("0", "9B"), ("1", "0B"), ("on 2", "0A"), ("on 3", "09"), ("half", "08"))
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("Panasonic_Old", 27, None, 25), rmdu.Signal("Panasonic_Old", 27, None, 16),
        rmdu.Signal("Panasonic_Old", 28, None, 16), rmdu.Signal("Panasonic_Old", 30, None, 16),
        "the function sends OBC/2, which is not read"]
    assert signal(upgrade("Panasonic (old)", "null null null", ("0", "9B"), fixed="90 90 90")) == \
        rmdu.Signal("Panasonic_Old", 27, None, 25)
    assert signal(upgrade("Panasonic (old)", "32 null null", ("0", "9B"))) == "the device is not five bits"


def _committed_jp1_keys(protocol: str):
    """``(executor, label, device, subdevice, function, file)`` of every key of the committed files that send ``protocol``:
    the executor and the function's own name are read from the citation."""
    shape = re.compile(r"function \d+ '(.*)' \(OBC [0-9A-F ]+; ([^;]+?), parms [^)]*\)")
    for path in sorted((ROOT / jp.IMPORT_ROOT).rglob("*.json")):
        doc = load(path)
        if doc.get("protocol", {}).get("name") != protocol:
            continue
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                m = shape.search(form["source"])
                yield (m[2], m[1], int(form["device"], 0), None if "subdevice" not in form else int(form["subdevice"], 0),
                       int(form["function"], 0), path.name)


def test_the_jp1_rca_upgrades_send_the_codes_the_lirc_remotes_of_rca_devices_send():
    """An independent check of the RCA rules at 56 kHz, from another project. The LIRC remotes of RCA devices
    (878 keys, 290 distinct device and function pairs: tests/test_rca.py's layout decoder) have the codes of TVs, a game
    console's DVD kit, converter boxes, a VCR and a home theater. The 18 upgrades of the executor ``RCA`` (its first
    variant, the function the OBC as it is) send 461 of 552 keys among them, and the same keys with the function
    complemented 32 and with its bits reversed 128. The nine of ``RCA Combo (Official)`` (the device and the function in
    two bytes, one of its variants complemented) send 403 of 552. The device 2 of one upgrade has no LIRC remote."""
    from test_rca import lirc_codes

    codes = lirc_codes()
    counts: Counter = Counter()
    for executor, _, device, _, function, _ in _committed_jp1_keys("RCA"):
        counts[executor] += 1
        counts[executor, "plain"] += (device, function) in codes
        counts[executor, "complemented"] += (device, ~function & 0xFF) in codes
        counts[executor, "reversed"] += (device, rev8(function)) in codes
    assert [counts["RCA"], counts["RCA", "plain"], counts["RCA", "complemented"], counts["RCA", "reversed"]] == [552, 461, 32, 128]
    assert [counts["RCA Combo (Official)"], counts["RCA Combo (Official)", "plain"]] == [552, 403]


def test_rc6_m_20n_is_the_obc_as_it_is_with_mode_6_a_device_and_a_four_bit_sub_device():
    """``Translator()``: F is the OBC (``58`` is 88, the Sky+ remote's Up). ``M=6, Device, Sub Device`` are parameters 0 to
    2 and a ``null`` one is the value FixedData keeps, in the layout of the variant: the first variant has M in bits 1 to
    3 of the first byte, the sub device in its low nibble and the device in the second byte (``EC 05``: 6, 12, 5); the
    ``short`` variant doubles every bit as the bit and its complement (``A9 35 56 6A 05 00`` is 6, 5, 12, ``A9 35 55 55
    05 00`` is 6, 0, 0). The mode must be 6, which the ledger's frame fixes."""
    assert signal(upgrade("RC6-M-20n", "6 0 12", ("Up", "58"))) == rmdu.Signal("RC6-6-20", 0, 12, 88)
    assert signal(upgrade("RC6-M-20n", "6 5 12 null", ("Up", "58"), variant="short")) == rmdu.Signal("RC6-6-20", 5, 12, 88)
    assert signal(upgrade("RC6-M-20n", "null null null", ("Up", "58"), fixed="EC 05")) == rmdu.Signal("RC6-6-20", 5, 12, 88)
    assert signal(upgrade("RC6-M-20n", "null null null", ("Up", "58"), fixed="E0 00")) == rmdu.Signal("RC6-6-20", 0, 0, 88)
    assert signal(upgrade("RC6-M-20n", "null null null null", ("Up", "58"), fixed="A9 35 56 6A 05 00", variant="short")) == \
        rmdu.Signal("RC6-6-20", 5, 12, 88)
    assert signal(upgrade("RC6-M-20n", "null null null null", ("Up", "58"), fixed="a9 35 55 55 05 00", variant="short")) == \
        rmdu.Signal("RC6-6-20", 0, 0, 88)
    assert signal(upgrade("RC6-M-20n", "null 7 3 null", ("Up", "58"), fixed="a9 35 55 55 05 00", variant="short")) == \
        rmdu.Signal("RC6-6-20", 7, 3, 88)
    assert signal(upgrade("RC6-M-20n", "5 0 12", ("Up", "58"))) == "the mode is not 6, which the ledger's RC6-6-20 fixes"
    assert signal(upgrade("RC6-M-20n", "6 0 16", ("Up", "58"))) == "the device is not eight bits or the sub device not four"
    assert signal(upgrade("RC6-M-20n", "6 0 12", ("Up", "58"), variant="2")) == "the variant is not read"
    assert signal(upgrade("RC6-M-20n", "null null null", ("Up", "58"))) == "FixedData is too short for the null parameters"


def test_the_jp1_rc6_m_20n_upgrades_send_the_codes_of_the_lirc_sky_remotes_and_the_keys_irptransmogrifier_captured():
    """An independent check of the RC6-M-20n rule, from two projects. The LIRC remotes of Sky and Pace boxes (451 keys of
    nine files, tests/test_rc6_6_20.py's layout decoder) hold 38 codes of device 0 and sub device 0 and 38 of sub device 12
    (and 121 of sub device 3, which no upgrade has). The 15 upgrades send **262 of their 278 keys for device 0 and sub
    device 0 and 179 of 181 for sub device 12** among them: 441 of 535, where the 76 keys of device 5 (two upgrades)
    have no LIRC remote. And IrpTransmogrifier's capture of a Sky+ remote (device 0, sub device 12) has the keys 0 to 3
    as functions 0 to 3 and Up, Down, Left, Right and OK as 88 to 92: **every one of the 15 upgrades names those keys
    and sends those functions** (135 of 135)."""
    from test_rc6_6_20 import lirc_frames

    lirc: dict[tuple[int, int], set[int]] = {}
    for _, _, (_, device, subdevice, function) in lirc_frames():
        lirc.setdefault((device, subdevice), set()).add(function)
    assert {k: len(v) for k, v in lirc.items()} == {(0, 0): 38, (0, 3): 121, (0, 12): 38}
    rows = list(_committed_jp1_keys("RC6-6-20"))
    assert len(rows) == 535 and {row[0] for row in rows} == {"RC6-M-20n"}
    hits: Counter = Counter()
    for _, _, device, subdevice, function, _ in rows:
        hits[device, subdevice] += 1
        hits[device, subdevice, "lirc"] += function in lirc.get((device, subdevice), ())
    assert [hits[k] for k in ((0, 0), (0, 0, "lirc"), (0, 12), (0, 12, "lirc"), (5, 12), (5, 12, "lirc"))] == \
        [278, 262, 181, 179, 76, 0]
    captured = {"0": 0, "1": 1, "2": 2, "3": 3, "up": 88, "down": 89, "left": 90, "right": 91, "ok": 92}
    agree: Counter = Counter()
    for _, label, _, _, function, _ in rows:
        name = re.sub(r"(?i)^(num_|direction|arrow )|[ _]?arrow$|/ok$", "", label.strip()).lower()
        name = {"select": "ok"}.get(name, name)
        if name in captured:
            agree[function == captured[name]] += 1
    assert agree == {True: 135}


def test_the_jp1_gi_cable_upgrades_send_the_codes_of_the_lirc_remotes_and_the_keys_irptransmogrifier_names():
    """An independent check of the G.I.Cable rule (device 0, the function the OBC reversed), from two projects. The LIRC
    remotes of cable boxes (777 keys: tests/test_gicable.py's layout decoder) hold 68 functions of device 0 (and two
    keys of device 4). The 20 upgrades of ``GI Cable`` send 973 of their 982 keys among them; with the OBC as it is
    271 would be and with it complemented 32. And IrpTransmogrifier's capture of a cable box names the keys 0, 1, 5, 6
    and the four arrows (functions 0, 1, 5, 6 and 52 to 55) and Play (27): the upgrades agree on all 140 uses of
    the first eight names, and on Play in 10 of 17 (the other seven send 56, a different model)."""
    from test_gicable import lirc_codes

    codes = lirc_codes()
    rows = list(_committed_jp1_keys("GI_Cable"))
    assert len(rows) == 982 and {(d, s) for _, _, d, s, _, _ in rows} == {(0, None)}
    assert sum((0, f) in codes for *_, f, _ in rows) == 973
    assert sum((0, rev8(f)) in codes for *_, f, _ in rows) == 271
    assert sum((0, ~f & 0xFF) in codes for *_, f, _ in rows) == 32
    captured = {"0": 0, "1": 1, "5": 5, "6": 6, "up": 52, "down": 53, "left": 54, "right": 55, "play": 27}
    agree: Counter = Counter()
    for _, label, _, _, function, _ in rows:
        name = re.sub(r"(?i)^(num_|arrow )|[ _]?arrow$", "", label.strip()).lower()
        if name in captured:
            agree[name == "play", function == captured[name]] += 1
    assert agree == {(False, True): 140, (True, True): 10, (True, False): 7}


def test_the_jp1_tdc_upgrades_for_device_6_and_sub_device_10_send_the_keys_irptransmogrifier_captured():
    """An independent check of the TDC rule, from another project's hardware capture. IrpTransmogrifier's
    ``TDC-38_6_10`` is a TDC IPTV remote, device 6 and sub device 10, whose keys 1 to 4 are functions 2, 5, 6 and 9 and whose
    Up, Down, Left, Right and OK are 25, 26, 29, 33 and 30. The four upgrades for device 6 and sub device 10, read as
    ``OBC >> 1``, name those nine keys and send every one of those functions (36 of 36). The capture of a TDC-56 remote is
    device 14, which no upgrade has, so ``TDC-56`` rests on the same translator and the variant parameter."""
    captured = {"1": 2, "2": 5, "3": 6, "4": 9, "up": 25, "down": 26, "left": 29, "right": 33, "select": 30}
    seen: dict[str, dict[str, int]] = {}
    for executor, label, device, sub, function, name in _committed_jp1_keys("TDC-38"):
        if (device, sub) != (6, 10):
            continue
        key = re.sub(r"(?i)^num_|[ _]?arrow$", "", label.strip()).lower()
        if key in captured:
            seen.setdefault(name, {})[key] = function
    assert len(seen) == 4 and all(v == captured for v in seen.values()), seen
    assert any(row[2:4] == (14, 10) for row in _committed_jp1_keys("TDC-56")) is False


def test_pioneer_3dev_and_4dev_are_two_parts_with_the_device_the_function_selects():
    """``Pioneer3DevXlator``: the first byte is the OBC reversed (``38`` is 28); the top three bits of the second pick the
    device (``80`` Device 1, ``40`` Device 2, ``00`` Device 3) and its low five, reversed, are the second part's OBC 2,
    which gets ``0x80`` if the OBC has bit 7 and ``0x40`` if it lacks bit 6 (``01 94``: OBC 0x80, five bits 20 reversed to
    5, so 5 + 0x80 + 0x40 = 0xC5). A null device is the byte of FixedData reversed (``25`` is 164). ``Pioneer4DevXlator``:
    two bytes, each an OBC reversed with the device bit (0x04 of the byte) set or clear; the device is 2 * the first's bit
    and the second's, of the four, and the OBCs are the bytes reversed with bit 5 cleared (``3C 38`` is Device 3 and 28 twice).
    Both parts use the chosen device, and the ledger writes a one-part signal as its two parts equal."""
    up = upgrade("Pioneer 3DEV", "165 164 null", ("a", "38 80"), ("b", "38 40"), ("c", "38 00"), ("d", "01 94"),
                 ("e", "38 20"), fixed="a5 25 25")
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("Pioneer-2Part", 165 << 8 | 165, None, 28 << 8 | 28),
        rmdu.Signal("Pioneer-2Part", 164 << 8 | 164, None, 28 << 8 | 28),
        rmdu.Signal("Pioneer-2Part", 164 << 8 | 164, None, 28 << 8 | 28),
        rmdu.Signal("Pioneer-2Part", 165 << 8 | 165, None, 0x80 << 8 | 0xC5),
        rmdu.Signal("Pioneer-2Part", 164 << 8 | 164, None, 28 << 8 | 28)]
    four = upgrade("Pioneer 4DEV", "165 164 null null", ("a", "38 38"), ("b", "38 3C"), ("c", "3C 38"), ("d", "3C 3C"),
                   ("e", "5A 5E"), fixed="A5 25 25 05")
    assert [signal(four, i) for i in range(5)] == [
        rmdu.Signal("Pioneer-2Part", 165 << 8 | 165, None, 28 << 8 | 28),
        rmdu.Signal("Pioneer-2Part", 164 << 8 | 164, None, 28 << 8 | 28),
        rmdu.Signal("Pioneer-2Part", 164 << 8 | 164, None, 28 << 8 | 28),
        rmdu.Signal("Pioneer-2Part", 160 << 8 | 160, None, 28 << 8 | 28),
        rmdu.Signal("Pioneer-2Part", 164 << 8 | 164, None, 90 << 8 | 90)]               # 5A 5E: Device 2, both OBCs 90
    assert signal(upgrade("Pioneer 4DEV", "null null null null", ("a", "38 38"))) == "the device the function selects is not set"
    assert signal(upgrade("Pioneer 3DEV", "300 164 null", ("a", "38 80"))) == "a parameter is not a byte"
    assert signal(upgrade("Pioneer 3DEV", "165 164 null", ("a", "38"))) == "the function has no two OBC bytes"


def test_the_jp1_pioneer_3dev_and_4dev_upgrades_send_the_codes_the_ir_blaster_database_has():
    """An independent check of the two rules, from another source. The IR Blaster import holds 1,673 distinct Pioneer-2Part
    codes in 132 files. The six 3DEV upgrades send **267 of their 292 keys** among them (91.4%) and the eight 4DEV
    upgrades **507 of 675** (75.1%), five of them 270 of 280 (96.4%) and the other three (``VSX-D901S``, ``Pioneer Master List``,
    ``PD-TM2``) 237 of 395, the last two being catalogues of many devices whose codes the database lacks. A wrong rule would fail: the OBCs
    complemented send 0 and 0, bit-reversed 66 and 105."""
    irblaster: set[tuple[int, int]] = set()
    for path in (ROOT / "remotes" / "irblaster").rglob("*.json"):
        doc = load(path)
        if doc.get("protocol", {}).get("name") == "Pioneer-2Part":
            for spec in doc["keys"].values():
                irblaster |= {(int(f["device"], 0), int(f["function"], 0)) for f in spec["forms"] if f.get("type") == "irp"}
    assert len(irblaster) == 1_673
    counts: Counter = Counter()
    per: Counter = Counter()
    for executor, _, device, _, function, name in _committed_jp1_keys("Pioneer-2Part"):
        if executor not in ("Pioneer 3DEV", "Pioneer 4DEV"):
            continue
        hi, lo = function >> 8, function & 0xFF
        counts[executor] += 1
        counts[executor, "plain"] += (device, function) in irblaster
        counts[executor, "complemented"] += (device, (~hi & 0xFF) << 8 | (~lo & 0xFF)) in irblaster
        counts[executor, "reversed"] += (device, rev8(hi) << 8 | rev8(lo)) in irblaster
        per[name] += (device, function) in irblaster
        per[name, "n"] += 1
    for executor, expected in (("Pioneer 3DEV", [292, 267, 0, 66]), ("Pioneer 4DEV", [675, 507, 0, 105])):
        assert [counts[executor], counts[executor, "plain"], counts[executor, "complemented"],
                counts[executor, "reversed"]] == expected, executor
    faithful = ("Audio-Pioneer_Elite_VSX-40__2011.12.24a.json", "Audio-Pioneer_Receiver_SX-315.json",
                "Audio-Pioneer_Receiver_VSX-C100.json", "Audio-Pioneer_VSX-521.json", "Audio-Pioneer_VSX-818V.json")
    assert (sum(per[n] for n in faithful), sum(per[n, "n"] for n in faithful)) == (270, 280)
    others = ("Audio-Pioneer_VSX-D901S.json", "Audio-Pioneer_Master_List.json", "CD-Pioneer_PD-TM2.json")
    assert (sum(per[n] for n in others), sum(per[n, "n"] for n in others)) == (237, 395)


def test_pioneer_dvd_sends_one_frame_or_two_by_the_obcs_bit_5_and_the_dv_c505_notes_agree_38_of_38():
    """``Translator(lsb,comp,3)``: the function's byte is the OBC complemented and reversed (``C2`` is 188); the OBC's ``0x20`` says
    two parts (``Device 1`` and the prefix command, then ``Device 2`` and the OBC) and its absence one (``Device 1`` and the OBC),
    which is the byte's bit at offset 5 (``0x04``) the other way round, as the protocol's notes put it. The DV-C505 upgrade's notes
    spell out the frame of 38 functions, **33 of two parts and 5 of one, and all 38 are what this reads**. The rows below are six
    of them, from the file."""
    rows = [("Power", "c2", "163 153 66 001 - 175 188 C2 028"), ("Zoom", "13", "163 153 66 001 - 175   55 13 154"),
            ("Arrow Left", "39", "163 153 66 001 - 175   99 39 203"), ("Play", "86", "163 158 86 254"),
            ("Stop", "e6", "163 152 E6 253"), ("Pause", "06", "163 159 06 002")]
    text = "Protocol.name=Pioneer DVD\r\nProtocolParms=163 175 153\r\nFixedData=3a 66 0a\r\n" + "".join(
        f"Function.{i}.name={n}\r\nFunction.{i}.hex={h}\r\nFunction.{i}.notes={note}\r\n" for i, (n, h, note) in enumerate(rows))
    up = rmdu.parse(text)
    shape = re.compile(r"(\d+) (\d+) [0-9A-F]{2} \d+(?: - (\d+) +(\d+) [0-9A-F]{2} \d+)?", re.I)
    for i, (_, _, note) in enumerate(rows):
        d1, c1, d2, c2 = shape.fullmatch(note).groups()
        want = (rmdu.Signal("Pioneer-2Part", int(d1) << 8 | int(d2), None, int(c1) << 8 | int(c2)) if d2 else
                rmdu.Signal("Pioneer-2Part", int(d1) << 8 | int(d1), None, int(c1) << 8 | int(c1)))
        assert signal(up, i) == want, rows[i]
    # a null parameter is the byte FixedData keeps (Device 1 at 0, the prefix command at 1, Device 2 at 2)
    nulls = upgrade("Pioneer DVD", "null null null", ("Power", "C2"), ("Play", "86"), fixed="3a 66 0a")
    assert [signal(nulls, i) for i in range(2)] == [
        rmdu.Signal("Pioneer-2Part", 0xA3AF, None, 0x99BC), rmdu.Signal("Pioneer-2Part", 0xA3A3, None, 0x9E9E)]
    assert signal(upgrade("Pioneer DVD", "163 null null", ("Power", "C2"))) == "the device or the prefix command parameter is missing"
    assert signal(upgrade("Pioneer DVD", "null null null", ("Power", "C2"))) == "the device parameter is missing"
    assert signal(upgrade("Pioneer DVD", "null 175 153", ("Play", "86"))) == "the device parameter is missing"
    assert signal(upgrade("Pioneer DVD", "163 300 153", ("Power", "C2"))) == "a parameter is not a byte"
    assert signal(upgrade("Pioneer DVD", "300 175 153", ("Play", "86"))) == "a parameter is not a byte"
    assert signal(upgrade("Pioneer DVD", "163 175 153", ("Power", "C2 00"))) == "the function has no single OBC byte"


def test_the_dvd0571_upgrade_sends_the_codes_of_its_pioneer_mix_twin():
    """The same device has two upgrades, ``Pioneer Improved DVD0571 setup`` (``Pioneer DVD``, 53 keys) and its ``-2`` (``Pioneer MIX``,
    55 keys), which read the function through different translators (three one-bit ones, and a flag byte). **51 of the 53 codes of the
    first are codes of the second**, and 49 of the 50 keys they both name send the same one; the two the second lacks are ``KEY_REWIND``
    and ``KEY_FAST_FWD``, which are one-part codes there and not in the twin."""
    def codes(name: str) -> dict[str, tuple[str, str]]:
        doc = load(ROOT / jp.IMPORT_ROOT / "Pioneer" / name)
        return {key: (f["device"], f["function"]) for key, spec in doc["keys"].items() for f in spec["forms"]}

    dvd = codes("DVD__standard-Pioneer_Improved_DVD0571_setup.json")
    mix = codes("DVD__standard-Pioneer_Improved_DVD0571-2_setup.json")
    assert (len(dvd), len(mix)) == (53, 55)
    assert len(set(dvd.values()) & set(mix.values())) == 51
    both = [key for key in dvd if key in mix]
    assert (len(both), sum(dvd[key] == mix[key] for key in both)) == (50, 49)
    assert [key for key in dvd if dvd[key] not in set(mix.values())] == ["KEY_REWIND", "KEY_FAST_FWD"]


def test_pioneer_dvd2_is_the_same_with_a_second_byte_that_only_repeats_the_flag():
    """``Translator(lsb,comp,0,8,0) FieldCopier(comp,5,15,1)``: the second byte's low bit is the complement of the first's bit at
    offset 5, which is the OBC's ``0x20``, so it is 1 for two parts and 0 for one; the reader holds the file to it (37 of 37 of the
    DV-563AS's functions do). ``FA`` is OBC 160, ``86`` is 158."""
    fixed = "3a 66 0a ff"
    up = upgrade("Pioneer DVD2", "163 175 null", ("0", "FA 01"), ("Play", "86 00"), ("bad", "FA 00"), ("bad", "86 01"),
                 ("bad", "FA 03"), fixed=fixed, variant="2")
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("Pioneer-2Part", 0xA3AF, None, 0x99A0), rmdu.Signal("Pioneer-2Part", 0xA3A3, None, 0x9E9E),
        "the second byte is not the flag the executor copies from the first",
        "the second byte is not the flag the executor copies from the first",
        "the second byte is not the flag the executor copies from the first"]
    assert signal(upgrade("Pioneer DVD2", "163 175 null", ("a", "FA"), fixed=fixed, variant="2")) == "the function has no two OBC bytes"


def test_the_jp1_pioneer_dvd_upgrades_send_the_codes_the_ir_blaster_database_has():
    """An independent check, from another source: the corpus as it stood at the end of DESIGN §35, before the JP1 source held any
    Pioneer-2Part key, has the IR Blaster import's 1,673 distinct Pioneer-2Part codes (the committed files give the same counts), which hold **138 of the 147 keys of the four ``Pioneer DVD`` upgrades (93.9%) and
    all 37 of the ``Pioneer DVD2`` one**; split by the flag, **all 21 one-part keys and 154 of the 163 two-part**, so both readings
    of the flag are tested. The nine that are not there are functions a database seldom has (``Discrete On`` and ``Off``,
    ``Disc Skip``, ``Power On`` and ``Off`` of a Blu-ray, a TV's ``Sleep``)."""
    irblaster: set[tuple[int, int]] = set()
    for path in (ROOT / "remotes" / "irblaster").rglob("*.json"):
        doc = load(path)
        if doc.get("protocol", {}).get("name") == "Pioneer-2Part":
            for spec in doc["keys"].values():
                irblaster |= {(int(f["device"], 0), int(f["function"], 0)) for f in spec["forms"] if f.get("type") == "irp"}
    assert len(irblaster) == 1_673
    counts: Counter = Counter()
    for executor, _, device, _, function, _ in _committed_jp1_keys("Pioneer-2Part"):
        if executor not in ("Pioneer DVD", "Pioneer DVD2"):
            continue
        part = "two" if device >> 8 != device & 0xFF else "one"
        counts[executor] += 1
        counts[executor, "hit"] += (device, function) in irblaster
        counts[part] += 1
        counts[part, "hit"] += (device, function) in irblaster
    assert [counts["Pioneer DVD"], counts["Pioneer DVD", "hit"], counts["Pioneer DVD2"], counts["Pioneer DVD2", "hit"]] == [147, 138, 37, 37]
    assert [counts["one"], counts["one", "hit"], counts["two"], counts["two", "hit"]] == [21, 21, 163, 154]


def test_rc5_6_combo_is_rc5_or_rc6_by_the_low_bit_of_the_second_byte():
    """``Translator(0,1,15) Translator(1,5,3,comp) Translator(1,1,0,6) Translator(2,6,8,comp) Translator(2,1,1,6)
    Translator(3,2,13) Translator(4,8,0)``: the type is the second byte's low bit (0 is RC-5). RC-5: the device is the low
    five bits of the first byte complemented, the command the six bits of the second above its low two complemented, and
    the seventh the first byte's ``0x40`` as it is (``5F BC`` is device 0 and 16 + 64 = 80, Philips's Up; ``1F FC`` is 0 and
    0). RC-6: the first byte is the OBC and the second's bits 1 and 2 pick one of the three RC-6 devices, which are the
    parameters or the doubled bytes of FixedData (``56 66`` is 21)."""
    fixed = "95 30 56 55 56 66 56 66"
    up = upgrade("RC5/6 combo", "16 21 null", ("Up", "5F BC"), ("0", "1F FC"), ("6a", "10 F9"), ("6b", "10 FB"),
                 ("6c", "10 FD"), ("6d", "10 FF"), fixed=fixed, variant="2")
    assert [signal(up, i) for i in range(6)] == [
        rmdu.Signal("RC5", 0, None, 80), rmdu.Signal("RC5", 0, None, 0),
        rmdu.Signal("RC6", 16, None, 0x10), rmdu.Signal("RC6", 21, None, 0x10), rmdu.Signal("RC6", 21, None, 0x10),
        "the function selects no RC-6 device"]
    assert signal(upgrade("RC5/6 combo", "16 21 null", ("a", "3F BC"), variant="2")) == "the RC-5 device is not five bits"
    assert signal(upgrade("RC5/6 combo", "16 21 null", ("a", "1F BE"), variant="2")) == "the function sets a bit RC-5 does not use"
    assert signal(upgrade("RC5/6 combo", "null null null", ("a", "10 F9"), variant="2")) == "the RC-6 device is not set"
    assert signal(upgrade("RC5/6 combo", "16 21 null", ("a", "10 F9"))) == "the variant is not read"
    assert signal(upgrade("RC5/6 combo", "16 300 null", ("a", "10 FB"), variant="2")) == "a parameter is not a byte"


def rc5x_function(slot: int, sub6: int, obc: int) -> str:
    """The two bytes ``RC5_5xComboTranslator.in`` makes for an RC-5x function, written here from the translator and not from the
    reader (offsets from the most significant bit of the two bytes): the sub device's low six bits complemented at 0, the OBC
    complemented at 6, a zero at 12, the device slot at 13 and the type 1 at 15."""
    first = ((~sub6 & 0x3F) << 2) | ((~obc & 0x3F) >> 4)
    second = (((~obc & 0x3F) & 0xF) << 4) | (slot << 1) | 1
    return f"{first:02X} {second:02X}"


def test_rc5_5x_combo_is_rc5_or_rc5x_by_the_low_bit_of_the_second_byte_and_the_slots_flag_adds_64():
    """``RC5_5xComboTranslator``: the type is the second byte's low bit (0 is RC-5, read as the ``RC5/6 combo`` does). RC-5x:
    the sub device (six bits complemented, plus 64 where the slot's flag is set), the OBC (six bits complemented) and the slot
    (two bits) which names one of the four devices of the setup sheet; a slot's device and flag are the parameters or, where the
    device is ``null``, the byte of ``FixedData`` (the device its low five bits complemented, the flag its ``0x40``).
    ``Rc5FlagTranslator`` gives a slot with no device the opposite flag of the one before it, so a flag *parameter* of a
    ``null`` device is not the effective one and ``FixedData``'s is read."""
    up = upgrade("RC-5/5x Combo", "16 0 7 0 24 0 16 1", ("Power on", rc5x_function(0, 12, 1)), ("second", rc5x_function(1, 5, 63)),
                 ("flagged", rc5x_function(3, 20, 2)), ("rc5", "5F BC"), ("rc5 low", "1F FC"), fixed="0F 18 07 4F")
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("RC5x", 16, 12, 1), rmdu.Signal("RC5x", 7, 5, 63), rmdu.Signal("RC5x", 16, 20 + 64, 2),
        rmdu.Signal("RC5", 0, None, 80), rmdu.Signal("RC5", 0, None, 0)]
    # a device that is null is read from FixedData, and so is its flag: 1F 5F is device 0 with the flag clear, then set
    unset = upgrade("RC-5/5x Combo", "null 0 null 0 null 0 null 0", ("a", rc5x_function(0, 3, 4)), ("b", rc5x_function(1, 3, 4)),
                    fixed="1F 5F 1F 5F")
    assert [signal(unset, i) for i in range(2)] == [rmdu.Signal("RC5x", 0, 3, 4), rmdu.Signal("RC5x", 0, 3 + 64, 4)]
    # what the reader refuses
    assert signal(upgrade("RC-5/5x Combo", "16 0 7 0 24 0 16 1", ("a", "3F BC"), fixed="0F 18 07 4F")) == "the RC-5 device is not five bits"
    assert signal(upgrade("RC-5/5x Combo", "16 0 7 0 24 0 16 1", ("a", "1F BE"), fixed="0F 18 07 4F")) == "the function sets a bit RC-5 does not use"
    code = rc5x_function(0, 12, 1).split()
    assert signal(upgrade("RC-5/5x Combo", "16 0", ("a", f"{code[0]} {int(code[1], 16) | 8:02X}"))) == "the function sets a bit RC-5x does not use"
    assert signal(upgrade("RC-5/5x Combo", "null 0", ("a", rc5x_function(0, 12, 1)))) == "the RC-5x device the function selects is not set"
    assert signal(upgrade("RC-5/5x Combo", "16", ("a", rc5x_function(0, 12, 1)))) == "the RC-5x sub device flag the function selects is not set"
    assert signal(upgrade("RC-5/5x Combo", "40 0", ("a", rc5x_function(0, 12, 1)))) == "a parameter is out of range"


def test_the_jp1_rc5_5x_combo_upgrades_send_the_system_command_and_extension_marantzs_own_chart_lists():
    """An independent check of the RC-5x reading, from the maker's chart (D124, D227). The Marantz upgrades are nine of the sixteen,
    and **314 of the 334 RC-5x keys of system 16 are a (system, command, extension) row of Marantz's three charts** (94.0%).
    The sub device is the command and the OBC the extension; swapped, 24 match, and **without the slot's flag (the +64) 259
    match**, so the flag is what the check measures as well. The rest are keys the chart does not list (an upgrade's own
    additions) or whose row the chart has under another number."""
    chart = set()
    for stem in ("marantz-master-ir", "marantz-2014-ir-command-sheet", "marantz_fy18_av_sr_nr_ir_code_v02-02072018"):
        path = ROOT / "sources" / "official" / "marantz" / "csv" / f"{stem}__AVR_Commands.csv"
        for row in list(csv.reader(path.open(encoding="utf-8")))[6:]:
            if len(row) > 5 and row[3].strip().isdigit() and row[4].strip().isdigit() and row[5].strip().isdigit():
                chart.add((int(row[3]), int(row[4]), int(row[5])))
    files = keys = marantz = hit = swapped = no_flag = 0
    for path in sorted((ROOT / jp.IMPORT_ROOT).rglob("*.json")):
        doc = load(path)
        if doc["protocol"].get("name") != "RC5x":
            continue
        files += 1
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            device, sub, obc = (int(form[name], 0) for name in ("device", "subdevice", "function"))
            keys += 1
            assert device <= 31 and sub <= 127 and obc <= 63
            if "Marantz" in path.name and device == 16:
                marantz += 1
                hit += (device, sub, obc) in chart
                swapped += (device, obc, sub) in chart
                no_flag += (device, sub & 63, obc) in chart
    assert (files, keys) == (10, 472)
    assert (marantz, hit, swapped, no_flag) == (334, 314, 24, 259)


def test_the_jp1_rc5_6_combo_upgrades_send_the_codes_the_other_sources_have_and_philips_cursor_keys_by_name():
    """An independent check of the RC5/6 combo rules, from the ledger's other sources (IR Blaster, LIRC, SmartIR, hifi-remote
    and the manufacturers' tables: 2,482 distinct RC-5 codes and 1,237 RC-6). The twelve upgrades send **219 of their 228 RC-5
    keys and 257 of 273 RC-6 keys among them** (95.0%). The seventh command bit is the first byte's ``0x40``: 34 functions
    have it (a function above 63) and 26 of them are among the other sources' codes, which cannot tell it from the six bits
    alone (227 of 228 are among them without it); **what tells them apart is the names**: the Philips 47PFL7422D upgrade's
    Up, Down, Menu, Left, Right and OK send RC-5 device 0 and functions 80, 81, 82, 85, 86 and 87, which the other sources
    label KEY_UP, KEY_DOWN, KEY_MENU, KEY_LEFT, KEY_RIGHT and KEY_OK more often than anything else."""
    codes: dict[str, set[tuple[int, int]]] = {"RC5": set(), "RC6": set()}
    labels: dict[int, Counter] = {}
    for source in ("irblaster", "lirc", "smartir", "hifi-remote", "official", "authored"):
        base = ROOT / "remotes" / source
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            doc = load(path)
            protocol = doc.get("protocol", {}).get("name")
            if protocol not in codes:
                continue
            for key, spec in doc["keys"].items():
                for form in spec["forms"]:
                    if form.get("type") == "irp":
                        device, function = int(form["device"], 0), int(form["function"], 0)
                        codes[protocol].add((device, function))
                        if protocol == "RC5" and device == 0:
                            labels.setdefault(function, Counter())[key] += 1
    assert (len(codes["RC5"]), len(codes["RC6"])) == (2_482, 1_237)
    counts: Counter = Counter()
    cursor: dict[str, int] = {}
    for protocol in ("RC5", "RC6"):
        for executor, label, device, _, function, name in _committed_jp1_keys(protocol):
            if executor != "RC5/6 combo":
                continue
            counts[protocol] += 1
            counts[protocol, "hit"] += (device, function) in codes[protocol]
            if protocol == "RC5":
                counts["flagged"] += function > 63
                counts["flagged hit"] += function > 63 and (device, function) in codes["RC5"]
                counts["six bits hit"] += (device, function & 63) in codes["RC5"]
                if "Nevo" in name and device == 0 and label in ("Up", "Down", "Menu", "Left", "Right", "OK"):
                    cursor[label] = function
    assert [counts["RC5"], counts["RC5", "hit"], counts["RC6"], counts["RC6", "hit"]] == [228, 219, 273, 257]
    assert [counts["flagged"], counts["flagged hit"], counts["six bits hit"]] == [34, 26, 227]
    assert cursor == {"Up": 80, "Down": 81, "Menu": 82, "Left": 85, "Right": 86, "OK": 87}
    for key, function in zip(("KEY_UP", "KEY_DOWN", "KEY_MENU", "KEY_LEFT", "KEY_RIGHT", "KEY_OK"), (80, 81, 82, 85, 86, 87)):
        assert labels[function].most_common(1)[0][0] == key, function


def test_xmp_is_xmp_1_with_the_parameters_or_fixed_data_and_the_obc_as_it_is_and_refuses_what_a_form_cannot_carry():
    """``Device, Subdevice, OEM Code, Final Frame Toggle`` and ``XMP Type`` are the parameters, or where ``null`` what
    ``FixedData`` keeps (``17 0F 44 3E 1F 00 40`` is device 62, sub device 16, OEM 68, no final frame, XMP-1; ``C0`` in its
    last byte is a final frame, which makes it ``XMPff-1``). The function is the OBC byte as it is (variant 2 and
    ``XMP (UEI)`` have two, the second the low byte of XMP-2's command, which must be 0). The OEM must be 68 and the type 0."""
    fixed = "17 0F 44 3E 1F 00 40"
    up = upgrade("XMP", "62 16 null 0 0", ("1", "01"), ("power", "0C"), fixed=fixed, variant="JP1")
    assert [signal(up, i) for i in range(2)] == [rmdu.Signal("XMP-1", 62, 16, 1), rmdu.Signal("XMP-1", 62, 16, 12)]
    nulls = upgrade("XMP (Slingbox)", "null null null null null", ("1", "01"), fixed=fixed, variant="JP1Slingbox")
    assert signal(nulls) == rmdu.Signal("XMP-1", 62, 16, 1)
    assert signal(upgrade("XMP (JP1)", "null null null null null", ("1", "01"), fixed="0E 0F 44 1A 00 00 40", variant="JP1")) == \
        rmdu.Signal("XMP-1", 26, 0, 1)
    assert signal(upgrade("XMP", "null null null null null", ("1", "01"), fixed="1C 1F 44 47 1E 01 40", variant="JP1")) == \
        rmdu.Signal("XMP-1", 71, 17, 1)
    assert signal(upgrade("XMP", "62 16 null 1 0", ("1", "01"), fixed=fixed, variant="JP1")) == rmdu.Signal("XMPff-1", 62, 16, 1)
    assert signal(upgrade("XMP", "null null null null null", ("1", "01"), fixed="17 0F 44 3E 1F 00 C0", variant="JP1")) == \
        rmdu.Signal("XMPff-1", 62, 16, 1)
    assert signal(upgrade("XMP", "62 16 69 0 0", ("1", "01"), fixed=fixed, variant="JP1")) == "the OEM code is not 68, which the ledger's XMP-1 fixes"
    assert signal(upgrade("XMP", "62 16 null 0 1", ("1", "01"), fixed=fixed, variant="JP1")) == "the type is XMP-2, which the ledger does not register"
    assert signal(upgrade("XMP", "null null null null null", ("1", "01"), fixed="17 0F 44 3E 1F 00 00", variant="JP1")) == \
        "the type is XMP-2, which the ledger does not register"
    two = upgrade("XMP", "62 16 null 0", ("9", "09 00"), ("x", "09 01"), ("y", "09"), fixed="17 0F 44 3E", variant="2")
    assert [signal(two, i) for i in range(3)] == [rmdu.Signal("XMP-1", 62, 16, 9), "the second OBC byte is not 0, which XMP-1 has no place for",
                                                  "the function has no OBC bytes of the executor's shape"]
    assert signal(upgrade("XMP", "26 48 68 1", ("a", "27 00"), fixed="3B 07 44 1A", variant="2")) == rmdu.Signal("XMPff-1", 26, 48, 0x27)
    assert signal(upgrade("XMP", "null null null null", ("a", "27 00"), fixed="3B 07 44 1A", variant="2")) == \
        rmdu.Signal("XMPff-1", 26, 48, 0x27)
    assert signal(upgrade("XMP (UEI)", "null null null 0", ("a", "27 00"), fixed="0E 0F 44 1A")) == rmdu.Signal("XMP-1", 26, 0, 0x27)
    assert signal(upgrade("XMP", "62 16 null 0 0", ("1", "01"), fixed=fixed, variant="Q")) == "the variant is not read"
    assert signal(upgrade("XMP", "null null null null null", ("1", "01"), fixed="17 0F", variant="JP1")) == "FixedData is too short for the null parameters"


def test_the_jp1_xmp_upgrades_send_the_codes_the_lirc_remotes_of_xmp_boxes_send_under_the_same_names():
    """An independent check of the XMP rule (the function is the OBC as it is, the device and sub device the parameters or
    ``FixedData``), from another project. The LIRC remotes of ten boxes (166 sequences: tests/test_xmp.py's layout decoder)
    hold 24 codes of device 62 and sub device 16, 11 of 49 and 16, 13 of 42 and 17, 3 of 71 and 17 and 21 of 72 and 18 (no
    upgrade has the last). **The upgrades send 24 of 24, 11 of 11, 12 of 13 and 3 of 3**: 50 of 51 codes, and name them as
    LIRC does: the ten digits, the volume, channel, mute, power, cursor and OK keys, last, language and info are the same
    functions of device 62 and sub device 16 in the upgrades' own spellings."""
    from test_xmp import lirc_frames

    lirc: dict[tuple[int, int], set[int]] = {}
    for _, _, (device, subdevice, function, _, _) in lirc_frames():
        lirc.setdefault((device, subdevice), set()).add(function)
    sent: dict[tuple[int, int], dict[int, set[str]]] = {}
    for executor, label, device, subdevice, function, _ in _committed_jp1_keys("XMP-1"):
        sent.setdefault((device, subdevice), {}).setdefault(function, set()).add(label.strip().lower())
    got = {k: (len(lirc[k]), len([f for f in lirc[k] if f in sent.get(k, {})])) for k in lirc}
    assert got == {(62, 16): (24, 24), (49, 16): (11, 11), (42, 17): (13, 12), (71, 17): (3, 3), (72, 18): (21, 0)}
    spellings = {10: ("vol up", "vol+", "volume up"), 11: ("vol down", "vol-", "volume down"), 12: ("mute", "volume mute"),
                 13: ("ch+", "channel +", "channel up", "ch up key"), 14: ("ch-", "channel -", "channel down", "ch down key"),
                 15: ("power", "power key", "power toggle"), 33: ("up", "up arrow", "curser up"),
                 34: ("down", "down arrow", "curser down"), 35: ("left", "left arrow", "curser left"),
                 36: ("right", "right arrow", "curser right"), 37: ("ok", "ok key", "enter", "select"),
                 38: ("info", "info key", "display"), 81: ("last", "last key", "last (prev ch)"), 130: ("lang", "language")}
    spellings.update({n: (str(n), f"num_{n}", f"numeric {n}") for n in range(10)})
    assert len(spellings) == 24 and set(spellings) == lirc[(62, 16)]
    for function, names in spellings.items():
        assert sent[(62, 16)][function] & set(names), function


def test_the_jp1_xmp_upgrades_with_the_final_frame_send_the_keys_of_the_remote_irptransmogrifier_captured_held():
    """An independent check of ``XMPff-1`` (the 16 upgrades whose ``Final Frame Toggle`` is on: 623 keys), from two sources.
    IrpTransmogrifier's capture of held keys of a remote at device 26 and sub device 48 (``XMP_1923_FinalFrame``) has the
    digits 0 to 7 as the functions 0 to 7 and up, down, left, right and OK as 33 to 37. **The Dreambox DM7020 upgrade, the one
    at that device and sub device, names those 13 keys and sends exactly those functions: 13 of 13.** And the LIRC remotes of
    XMP boxes (tests/test_xmp.py) hold 24 codes of device 62 and sub device 16 and 3 of 71 and 17, of which the final-frame
    upgrades send 21 and 3."""
    from test_xmp import lirc_frames

    rows = list(_committed_jp1_keys("XMPff-1"))
    assert len(rows) == 623 and len({name for *_, name in rows}) == 16
    assert {executor for executor, *_ in rows} == {"XMP", "XMP (Slingbox)", "XMP (Slingbox)(No repeat)"}
    captured = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "up": 33, "down": 34, "left": 35,
                "right": 36, "ok": 37}
    seen: dict[str, int] = {}
    for _, label, device, subdevice, function, name in rows:
        if (device, subdevice) != (26, 48):
            continue
        key = re.sub(r"(?i)^(num_|numeric |curser |cursor |arrow )|[ _]?arrow$", "", label.strip()).lower()
        key = {"select": "ok", "enter": "ok"}.get(key, key)
        if key in captured:
            seen[key] = function
    assert seen == captured
    lirc: dict[tuple[int, int], set[int]] = {}
    for _, _, (device, subdevice, function, _, _) in lirc_frames():
        lirc.setdefault((device, subdevice), set()).add(function)
    sent: dict[tuple[int, int], set[int]] = {}
    for _, _, device, subdevice, function, _ in rows:
        sent.setdefault((device, subdevice), set()).add(function)
    assert len(lirc[(62, 16)] & sent[(62, 16)]) == 21 and len(lirc[(71, 17)] & sent[(71, 17)]) == 3


def test_an_executor_not_read_and_a_function_with_no_single_byte_are_said_so():
    assert "not read by this import" in signal(upgrade("DirecTV", "28 0 null", ("power", "C4")))
    assert signal(upgrade("NEC1", "6 null null", ("Power", "FF BF"))) == "the function has no single OBC byte"
    assert signal(upgrade("NEC1", "6 null null", ("Guide", None))) == "the function has no single OBC byte"


def test_the_decoded_samsung_agrees_with_the_authored_bn59_and_the_sony_with_the_authored_sony():
    """The import is checked against the ledger's own authored data, not only against a rule."""
    bn59 = load_remote(ROOT / "remotes" / "samsung" / "BN59-01199F.json")
    power = next(f for f in bn59.keys["KEY_POWER"])
    assert (power.device, power.subdevice, power.function) == (7, 7, 0x02)
    b118 = load_remote(ROOT / "remotes" / "sony" / "RMT-B118P.json")
    play = next(f for f in b118.keys["KEY_PLAY"])
    assert (play.device, play.subdevice, play.function) == (26, 226, 0x1A)
    assert signal(upgrade("Sony20", "26 226 null", ("Play", "58"))) == rmdu.Signal("Sony20", 26, 226, 0x1A)


# --- the names ----------------------------------------------------------------------------------


def test_names_collide_into_function_numbers_and_unnamed_functions_have_one_too():
    names = jp.key_names([(0, "Power"), (1, "vol +"), (2, "vol -"), (3, "Up"), (4, "UP"), (5, None), (6, "??")])
    assert names[0] == "KEY_POWER" and names[1] == "KEY_VOL_PLUS" and names[2] == "KEY_VOL_MINUS"
    assert names[3] == "KEY_UP_3" and names[4] == "KEY_UP_4"
    assert names[5] == "KEY_FUNCTION_5" and names[6] == "KEY_UNLABELED"


@pytest.mark.parametrize("brand,description,model", [
    ("Maximum", "Maximum XO-506C", "XO-506C"), ("Daewoo", "Daewoo AC", "AC"), ("Sony", "Sony - kdl46", "kdl46"),
    ("Samsung", "TV SAMSUNG - UN40H5203AFXZC", "TV SAMSUNG - UN40H5203AFXZC"), ("Acme", "Acme", "Acme")])
def test_the_model_is_the_description_without_the_brand_it_begins_with(brand, description, model):
    assert jp.split_model(brand, description) == model


# --- the master index and a small checkout, imported ------------------------------------------------

ROWS = [
    ("TV", "SONY", "Sony TV", "Sony TV.rmdu", "rmdu", "00 CA", "Sony 12/15"),
    ("TV", "Sony", "Sony Bravia", "Sony Bravia.rmdu", "rmdu", "00 CA", "Sony 12/15"),
    ("Audio", "Sony", "Sony Amp", "Sony_Amp.rmdu", "rmdu", "00 DE", "Sony20"),
    ("TV", "Samsung", "Samsung UN40", "Samsung UN40.rmdu", "rmdu", "00 5A", "NECx2"),
    ("DVD", "Samsung", "Samsung UN40", "Samsung UN40.rmdu", "rmdu", "00 5A", "NECx2"),
    ("Misc", "Acme", "Acme Box", "Acme Box.rmdu", "rmdu", "00 5A", "NEC1"),
]
FILES = {
    "TV/Sony TV.rmdu": ("Description=Sony TV\r\nProtocol.name=Sony 12/15\r\nProtocolParms=1 0 151 0\r\n"
                        "Function.0.name=1\r\nFunction.0.hex=00\r\nFunction.1.name=2\r\nFunction.1.hex=81\r\n"),
    "Audio/Sony Amp.rmdu": ("Description=Sony Amp\r\nProtocol.name=Sony20\r\nProtocolParms=26 12 null\r\n"
                            "Function.0.name=Power\r\nFunction.0.hex=A8\r\nFunction.0.notes=toggles\r\n"),
    "TV/Samsung UN40.rmdu": ("Description=Samsung UN40\r\nProtocol.name=NECx2\r\nProtocolParms=7 7 null\r\n"
                             "Function.0.name=POWER\r\nFunction.0.hex=BF\r\nFunction.1.name=Guide\r\n"),
    "DVD/Samsung UN40.rmdu": ("Description=Samsung UN40 DVD\r\nProtocol.name=NECx2\r\nProtocolParms=7 7 null\r\n"
                              "Function.0.name=POWER\r\nFunction.0.hex=BF\r\n"),
    "Misc/Acme Box.rmdu": ("Description=Learned Signal Upgrade\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n"
                           "Function.0.name=Power\r\nFunction.0.hex=AF\r\n"),
    "Misc/Nobody Box.rmdu": ("Description=Nobody Box\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n"
                             "Function.0.name=Power\r\nFunction.0.hex=AF\r\n"),
    "Audio/Mce Box.rmdu": ("Description=Acme MCE\r\nProtocol.name=DirecTV\r\nProtocolParms=10 0\r\n"
                           "Function.0.name=power\r\nFunction.0.hex=CC\r\n"),
    "TV/Keymap Master.txt": "not read\r\n",
    "Audio/Empty.rmdu": "Description=Acme Empty\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n",
}


def make_checkout(tmp_path: Path, commit: str = COMMIT, files: dict[str, str] | None = None):
    repo = tmp_path / "repo"
    for rel, text in (files or FILES).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    (repo / jp.INPUT).write_bytes(b"workbook")
    root = tmp_path / "ledger"
    master = root / jp.MASTER_DIR
    master.mkdir(parents=True)
    with (master / jp.MASTER_CSV).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Category", "Brand", "File Description (from web)", "File Name", "Type", "PID", "Protocol"])
        writer.writerows(ROWS)
    (master / jp.MASTER_META).write_text(json.dumps(
        {"commit": commit, "xlsSha256": hashlib.sha256(b"workbook").hexdigest()}), encoding="utf-8")
    (root / "remotes").mkdir()
    return root, repo


@pytest.fixture()
def small(tmp_path):
    root, repo = make_checkout(tmp_path)
    return jp.import_tree(root, repo, COMMIT, {})


def test_a_checkout_becomes_one_file_per_protocol_and_every_function_a_key(small):
    docs, report = small
    assert sorted(docs) == [
        "remotes/jp1/Acme/Misc-Acme_Box.json", "remotes/jp1/Samsung/DVD-Samsung_UN40.json",
        "remotes/jp1/Samsung/TV-Samsung_UN40.json", "remotes/jp1/Sony/Audio-Sony_Amp.json",
        "remotes/jp1/Sony/TV-Sony_TV-sony12.json", "remotes/jp1/Sony/TV-Sony_TV-sony15.json"]
    sony12 = docs["remotes/jp1/Sony/TV-Sony_TV-sony12.json"]
    assert sony12["model"] == "TV [Sony12]" and sony12["manufacturer"] == "Sony"
    assert sony12["protocol"] == {"name": "Sony12", "carrierHz": 40000, "minSends": 3}
    assert docs["remotes/jp1/Sony/TV-Sony_TV-sony15.json"]["keys"]["KEY_2"]["forms"][0]["function"] == 1
    samsung = docs["remotes/jp1/Samsung/TV-Samsung_UN40.json"]
    assert samsung["protocol"]["name"] == "NECx2" and samsung["protocol"]["minSends"] == 1
    assert set(samsung["keys"]) == {"KEY_POWER"}      # `Guide` has no OBC byte
    assert (report.files["upgrades"], report.remotes["imported"]) == (8, 6)
    assert report.unread == {"DirecTV": 1} and report.files["not read: KeymapMaster .txt"] == 1
    assert dict(report.protocols) == {"Sony12": 1, "Sony15": 1, "Sony20": 1, "NECx2": 2, "NEC1": 1}


def test_the_master_index_gives_brand_and_category_and_a_prefix_gives_the_rest(small):
    docs, report = small
    # `SONY` and `Sony` are one brand, spelled as the index mostly spells it
    assert {Path(p).parts[2] for p in docs} == {"Acme", "Samsung", "Sony"}
    assert docs["remotes/jp1/Acme/Misc-Acme_Box.json"]["model"] == "Box"      # the generic description is not used
    assert docs["remotes/jp1/Acme/Misc-Acme_Box.json"]["controls"] == ["Acme Box"]
    # two rows share a file name: the category the repository files it under chooses
    assert docs["remotes/jp1/Samsung/DVD-Samsung_UN40.json"]["controls"] == ["Samsung UN40 DVD"]
    # `Nobody Box` has neither a row nor a brand, `Empty` no function
    assert report.files["skipped: no brand"] == 1 and report.files["skipped: no functions"] == 1
    assert dict(report.brands_from) == {"the master index": 5}


def test_a_citation_names_the_commit_the_file_the_function_the_obc_and_the_reading(small):
    docs, _ = small
    form = docs["remotes/jp1/Sony/Audio-Sony_Amp.json"]["keys"]["KEY_POWER"]["forms"][0]
    assert form["source"] == (
        "jp1-device-upgrades@87b0ac0 Audio/Sony Amp.rmdu function 0 'Power' (OBC A8; Sony20, parms 26 12 null): "
        "Sony20 device 26 subdevice 12 function 21; the upgrade's note: 'toggles'")
    assert form["confidence"] == "plausible" and form["type"] == "irp"
    assert (form["device"], form["subdevice"], form["function"]) == (26, 12, 21)


def test_everything_it_writes_loads_compiles_and_a_second_run_changes_nothing(tmp_path):
    root, repo = make_checkout(tmp_path)
    report = jp.write_import(root, repo, COMMIT)
    assert report.remotes["imported"] == 6
    for path in (root / jp.IMPORT_ROOT).rglob("*.json"):
        remote = load_remote(path)
        for key in remote.keys:
            remote.compile_group(key, "primary")
    before = {p: p.read_bytes() for p in (root / jp.IMPORT_ROOT).rglob("*") if p.is_file()}
    jp.write_import(root, repo, COMMIT)
    assert before == {p: p.read_bytes() for p in (root / jp.IMPORT_ROOT).rglob("*") if p.is_file()}
    assert (root / jp.IMPORT_ROOT / jp.REPORT).read_text().startswith("# JP1 device upgrades import report")


def test_an_authored_remote_of_the_same_name_wins(tmp_path):
    root, repo = make_checkout(tmp_path)
    docs, report = jp.import_tree(root, repo, COMMIT, {("acme", "box"): "remotes/acme/box.json"})
    assert "remotes/jp1/Acme/Misc-Acme_Box.json" not in docs
    assert report.collisions == [("Misc/Acme Box.rmdu", "remotes/jp1/Acme/Misc-Acme_Box.json")]


def test_two_files_whose_names_slug_alike_are_numbered_in_path_order(tmp_path):
    files = {"TV/Sony TV.rmdu": FILES["TV/Sony TV.rmdu"], "TV/Sony_TV.rmdu": FILES["TV/Sony TV.rmdu"]}
    root, repo = make_checkout(tmp_path, files=files)
    docs, _ = jp.import_tree(root, repo, COMMIT, {})
    assert {p for p in docs if "sony12" in p} == {"remotes/jp1/Sony/TV-Sony_TV-sony12.json",
                                                   "remotes/jp1/Sony/TV-Sony_TV-sony12-2.json"}
    assert len({d["model"] for d in docs.values()}) == len(docs)


def test_the_master_index_must_be_the_one_of_this_commit_and_workbook(tmp_path):
    root, repo = make_checkout(tmp_path)
    with pytest.raises(ValidationError, match="was made from commit"):
        jp.load_master(root, repo, "1234567" + "0" * 33)
    (repo / jp.INPUT).write_bytes(b"another workbook")
    with pytest.raises(ValidationError, match="not the workbook"):
        jp.load_master(root, repo, COMMIT)
    with pytest.raises(ValidationError, match="cannot read the master index"):
        jp.load_master(tmp_path / "nowhere", repo, COMMIT)


def test_brand_spellings_and_the_order_of_brands_do_not_depend_on_the_process(tmp_path):
    root, repo = make_checkout(tmp_path)
    master = jp.load_master(root, repo, COMMIT)
    assert master.brand("sony") == "Sony" and master.brand("SONY") == "Sony" and master.brand("Unknown") == "Unknown"
    assert master.brands == sorted(master.brands, key=lambda b: (-len(b), b))
    assert jp.brand_by_prefix(master, "sony tv model") == "Sony" and jp.brand_by_prefix(master, "sonyx") is None


# --- the committed tree ---------------------------------------------------------------------------


def test_the_committed_jp1_import_keeps_r19():
    """SPEC R19 conditions 2 and 3 over every form: README, report and the index beside it, one
    Plausible `irp` form a key, a citation of the fixed shape that names the commit the index was made
    from, and a reading that is the form's own. A cheap scan of the JSON, not a load."""
    meta = json.loads((ROOT / jp.MASTER_DIR / jp.MASTER_META).read_text(encoding="utf-8"))
    assert (ROOT / jp.IMPORT_ROOT / "README.md").is_file() and (ROOT / jp.IMPORT_ROOT / jp.REPORT).is_file()
    assert f"@ `{meta['commit']}`" in (ROOT / jp.IMPORT_ROOT / jp.REPORT).read_text(encoding="utf-8")
    shape = re.compile(
        rf"^jp1-device-upgrades@{meta['commit'][:7]} \S.* function (\d+) '.*' \(OBC ((?:[0-9A-F]{{2}})(?: [0-9A-F]{{2}})*); [^;]+, parms [^)]*\): "
        r"(NEC1|NEC2|NECx1|NECx2|Sony12|Sony15|Sony20|RC5|RC6|JVC|Aiwa|Denon|Sharp|Panasonic|Samsung36|Proton|F12_relaxed|RECS80|Denon-K|Pioneer-2Part|RCA-38|RECS80-0068|JVC-48|Teac-K|NEC1-f16|NEC2-f16|MCE|Nokia32|Panasonic_Old|RCA|TDC-38|TDC-56|GI_Cable|RC6-6-20|XMP-1|XMPff-1|RC5x) device (\d+)(?: subdevice (\d+))? function (\d+)", re.S)

    def num(value):
        return int(value, 0) if isinstance(value, str) else value

    files = sorted((ROOT / jp.IMPORT_ROOT).rglob("*.json"))
    assert len(files) > 1_500
    bad = []
    for path in files:
        doc = load(path)
        for key, spec in doc["keys"].items():
            forms = spec["forms"]
            if len(forms) != 1:
                bad.append(f"{path.relative_to(ROOT)}:{key}")
            for form in forms:
                m = shape.match(form.get("source", ""))
                ok = (form["type"] == "irp" and form["confidence"] == "plausible" and "verifiedBy" not in form
                      and m is not None and m[3] == doc["protocol"]["name"]
                      and (int(m[4]), None if m[5] is None else int(m[5]), int(m[6]))
                      == (num(form["device"]), None if "subdevice" not in form else num(form["subdevice"]),
                          num(form["function"])))
                if not ok:
                    bad.append(f"{path.relative_to(ROOT)}:{key}")
    assert bad == []


def test_the_committed_samsung_upgrade_agrees_with_the_authored_bn59_on_every_key_they_share():
    """Two independent sources for one TV family: a forum member's JP1 upgrade for the UN40H5203AFXZC
    and the ledger's authored BN59-01199F. Where both name a key, they send the same signal."""
    path = next((ROOT / jp.IMPORT_ROOT / "Samsung").glob("TV-TV_SAMSUNG_-_UN40H5203AFXZC*.json"))
    upgrade_keys = load_remote(path).keys
    authored = load_remote(ROOT / "remotes" / "samsung" / "BN59-01199F.json").keys
    shared = set(upgrade_keys) & set(authored)
    assert {"KEY_POWER"} <= shared
    for key in shared:
        a, b = authored[key][0], upgrade_keys[key][0]
        assert (a.device, a.subdevice, a.function) == (b.device, b.subdevice, b.function), key


def test_the_committed_arcam_upgrades_send_the_codes_arcam_publishes():
    """An independent check of the RC-5 rule, from another source. Arcam's own table of its amplifiers'
    IR codes (arcam.co.uk/ugc/tor/a18/IR Codes/amps_rc.pdf, system code 16) lists PHONO select 16-1, AV select
    16-2, Tuner select 16-3, tape select 16-5, CD select 16-7, Mute 16-13, Volume up 16-16, Volume down 16-17,
    Power-on 16-123 and Power-off 16-124. Two forum upgrades of Arcam equipment, decoded through the RC-5
    executor's translator, send exactly those (the AVR100's `Power OFF` and `Power ON` through the OBC>63
    flag of its second device slot)."""
    def codes(name: str) -> dict[str, tuple[int, int]]:
        doc = load(ROOT / jp.IMPORT_ROOT / "Arcam" / name)
        assert doc["protocol"]["name"] == "RC5"
        return {key: (int(spec["forms"][0]["device"], 16), int(spec["forms"][0]["function"], 16))
                for key, spec in doc["keys"].items()}

    amp = codes("Audio-Arcam_Amp-Tuner-CD.json")
    assert {k: amp[k] for k in ("KEY_PHONO", "KEY_AV", "KEY_TUNER", "KEY_TAPE", "KEY_CD", "KEY_MUTE", "KEY_VOL_PLUS", "KEY_VOL_MINUS")} == {
        "KEY_PHONO": (16, 1), "KEY_AV": (16, 2), "KEY_TUNER": (16, 3), "KEY_TAPE": (16, 5), "KEY_CD": (16, 7),
        "KEY_MUTE": (16, 13), "KEY_VOL_PLUS": (16, 16), "KEY_VOL_MINUS": (16, 17)}
    receiver = codes("Audio-Arcam_Receiver_AVR100.json")
    assert (receiver["KEY_POWER_ON"], receiver["KEY_POWER_OFF"]) == ((16, 123), (16, 124))


def test_a_sony_combo_upgrade_sends_every_verified_key_of_the_authored_rmt_b118p():
    """An independent check of the Sony Combo rule's hardest part, the index of the four sub devices. The ledger's
    authored RMT-B118P has 38 keys Verified against hifi-remote's own Sony Blu-ray table (Sony20, device 26, sub
    device 226). The forum's `Sony UHP-H1` upgrade is a Sony Combo (12/15/20) whose functions choose their sub
    device through the index in the second byte, and the signals it decodes to include all 38."""
    def triples(path: Path) -> set[tuple[int, int, int]]:
        doc = load(path)
        return {(int(f["device"], 0), int(f["subdevice"], 0), int(f["function"], 0))
                for spec in doc["keys"].values() for f in spec["forms"] if "subdevice" in f}

    authored = triples(ROOT / "remotes" / "sony" / "RMT-B118P.json")
    assert len(authored) == 38 and {(d, s) for d, s, _ in authored} == {(26, 226)}
    files = sorted((ROOT / jp.IMPORT_ROOT / "Sony").glob("DVD__Blu-Ray-Sony_UHP-H1*.json"))
    assert files and all(load(f)["keys"] for f in files)
    sent = set().union(*(triples(f) for f in files))
    assert authored <= sent


def test_a_jp1_samsung36_key_compiles_to_the_signal_the_ir_blaster_import_has_for_the_same_code():
    """The same code is one signal whichever source it came from. The forum's Samsung HW-F355 upgrade and the IR
    Blaster database's remote 1952 both send Samsung36 `POWER` as device 0x30, sub device 0x0F, function 0x70; the
    files compile to the same Pronto string only because the JP1 file carries the IR Blaster one's 500 us unit."""
    mine = load_remote(ROOT / jp.IMPORT_ROOT / "Samsung" / "Audio-Samsung_HW-F355.json")
    theirs = load_remote(next((ROOT / "remotes" / "irblaster").glob("*/1952-Samsung36.json")))
    assert mine.keys["KEY_POWER"][0].function == theirs.keys["KEY_POWER"][0].function == 0x70
    assert mine.compile_group("KEY_POWER", "primary") == theirs.compile_group("KEY_POWER", "primary")


def test_two_committed_pioneer_upgrades_send_the_signal_irptransmogrifier_decoded_from_a_receiver():
    """An independent check of the Pioneer MIX rule. IrpTransmogrifier's own teaser capture of a Pioneer receiver
    (PioneerMix2.ict, decoded as D0=170, F0=91, D=175, F=36) is the ledger's golden vector for Pioneer-2Part
    (tests/vectors/irpt_pioneer2part_D170_F91_D175_F36.pronto, CITATIONS.md). Two forum upgrades of Pioneer plasma
    TVs, each with a function whose OBC and flag byte the rule decodes to exactly that, name it a TV guide key."""
    expected = (0xAAAF, 0x5B24)               # device = D0 * 256 + D, function = F0 * 256 + F
    assert (170 * 256 + 175, 91 * 256 + 36) == expected
    for name, key in (("TV-Pioneer_PDP-5080HD.json", "KEY_TV_GUIDE"),
                      ("TV-Pioneer_PDP-SX4280D__UK_version__-_as_modded_Oct_14.json", "KEY_EPG")):
        doc = load(ROOT / jp.IMPORT_ROOT / "Pioneer" / name)
        assert doc["protocol"]["name"] == "Pioneer-2Part"
        form = doc["keys"][key]["forms"][0]
        assert (int(form["device"], 16), int(form["function"], 16)) == expected, (name, key)


def test_the_jp1_mce_upgrades_send_the_functions_the_lirc_remotes_of_four_mce_devices_send():
    """An independent check of the MCE rule, from another project. The LIRC remotes of four MCE devices (225 keys,
    decoded by tests/test_mce.py's layout decoder at the unit each has) are device 4, 20, 36 and 116 with second OEM
    byte 15. The forum's upgrades with the same device and second OEM byte, read through the MCE executor, send
    every function of the Xbox 360, Vista and Hauppauge remotes (devices 116, 20 and 36: 131 keys) and 69 of the 94
    of the mceusb remote (device 4), whose upgrades differ in what they keep. The functions are the upgrades' OBC
    bytes as they are."""
    from test_mce import decode, lirc_frames

    lirc: dict[tuple[int, int], set[int]] = {}
    for _, _, durations in lirc_frames():
        mode, trailer, oem1, s, toggle, d, f = decode(durations, unit=durations[0] / 6)
        assert (mode, trailer, oem1) == (6, 0, 128)
        lirc.setdefault((d, s), set()).add(f)
    assert {k: len(v) for k, v in lirc.items()} == {(4, 15): 94, (20, 15): 43, (36, 15): 43, (116, 15): 45}
    sent: dict[tuple[int, int], set[int]] = {}
    for path in (ROOT / jp.IMPORT_ROOT).rglob("*.json"):
        doc = load(path)
        if doc.get("protocol", {}).get("name") != "MCE":
            continue
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                sent.setdefault((int(form["device"], 0), int(form["subdevice"], 0)), set()).add(int(form["function"], 0))
    for key in ((20, 15), (36, 15), (116, 15)):
        assert lirc[key] <= sent[key], key
    assert len(lirc[(4, 15)] & sent[(4, 15)]) == 69


def test_the_jp1_nokia32_upgrades_send_the_functions_the_lirc_remotes_of_four_boxes_send():
    """An independent check of the Nokia32 rule, from another project. The LIRC remotes of a Foxtel box (device 33,
    sub device 160), a Motorola VIP (35, 64), a Pace box (35, 128) and a Galaxis one (13, 80) are decoded by
    tests/test_nokia32.py's layout decoder (154 keys). The forum's upgrades for the same device and sub device, read
    through the Nokia32 executor, send 29 of the Foxtel remote's 30 (device, sub device, X and F), 40 of 41 and 40 of 40:
    109 of 111. The Galaxis remote's X is 8 where the upgrades for device 13 have 45, another box, and 30 of its 43
    functions are among theirs when X is left out."""
    from test_nokia32 import LIRC_NOKIA32, decode_lirc
    from remote_ledger.remote import load_remote

    lirc: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for name in LIRC_NOKIA32:
        for key, forms in load_remote(ROOT / "remotes" / name).keys.items():
            form = forms[0]
            got = decode_lirc(list(form.repeat or form.intro or ())) if form.type == "raw" else None
            if got:
                lirc.setdefault((got[0], got[1]), set()).add((got[3], got[4]))
    sent: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for path in (ROOT / jp.IMPORT_ROOT).rglob("*.json"):
        doc = load(path)
        if doc.get("protocol", {}).get("name") != "Nokia32":
            continue
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                function = int(form["function"], 0)
                sent.setdefault((int(form["device"], 0), int(form["subdevice"], 0)), set()).add((function >> 8, function & 0xFF))
    assert {k: len(v) for k, v in lirc.items()} == {(33, 160): 30, (35, 64): 41, (35, 128): 40, (13, 80): 43}
    assert [len(lirc[k] & sent[k]) for k in ((33, 160), (35, 64), (35, 128))] == [29, 40, 40]
    assert not lirc[(13, 80)] & sent[(13, 80)]
    assert len({f for _, f in lirc[(13, 80)]} & {f for _, f in sent[(13, 80)]}) == 30


def test_the_jp1_panasonic_old_upgrades_send_the_cable_boxs_digits_irptransmogrifier_decoded():
    """An independent check of the Panasonic (old) rule. IrpTransmogrifier's test data holds sixteen Pronto strings of
    a cable box's remote, decoded as Panasonic_Old D=27 with the keys 1 to 9 and 0 as F = 16 to 24 and 25. Seventeen of
    the nineteen forum upgrades name their ten digit keys, and every one of them, the Technics audio ones (device 9 and
    12) included, sends exactly those functions."""
    digits = [f"KEY_{i}" for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)]
    wanted = [16, 17, 18, 19, 20, 21, 22, 23, 24, 25]
    named = 0
    for path in (ROOT / jp.IMPORT_ROOT).rglob("*.json"):
        doc = load(path)
        if doc.get("protocol", {}).get("name") != "Panasonic_Old" or not set(digits) <= set(doc["keys"]):
            continue
        assert [int(doc["keys"][k]["forms"][0]["function"], 0) for k in digits] == wanted, path.name
        named += 1
    assert named == 17
