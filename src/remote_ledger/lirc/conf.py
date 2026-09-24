"""Parse ``lircd.conf`` exactly as lircd 0.10.2 does.

A port of ``lib/config_file.c`` (lirc 0.10.2 release tarball, sha256
``3d44ec82...d222a``), restricted to what can change a transmitted waveform:
every keyword, lircd's numeric parsing, its line reader, its code lists, raw
code blocks, the ``end remote`` sanity checks and the post-parse fix-ups, and
the order ``read_config`` hands remotes back in. Citations are
``file:line`` into that tarball.

The rules that surprise people, all verified against the compiled oracle:

* **Every number is parsed with base 0** (``config_file.c:249-333``), so
  ``010`` is octal 8 and ``0x10`` is 16 -- in durations as much as in codes.
  32-bit fields silently wrap (``strtoul`` into ``uint32_t``); a duration of
  2^31 or more becomes a *negative* ``lirc_t`` with only a warning.
* **A comment must start in column 0** (``config_file.c:974``). Anywhere
  else ``#`` is an ordinary token: ``gap 108000 # x`` is a parse error
  (``#`` is read as ``gap2``), and an indented comment inside a remote block
  is "unknown definition".
* **Any parse error rejects the whole file**, not just the remote it is in
  (``config_file.c:1237-1248``).
* **Multiple codes on one line** (``KEY 0x1 0x2``) build lircd's
  ``code->next`` list; a token starting with ``#`` ends the list.
* **REVERSE reverses the first code only**, never the ``next`` list
  (``config_file.c:1261-1265``).
* **Remotes come back sorted** (``config_file.c:714-750``): non-raw remotes
  first by total bit count, then raw remotes by number of codes, a stable
  sort -- unless any remote sets ``manual_sort``. Not reverse file order:
  ``ir_remotes_append`` appends.

Values are stored post-fix-up, i.e. exactly what ``lib/transmit.c`` later
reads. Each :class:`IrCode` keeps the physical line it was defined on and each
:class:`Remote` the lines of its ``begin remote`` and of every keyword, so an
importer can cite ``file:line`` for any value it uses.
"""

from __future__ import annotations

import glob as _glob
import os
import re
from dataclasses import dataclass, field

from .errors import LircConfigError, LircUndefinedBehaviour

# --- flags: lib/ir_remote_types.h:95-123 -------------------------------------
IR_PROTOCOL_MASK = 0x07FF
RAW_CODES = 0x0001
RC5 = 0x0002
SHIFT_ENC = RC5
RC6 = 0x0004
RCMM = 0x0008
SPACE_ENC = 0x0010
SPACE_FIRST = 0x0020
GRUNDIG = 0x0080
BO = 0x0100
SERIAL = 0x0200
XMP = 0x0400
REVERSE = 0x0800
NO_HEAD_REP = 0x1000
NO_FOOT_REP = 0x2000
CONST_LENGTH = 0x4000
REPEAT_HEADER = 0x8000
COMPAT_REVERSE = 0x00010000

DEFAULT_FREQ = 38000  # ir_remote_types.h:130, set at `begin remote`

#: config_file.c:95-115, in lircd's lookup order (first case-insensitive hit).
ALL_FLAGS: tuple[tuple[str, int], ...] = (
    ("RAW_CODES", RAW_CODES),
    ("RC5", RC5),
    ("SHIFT_ENC", SHIFT_ENC),  # obsolete alias of RC5
    ("RC6", RC6),
    ("RCMM", RCMM),
    ("SPACE_ENC", SPACE_ENC),
    ("SPACE_FIRST", SPACE_FIRST),
    ("GRUNDIG", GRUNDIG),
    ("BO", BO),
    ("SERIAL", SERIAL),
    ("XMP", XMP),
    ("REVERSE", REVERSE),
    ("NO_HEAD_REP", NO_HEAD_REP),
    ("NO_FOOT_REP", NO_FOOT_REP),
    ("CONST_LENGTH", CONST_LENGTH),
    ("REPEAT_HEADER", REPEAT_HEADER),
)

LINE_LEN = 4096  # config_file.c:70
MAX_INCLUDES = 10  # config_file.c:71
_PATH_BUF = 256  # config_file.c:902: the include path buffer
_WHITESPACE = re.compile(rb"[ \t]+")  # config_file.c:73: strtok on " \t" only

U64 = (1 << 64) - 1
U32 = (1 << 32) - 1


# --- C integer semantics ------------------------------------------------------

def i32(x: int) -> int:
    """Wrap to a C ``int`` / ``lirc_t`` (two's complement, as gcc on x86-64)."""
    x &= U32
    return x - (1 << 32) if x & 0x80000000 else x


def u64(x: int) -> int:
    """Wrap to ``ir_code`` (``uint64_t``, ir_remote_types.h:43)."""
    return x & U64


def gen_mask(bits: int) -> int:
    """ir_remote.h:420-431 -- a loop, so any ``bits`` >= 64 gives all ones."""
    return U64 if bits >= 64 else ((1 << bits) - 1 if bits > 0 else 0)


def shl64(count: int, what: str) -> int:
    """``((ir_code)1) << count``, refusing the counts C leaves undefined."""
    if not 0 <= count < 64:
        raise LircUndefinedBehaviour(
            f"{what}: shifting a 64-bit ir_code by {count} is undefined in C"
        )
    return 1 << count


def reverse(data: int, bits: int) -> int:
    """ir_remote.h:99-109 -- the low ``bits`` of ``data``, bit-reversed."""
    if bits > 64:
        raise LircUndefinedBehaviour(
            f"reverse() over {bits} bits shifts a 64-bit ir_code by >= 64"
        )
    c = 0
    for i in range(bits):
        if data & (1 << i):
            c |= 1 << (bits - 1 - i)
    return c


_HEX = b"0123456789abcdefABCDEF"
_C_ISSPACE = b" \t\n\v\f\r"


def _strto(tok: bytes, *, signed: bool) -> tuple[int, int, bool]:
    """glibc ``strtoul``/``strtoull``/``strtol`` with base 0 on LP64.

    Returns ``(value, end, erange)`` where ``end`` is the index ``endptr``
    points at. Base 0: ``0x`` + hex digit selects hex, a leading ``0`` octal,
    anything else decimal; ``0x`` with no hex digit after it parses as ``0``
    and stops at the ``x``. A leading ``-`` negates modulo 2^64 for the
    unsigned forms.
    """
    n = len(tok)
    i = 0
    while i < n and tok[i] in _C_ISSPACE:
        i += 1
    neg = False
    if i < n and tok[i] in b"+-":
        neg = tok[i] == 0x2D
        i += 1
    if i + 2 < n and tok[i] == 0x30 and tok[i + 1] in b"xX" and tok[i + 2] in _HEX:
        base, i = 16, i + 2
    elif i < n and tok[i] == 0x30:
        base = 8
    else:
        base = 10
    start = i
    value = 0
    while i < n:
        c = tok[i]
        if 0x30 <= c <= 0x39:
            d = c - 0x30
        elif 0x61 <= c <= 0x66:
            d = c - 0x61 + 10
        elif 0x41 <= c <= 0x46:
            d = c - 0x41 + 10
        else:
            break
        if d >= base:
            break
        value = value * base + d
        i += 1
    if i == start:
        return 0, 0, False  # no conversion: endptr = nptr
    if signed:
        if not neg and value > (1 << 63) - 1:
            return (1 << 63) - 1, i, True
        if neg and value > (1 << 63):
            return -(1 << 63), i, True
        return (-value if neg else value), i, False
    if value > U64:
        return U64, i, True
    return ((-value) & U64 if neg else value), i, False


# --- parsed objects -----------------------------------------------------------

def _decode(b: bytes) -> str:
    """Names are C byte strings; keep them lossless (surrogateescape)."""
    return b.decode("utf-8", "surrogateescape")


def encode_name(s: str) -> bytes:
    """Inverse of the name decoding, for byte-exact C comparisons."""
    return s.encode("utf-8", "surrogateescape")


@dataclass(eq=False)
class IrCode:
    """One ``struct ir_ncode``: a button line (ir_remote_types.h:60-86).

    ``code`` is the first code, ``next`` lircd's ``ir_code_node`` list of any
    further codes on the same line, ``signals`` the durations of a
    ``raw_codes`` entry (whose ``code`` lircd numbers 1, 2, 3...). ``line`` is
    the physical source line, 1-based.
    """

    name: str
    code: int
    line: int
    next: tuple[int, ...] = ()
    signals: tuple[int, ...] | None = None

    @property
    def codes(self) -> tuple[int, ...]:
        """Every code the button sends, in lircd's ``transmit_state`` order."""
        return (self.code, *self.next)


@dataclass(eq=False)
class Remote:
    """One ``struct ir_remote`` (ir_remote_types.h:150-239), user fields only.

    Runtime state (toggle state, remaining gap, ``transmit_state``) lives in
    :mod:`remote_ledger.lirc.transmit`, never here: parsing is pure.
    """

    path: str | None
    line: int
    name: str | None = None
    driver: str | None = None
    codes: list[IrCode] | None = None
    bits: int = 0
    flags: int = 0
    eps: int = 0
    aeps: int = 0
    phead: int = 0
    shead: int = 0
    pthree: int = 0
    sthree: int = 0
    ptwo: int = 0
    stwo: int = 0
    pone: int = 0
    sone: int = 0
    pzero: int = 0
    szero: int = 0
    plead: int = 0
    ptrail: int = 0
    pfoot: int = 0
    sfoot: int = 0
    prepeat: int = 0
    srepeat: int = 0
    pre_data_bits: int = 0
    pre_data: int = 0
    post_data_bits: int = 0
    post_data: int = 0
    pre_p: int = 0
    pre_s: int = 0
    post_p: int = 0
    post_s: int = 0
    gap: int = 0
    gap2: int = 0
    repeat_gap: int = 0
    toggle_bit: int = 0
    toggle_bit_mask: int = 0
    suppress_repeat: int = 0
    min_repeat: int = 0
    min_code_repeat: int = 0
    freq: int = DEFAULT_FREQ
    duty_cycle: int = 0
    toggle_mask: int = 0
    rc6_mask: int = 0
    baud: int = 0
    bits_in_byte: int = 0
    parity: int = 0
    stop_bits: int = 0
    ignore_mask: int = 0
    repeat_mask: int = 0
    manual_sort: int = 0
    #: The state ``config_file.c:1287-1294`` leaves before the first send.
    toggle_bit_mask_state: int = 0
    end_line: int | None = None
    #: keyword (lower case) -> physical line of its last assignment.
    field_lines: dict[str, int] = field(default_factory=dict)

    # --- ir_remote.h predicates, verbatim --------------------------------
    @property
    def protocol(self) -> int:
        return self.flags & IR_PROTOCOL_MASK

    def is_raw(self) -> bool:  # ir_remote.h:145
        return self.protocol == RAW_CODES

    def is_space_enc(self) -> bool:  # ir_remote.h:153
        return self.protocol == SPACE_ENC

    def is_space_first(self) -> bool:  # ir_remote.h:161
        return self.protocol == SPACE_FIRST

    def is_rc5(self) -> bool:  # ir_remote.h:169
        return self.protocol == RC5

    def is_rc6(self) -> bool:  # ir_remote.h:177: RC6 flag *or* any rc6_mask
        return self.protocol == RC6 or self.rc6_mask != 0

    def is_biphase(self) -> bool:  # ir_remote.h:185
        return self.is_rc5() or self.is_rc6()

    def is_rcmm(self) -> bool:
        return self.protocol == RCMM

    def is_grundig(self) -> bool:
        return self.protocol == GRUNDIG

    def is_bo(self) -> bool:
        return self.protocol == BO

    def is_serial(self) -> bool:
        return self.protocol == SERIAL

    def is_xmp(self) -> bool:
        return self.protocol == XMP

    def is_const(self) -> bool:  # ir_remote.h:233
        return bool(self.flags & CONST_LENGTH)

    def has_repeat(self) -> bool:  # ir_remote.h:131: both halves, signed
        return self.prepeat > 0 and self.srepeat > 0

    def has_repeat_gap(self) -> bool:
        return self.repeat_gap > 0

    def has_pre(self) -> bool:
        return self.pre_data_bits > 0

    def has_post(self) -> bool:
        return self.post_data_bits > 0

    def has_header(self) -> bool:
        return self.phead > 0 and self.shead > 0

    def has_foot(self) -> bool:
        return self.pfoot > 0 and self.sfoot > 0

    def has_toggle_bit_mask(self) -> bool:
        return self.toggle_bit_mask > 0

    def has_repeat_mask(self) -> bool:
        return self.repeat_mask > 0

    def has_toggle_mask(self) -> bool:
        return self.toggle_mask > 0

    def bit_count(self) -> int:  # ir_remote.h:82
        return self.pre_data_bits + self.bits + self.post_data_bits

    def min_gap(self) -> int:
        """ir_remote.h:313-319: the smaller non-zero of gap/gap2, as lirc_t."""
        if self.gap2 != 0 and self.gap2 < self.gap:
            return i32(self.gap2)
        return i32(self.gap)

    def max_gap(self) -> int:
        """ir_remote.h:321-327."""
        if self.gap2 > self.gap:
            return i32(self.gap2)
        return i32(self.gap)

    def get_code_by_name(self, name: str) -> IrCode | None:
        """ir_remote.c:393-411: first ASCII-case-insensitive match."""
        if not self.codes:
            return None
        want = encode_name(name).lower()
        for code in self.codes:
            if encode_name(code.name).lower() == want:
                return code
        return None


@dataclass
class LircConfig:
    """What ``read_config`` returns, plus provenance for citations.

    ``remotes`` is in read_config's order -- the order irsimsend and lircd
    iterate; ``file_order`` is the order the ``begin remote`` blocks appear
    (post-include), which is the order lircd ran its fix-ups in.
    ``warnings`` holds ``(line, message)`` for what lircd only logs.
    """

    path: str | None
    remotes: list[Remote]
    file_order: list[Remote]
    warnings: list[tuple[int, str]] = field(default_factory=list)


# --- the reader ---------------------------------------------------------------

_ID_NONE, _ID_REMOTE, _ID_CODES, _ID_RAW_CODES, _ID_RAW_NAME = range(5)


def _fgets_chunks(data: bytes):
    """Yield ``(chunk, physical_line)`` as ``fgets(buf, LINE_LEN, f)`` reads.

    fgets stops after LINE_LEN - 1 bytes, so config_file.c:957's "line too
    long" test (``len == LINE_LEN``) can never fire; a longer line is simply
    read as several, each bumping lircd's ``line`` counter.
    """
    pos, n, phys = 0, len(data), 1
    while pos < n:
        nl = data.find(b"\n", pos, pos + LINE_LEN - 1)
        end = nl + 1 if nl != -1 else min(pos + LINE_LEN - 1, n)
        yield data[pos:end], phys
        if nl != -1:
            phys += 1
        pos = end


class _Parser:
    """``read_config_recursive`` (config_file.c:934-1319) as an object.

    lircd keeps ``line`` and ``parse_error`` in file-scope statics shared by
    every include level; they are attributes of one parser here, saved and
    restored around includes as config_file.c:985-991 does.
    """

    def __init__(self, warnings: list[tuple[int, str]]):
        self.warnings = warnings
        self.line = 0  # lircd's counter (fgets chunks)
        self.phys = 0  # physical line, for citations

    # -- errors and warnings ------------------------------------------------
    def error(self, msg: str) -> LircConfigError:
        return LircConfigError(msg, path=self.path, line=self.line)

    def warn(self, msg: str) -> None:
        self.warnings.append((self.phys, msg))

    # -- numeric parsers, config_file.c:249-333 --------------------------------
    def s_strtocode(self, val: bytes) -> int:
        code, end, erange = _strto(val, signed=False)
        if (code == U64 and erange) or end != len(val) or not val:
            raise self.error(f'"{_decode(val)}": must be a valid (uint64_t) number')
        return code

    def s_strtou32(self, val: bytes) -> int:
        n, end, _ = _strto(val, signed=False)
        if not val or end != len(val):
            raise self.error(f'"{_decode(val)}": must be a valid (uint32_t) number')
        return n & U32

    def s_strtoi(self, val: bytes) -> int:
        n, end, _ = _strto(val, signed=True)
        if not val or end != len(val) or n != i32(n):
            raise self.error(f'"{_decode(val)}": must be a valid (int) number')
        return n

    def s_strtoui(self, val: bytes) -> int:
        n, end, _ = _strto(val, signed=False)
        if not val or end != len(val):
            raise self.error(
                f'"{_decode(val)}": must be a valid (unsigned int) number')
        return n & U32

    def s_strtolirc_t(self, val: bytes) -> int:
        n, end, _ = _strto(val, signed=False)
        if not val or end != len(val):
            raise self.error(f'"{_decode(val)}": must be a valid (lirc_t) number')
        h = i32(n)
        if h < 0:
            self.warn(f'"{_decode(val)}" is out of range')
        return h

    # -- flags, config_file.c:391-435 -----------------------------------------
    def parse_flags(self, val: bytes) -> int:
        flags = 0
        for flag in val.split(b"|"):
            for name, bit in ALL_FLAGS:
                if flag.lower() == name.lower().encode():
                    if bit & IR_PROTOCOL_MASK and flags & IR_PROTOCOL_MASK:
                        raise self.error(
                            f'multiple protocols given in flags: "{_decode(flag)}"')
                    flags |= bit
                    break
            else:
                raise self.error(f'unknown flag: "{_decode(flag)}"')
        return flags

    # -- one keyword line in a remote, config_file.c:437-619 ----------------
    def define_remote(self, key: bytes, val: bytes, val2: bytes | None,
                      rest: list[bytes], rem: Remote) -> None:
        k = key.lower()
        kname = _decode(k)
        argc = 1
        if k == b"name":
            rem.name = _decode(val)
        # config_file.c:446: the dyncodes branch is taken only with
        # lircd:dynamic-codes, which defaults to False (lircd.cpp:2238).
        elif k == b"driver":
            rem.driver = _decode(val)
        elif k == b"bits":
            rem.bits = self.s_strtoi(val)
        elif k == b"flags":
            rem.flags |= self.parse_flags(val)  # OR-ed: flags lines accumulate
        elif k == b"eps":
            rem.eps = self.s_strtoi(val)
        elif k == b"aeps":
            rem.aeps = self.s_strtoi(val) & U32
        elif k == b"plead":
            rem.plead = self.s_strtolirc_t(val)
        elif k == b"ptrail":
            rem.ptrail = self.s_strtolirc_t(val)
        elif k == b"pre_data_bits":
            rem.pre_data_bits = self.s_strtoi(val)
        elif k == b"pre_data":
            rem.pre_data = self.s_strtocode(val)
        elif k == b"post_data_bits":
            rem.post_data_bits = self.s_strtoi(val)
        elif k == b"post_data":
            rem.post_data = self.s_strtocode(val)
        elif k == b"gap":
            # config_file.c:488-492: a second token is always gap2 -- so a
            # trailing "# comment" is parsed as a number and rejected.
            if val2 is not None:
                rem.gap2 = self.s_strtou32(val2)
            rem.gap = self.s_strtou32(val)
            argc = 2 if val2 is not None else 1
        elif k == b"repeat_gap":
            rem.repeat_gap = self.s_strtou32(val)
        elif k == b"repeat_mask":
            rem.repeat_mask = self.s_strtocode(val)
        elif k in (b"toggle_bit", b"repeat_bit"):  # both obsolete names
            rem.toggle_bit = self.s_strtoi(val)
            kname = "toggle_bit"
        elif k == b"toggle_bit_mask":
            rem.toggle_bit_mask = self.s_strtocode(val)
        elif k == b"toggle_mask":
            rem.toggle_mask = self.s_strtocode(val)
        elif k == b"rc6_mask":
            rem.rc6_mask = self.s_strtocode(val)
        elif k == b"ignore_mask":
            rem.ignore_mask = self.s_strtocode(val)
        elif k == b"manual_sort":
            rem.manual_sort = self.s_strtoi(val)
        elif k == b"suppress_repeat":
            rem.suppress_repeat = self.s_strtoi(val)
        elif k == b"min_repeat":
            rem.min_repeat = self.s_strtoi(val)
        elif k == b"min_code_repeat":
            rem.min_code_repeat = self.s_strtoi(val) & U32
        elif k == b"frequency":
            rem.freq = self.s_strtoui(val)
        elif k == b"duty_cycle":
            rem.duty_cycle = self.s_strtoui(val)
        elif k == b"baud":
            rem.baud = self.s_strtoui(val)
        elif k == b"serial_mode":
            self._serial_mode(val, rem)
        elif val2 is not None and k in _PAIRS:
            p, s = _PAIRS[k]
            setattr(rem, p, self.s_strtolirc_t(val))
            setattr(rem, s, self.s_strtolirc_t(val2))
            argc = 2
        elif val2 is not None:
            raise self.error(
                f'unknown definiton: "{_decode(key)} {_decode(val)} {_decode(val2)}"')
        else:
            raise self.error(
                f'unknown definiton or too few arguments: "{_decode(key)} {_decode(val)}"')
        rem.field_lines[kname] = self.phys
        # config_file.c:1135-1141
        if (argc == 1 and val2 is not None) or (argc == 2 and val2 is not None and rest):
            self.warn(f"{rem.name}: garbage after '{_decode(key)}' token ignored")

    def _serial_mode(self, val: bytes, rem: Remote) -> None:
        """config_file.c:542-570."""
        if not val or not 0x35 <= val[0] <= 0x39:
            raise self.error("bad bit count")
        rem.bits_in_byte = val[0] - 0x30
        parity = val[1:2].upper()
        if parity == b"N":
            rem.parity = 0
        elif parity == b"E":
            rem.parity = 1
        elif parity == b"O":
            rem.parity = 2
        else:
            raise self.error("unsupported parity mode")
        if val[2:] == b"1.5":
            rem.stop_bits = 3
        else:
            rem.stop_bits = (self.s_strtoui(val[2:]) * 2) & U32

    # -- a code line, config_file.c:1141-1157 ---------------------------------
    def define_code(self, key: bytes, val: bytes, more: list[bytes]) -> IrCode:
        code = self.s_strtocode(val)
        nodes = []
        for tok in more:
            if tok[:1] == b"#":
                break  # the rest of the line is a comment
            nodes.append(self.s_strtocode(tok))
        return IrCode(name=_decode(key), code=code, line=self.phys, next=tuple(nodes))

    def check_ncode_dups(self, rem: Remote, codes: list[IrCode], code: IrCode) -> None:
        """config_file.c:918-931 -- notices only; duplicates are all kept."""
        if any(c.name == code.name for c in codes):
            self.warn(f"{rem.name}: Multiple definitions of: {code.name}")
        if any(c.code == code.code and c.next == code.next for c in codes):
            self.warn(f"{rem.name}: Multiple values for same code: {code.name}")

    # -- end remote, config_file.c:621-673 --------------------------------
    def sanity_checks(self, rem: Remote) -> None:
        if rem.name is None:
            raise self.error("Missing remote name")
        if rem.gap == 0:
            self.warn(f"{rem.name}: Gap value missing or invalid")
        if rem.has_repeat_gap() and rem.is_const():
            self.warn(f"{rem.name}: Repeat_gap ignored (CONST_LENGTH is set)")
        if rem.is_raw():
            return
        mask = gen_mask(rem.pre_data_bits)
        if rem.pre_data & mask != rem.pre_data:
            self.warn(f"{rem.name}: Invalid pre_data")
            rem.pre_data &= mask
        mask = gen_mask(rem.post_data_bits)
        if rem.post_data & mask != rem.post_data:
            self.warn(f"{rem.name}: Invalid post_data")
            rem.post_data &= mask
        if rem.codes is None:
            raise self.error(f"{rem.name}: No codes")
        mask = gen_mask(rem.bits)
        for c in rem.codes:
            if c.code & mask != c.code:
                self.warn(f"{rem.name}: Invalid code : {c.name}")
                c.code &= mask
            if any(n & mask != n for n in c.next):
                self.warn(f"{rem.name}: Invalid code {c.name}")
                c.next = tuple(n & mask for n in c.next)

    # -- the main loop ------------------------------------------------------
    def parse(self, data: bytes, path: str | None, depth: int) -> list[Remote] | None:
        """Return the remote list, or ``None`` where lircd returns NULL.

        Raises :class:`LircConfigError` where lircd returns ``(void*)-1``.
        """
        self.path = path
        self.line = 0
        top: list[Remote] | None = None
        rem: Remote | None = None
        mode = _ID_NONE
        codes_list: list[IrCode] = []
        raw_codes: list[IrCode] = []
        signals: list[int] = []
        raw_name: bytes | None = None
        raw_line = 0
        raw_counter = 0

        def finish_raw() -> None:
            # config_file.c:1073-1084 and 1162-1173
            if len(signals) % 2 == 0:
                raise self.error("bad signal length")
            raw_codes.append(IrCode(name=_decode(raw_name), code=raw_counter,
                                    line=raw_line, signals=tuple(signals)))

        for chunk, phys in _fgets_chunks(data):
            self.line += 1
            self.phys = phys
            s = chunk.split(b"\0", 1)[0]  # strlen stops at a NUL
            n = len(s)
            # config_file.c:963-972: strip one '\n', then test the byte
            # before it for '\r' -- which, on a line with no '\n' (the last
            # line of a file), is the *second*-to-last byte.
            if n > 0:
                n -= 1
                if s[n] == 0x0A:
                    s = s[:n]
            if n > 0:
                n -= 1
                if s[n] == 0x0D:
                    s = s[:n]
            if s[:1] == b"#":
                continue  # config_file.c:974: comments only in column 0
            toks = [t for t in _WHITESPACE.split(s) if t]
            if not toks:
                continue
            key = toks[0]
            val = toks[1] if len(toks) > 1 else None
            val2 = toks[2] if len(toks) > 2 else None
            rest = toks[3:]
            if val is not None:
                kl, vl = key.lower(), val.lower()
                if kl == b"include":
                    save = self.line, self.phys, self.path
                    top = self._read_all_included(path, depth, val, top)
                    self.line, self.phys, self.path = save
                elif kl == b"begin":
                    if vl == b"codes":
                        self._check_mode(mode, _ID_REMOTE, "begin codes")
                        if rem.codes is not None:
                            raise self.error("codes are already defined")
                        codes_list = []
                        mode = _ID_CODES
                    elif vl == b"raw_codes":
                        self._check_mode(mode, _ID_REMOTE, "begin raw_codes")
                        if rem.codes is not None:
                            raise self.error("codes are already defined")
                        # set_protocol(): ir_remote.h:139-143
                        rem.flags = (rem.flags & ~IR_PROTOCOL_MASK) | RAW_CODES
                        raw_counter = 0
                        raw_codes = []
                        mode = _ID_RAW_CODES
                    elif vl == b"remote":
                        self._check_mode(mode, _ID_NONE, "begin remote")
                        mode = _ID_REMOTE
                        rem = Remote(path=path, line=phys)
                        if not top:
                            top = [rem]
                        else:
                            top.append(rem)
                    elif mode == _ID_CODES:
                        # a button literally named "begin"
                        code = self.define_code(key, val, toks[2:])
                        self.check_ncode_dups(rem, codes_list, code)
                        codes_list.append(code)
                    else:
                        raise self.error(f'unknown section "{_decode(val)}"')
                elif kl == b"end":
                    if vl == b"codes":
                        self._check_mode(mode, _ID_CODES, "end codes")
                        rem.codes = codes_list
                        mode = _ID_REMOTE
                    elif vl == b"raw_codes":
                        if mode == _ID_RAW_NAME:
                            finish_raw()
                            mode = _ID_RAW_CODES
                        self._check_mode(mode, _ID_RAW_CODES, "end raw_codes")
                        rem.codes = raw_codes
                        mode = _ID_REMOTE
                    elif vl == b"remote":
                        self._check_mode(mode, _ID_REMOTE, "end remote")
                        self.sanity_checks(rem)
                        rem.end_line = phys
                        mode = _ID_NONE
                    elif mode == _ID_CODES:
                        # a button named "end": config_file.c:1110-1119
                        # adds it without the duplicate notice
                        codes_list.append(self.define_code(key, val, toks[2:]))
                    else:
                        raise self.error(f"unknown section {_decode(val)}")
                elif mode == _ID_REMOTE:
                    self.define_remote(key, val, val2, rest, rem)
                elif mode == _ID_CODES:
                    code = self.define_code(key, val, toks[2:])
                    self.check_ncode_dups(rem, codes_list, code)
                    codes_list.append(code)
                elif mode in (_ID_RAW_CODES, _ID_RAW_NAME):
                    if kl == b"name":
                        if mode == _ID_RAW_NAME:
                            finish_raw()
                        raw_name = val
                        raw_line = phys
                        raw_counter += 1
                        signals = []
                        mode = _ID_RAW_NAME
                        if val2 is not None:
                            self.warn(f"{rem.name}: garbage after 'name' token ignored")
                    else:
                        if mode == _ID_RAW_CODES:
                            raise self.error(f"no name for signal defined at line {self.line}")
                        # addSignal: s_strtoui, stored as lirc_t
                        signals.extend(i32(self.s_strtoui(t)) for t in toks)
                # _ID_NONE: config_file.c:1132 has no case for it, so a
                # two-token line outside any block is silently ignored.
            elif mode == _ID_RAW_NAME:
                signals.append(i32(self.s_strtoui(key)))
            else:
                raise self.error(f"error in configfile line {self.line}")

        if mode != _ID_NONE:
            raise self.error("unexpected end of file")
        if top is None:
            return None

        # config_file.c:1249-1316: fix-ups, in list (file) order.
        for r in top:
            self._fix_up(r)
        return top

    def _check_mode(self, is_mode: int, c_mode: int, what: str) -> None:
        """config_file.c:335-344."""
        if is_mode != c_mode:
            raise self.error(f'"{what}" isn\'t valid at this position')

    def _fix_up(self, rem: Remote) -> None:
        if not rem.is_raw() and rem.flags & REVERSE:
            # config_file.c:1254-1271 -- the first code of each button only.
            if rem.has_pre():
                rem.pre_data = reverse(rem.pre_data, rem.pre_data_bits)
            if rem.has_post():
                rem.post_data = reverse(rem.post_data, rem.post_data_bits)
            for c in rem.codes:
                c.code = reverse(c.code, rem.bits)
            rem.flags = (rem.flags & ~REVERSE) | COMPAT_REVERSE
        # config_file.c:1272-1276: old RC-6 files named the double-width
        # bit only through toggle_bit.
        if rem.flags & RC6 and rem.rc6_mask == 0 and rem.toggle_bit > 0:
            rem.rc6_mask = shl64(rem.bit_count() - rem.toggle_bit, "rc6_mask default")
        # config_file.c:1277-1286: toggle_bit N counts from the MSB, 1-based.
        if rem.toggle_bit > 0:
            if rem.has_toggle_bit_mask():
                self.warn(f"{rem.name} uses both toggle_bit and toggle_bit_mask")
            else:
                rem.toggle_bit_mask = shl64(
                    rem.bit_count() - rem.toggle_bit, "toggle_bit -> toggle_bit_mask")
            rem.toggle_bit = 0
        # config_file.c:1287-1294
        if rem.has_toggle_bit_mask() and not rem.is_raw() and rem.codes is not None:
            first = rem.codes[0].code if rem.codes else 0  # zeroed terminator
            state = first & rem.toggle_bit_mask
            if state:
                state ^= rem.toggle_bit_mask  # "start with state set to 0"
            rem.toggle_bit_mask_state = state
        if rem.is_serial():
            # config_file.c:1295-1307
            if rem.baud > 0:
                base = i32(1000000 // rem.baud)
                if rem.pzero == 0 and rem.szero == 0:
                    rem.pzero = base
                if rem.pone == 0 and rem.sone == 0:
                    rem.sone = base
            if rem.bits_in_byte == 0:
                rem.bits_in_byte = 8
        if rem.min_code_repeat > 0:
            # config_file.c:1308-1313: unsigned vs int comparison
            if not rem.has_repeat() or rem.min_code_repeat > (rem.min_repeat & U32):
                self.warn("invalid min_code_repeat value")
                rem.min_code_repeat = 0
        if rem.codes is None:
            # calculate_signal_lengths (config_file.c:1337) dereferences it.
            raise LircUndefinedBehaviour(
                f"{rem.name}: a RAW_CODES remote with no codes block makes "
                "lircd dereference a NULL code list (config_file.c:1337)"
            )

    # -- include, config_file.c:752-915 ---------------------------------
    def _read_all_included(self, name: str | None, depth: int, val: bytes,
                           top: list[Remote] | None) -> list[Remote] | None:
        inner = val[1:-1]  # config_file.c:905-906 drops the 1st and last byte
        if name is None:
            return top  # lirc_parse_relative returns the child, buff stays ""
        # lirc_parse_relative (config_file.c:779-809) into char buff[256]
        if inner.startswith(b"/"):
            pattern = inner[:_PATH_BUF - 1]  # snprintf truncates
        else:
            current = os.fsencode(name)
            if len(current) >= _PATH_BUF:
                return top  # returns NULL; glob("") matches nothing
            parent = os.path.dirname(current) or b"."
            if len(parent) + 1 + len(inner) + 1 > _PATH_BUF:
                # returns NULL with buff holding the directory: glob matches
                # it, and reading a directory yields no remotes.
                return top
            pattern = parent + b"/" + inner
        # glob() sorts its matches (GLOB_NOSORT is not passed)
        for match in sorted(_glob.glob(pattern)):
            if len(match) + 2 > _PATH_BUF - 1:
                # snprintf("\"%s\"") cuts the closing quote, and
                # lirc_parse_include then rejects it: "invalid quoting"
                self.warn("error parsing child file value: invalid quoting")
                continue
            top = self._read_included(name, depth, os.fsdecode(match), top)
        return top

    def _read_included(self, name, depth, child, top):
        if depth > MAX_INCLUDES:
            self.warn("too many files included")
            return top
        if os.path.isdir(child):
            return top  # fopen() succeeds, fgets() reads nothing: no remotes
        try:
            with open(child, "rb") as fh:
                data = fh.read()
        except OSError:
            # config_file.c:871-877 returns NULL here, dropping every remote
            # parsed so far -- reproduced, not fixed.
            self.warn(f"error opening child file '{child}'; ignoring")
            return None
        sub = _Parser(self.warnings)
        rems = sub.parse(data, child, depth + 1)
        if rems:  # ir_remotes_append (config_file.c:813-830)
            top = (top or []) + rems
        return top


_PAIRS = {
    b"header": ("phead", "shead"),
    b"three": ("pthree", "sthree"),
    b"two": ("ptwo", "stwo"),
    b"one": ("pone", "sone"),
    b"zero": ("pzero", "szero"),
    b"foot": ("pfoot", "sfoot"),
    b"repeat": ("prepeat", "srepeat"),
    b"pre": ("pre_p", "pre_s"),
    b"post": ("post_p", "post_s"),
}


def _remote_bits_cmp(r1: Remote, r2: Remote) -> int:
    """config_file.c:681-707."""
    raw1, raw2 = r1.is_raw(), r2.is_raw()
    if not raw1 and raw2:
        return -1
    if raw1 and not raw2:
        return 1
    if raw1 and raw2:
        s1, s2 = len(r1.codes), len(r2.codes)
    else:
        s1, s2 = r1.bit_count(), r2.bit_count()
    return 0 if s1 == s2 else (-1 if s1 < s2 else 1)


def sort_by_bit_count(remotes: list[Remote]) -> list[Remote]:
    """config_file.c:714-750: stable insertion sort, unless manual_sort."""
    if any(r.manual_sort for r in remotes):
        return list(remotes)
    out: list[Remote] = []
    for rem in remotes:
        i = 0
        while i < len(out) and _remote_bits_cmp(out[i], rem) <= 0:
            i += 1
        out.insert(i, rem)
    return out


def parse(data: bytes | str, path: str | None = None) -> LircConfig:
    """``read_config()`` (config_file.c:833-840) over ``data``.

    ``path`` is used for includes and citations. Raises
    :class:`LircConfigError` wherever lircd rejects the file.
    """
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogateescape")
    warnings: list[tuple[int, str]] = []
    top = _Parser(warnings).parse(data, path, 0) or []
    return LircConfig(path=path, remotes=sort_by_bit_count(top),
                      file_order=list(top), warnings=warnings)


def parse_file(path: str | os.PathLike) -> LircConfig:
    path = os.fspath(path)
    with open(path, "rb") as fh:
        return parse(fh.read(), path)
