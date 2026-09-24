"""What lircd 0.10.2 transmits for a button: ``lib/transmit.c``, ported.

This is the send side of lircd, reduced to arithmetic: ``init_send_or_sim``
and everything it calls, ``send_ir_ncode`` (``lib/ir_remote.c``), the file
driver's ``send_func`` (``plugins/file.c``), and irsimsend's ``send_code``
(``tools/irsimsend.cpp``), which is the oracle this port was checked against
over the whole LIRC remotes corpus. ``file:line`` citations are into the lirc
0.10.2 release tarball.

Everything is integer arithmetic at C widths: durations are ``lirc_t``
(``int``, wrapped with :func:`~remote_ledger.lirc.conf.i32`), codes and masks
``ir_code`` (``uint64_t``). The rules that decide the output, each easy to get
wrong by "cleaning up":

* **Adjacent marks, and adjacent spaces, merge** (``transmit.c:86-114``); a
  space before any mark is **dropped** ("first signal is a space!"), so an
  RC5 or SPACE_FIRST frame loses its leading half-bit.
* **The buffer is made to end on a mark** (``sync_send_buffer``,
  ``transmit.c:159-167``): a pending final space is never emitted, and the
  gap is written after the last mark instead.
* **CONST_LENGTH pads to the gap** (``transmit.c:462-471``): the trailing
  space is ``min_gap - sum``, and with NO_HEAD_REP the first frame's sum
  **excludes its own header** (``transmit.c:339-340``), so that frame runs
  long by exactly ``phead + shead``. A frame already longer than the gap is
  refused ("too short gap"), not clamped.
* **A gap under 10 ms concatenates** (``transmit.c:486-509``): while a
  multi-code button still has codes queued, or ``repeat_countdown`` is
  positive, the next frame -- *as a repeat* -- is appended to the same buffer
  after the gap.
* **Repeats**: with ``repeat`` timings a repeat sends ``[plead] prepeat
  srepeat [ptrail]``, preceded by the header only under REPEAT_HEADER;
  without them it re-sends the code XOR ``repeat_mask``, dropping the header
  under NO_HEAD_REP and the foot under NO_FOOT_REP.
* **Toggles**: ``toggle_bit_mask`` state is flipped by the *caller* once per
  button press (irsimsend.cpp:190-192) and forces (one bit) or XORs (several
  bits) the masked bits; ``toggle_mask`` state counts sends 0, 1, 2, 3, 2, 3...
  and XORs the masked bits on odd counts (``transmit.c:263,428-432``).

:func:`transmit` is the one-button entry point. :class:`IrSimSend` replays a
whole irsimsend session, which is what ``tools/lirc_oracle_compare.py``
compares line for line.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Iterator

from .conf import (
    CONST_LENGTH,
    NO_FOOT_REP,
    NO_HEAD_REP,
    RAW_CODES,
    REPEAT_HEADER,
    U64,
    IrCode,
    LircConfig,
    Remote,
    encode_name,
    i32,
    reverse,
    shl64,
)
from .errors import (
    LircNoTimings,
    LircTerminated,
    LircTransmitError,
    LircUndefinedBehaviour,
    LircUnsupportedProtocol,
)

WBUF_SIZE = 256  # lib/transmit.h:35
LIRCD_EXACT_GAP_THRESHOLD = 10000  # transmit.c:23
LIRC_EOF = 0x08000000  # lib/lirc_config.h:90
#: irsimsend.cpp:185: ``static char last_code[32]``, filled by strncpy.
LAST_CODE_LEN = 32
#: Bound on the concatenation loop. lircd has none: a loop that adds nothing
#: to the buffer (every duration zero) spins forever. No such file exists in
#: the corpus; the bound turns a hang into an error.
_MAX_CONCAT_ROUNDS = 100_000


# --- per-session state ---------------------------------------------------------

@dataclass
class RemoteState:
    """The mutable half of ``struct ir_remote`` (ir_remote_types.h:220-228)."""

    toggle_bit_mask_state: int
    min_repeat: int
    toggle_mask_state: int = 0
    repeat_countdown: int = 0
    min_remaining_gap: int = 0
    max_remaining_gap: int = 0


class _SendBuffer:
    """``struct sbuf`` (transmit.c:35-45).

    ``data`` is the pointer the driver reads through. It is **not** always
    ``_data``: a raw code points it at the code's own signals
    (transmit.c:443), and only the non-raw, non-repeat path points it back
    (transmit.c:433). A repeat built through ``send_repeat`` therefore writes
    ``_data`` while the driver goes on reading whatever ``data`` last pointed
    at. That is lircd's behaviour and it is reproduced, because a raw remote
    with ``repeat`` timings transmits exactly that.
    """

    def __init__(self) -> None:
        self._data = [0] * WBUF_SIZE
        # send_buffer_init() (file.c:160) zeroes the struct: data = NULL.
        self.data: list[int] | tuple[int, ...] | None = None
        self.clear()

    def clear(self) -> None:  # transmit.c:63-72
        self.wptr = 0
        self.too_long = 0
        self.is_biphase = 0
        self.pendingp = 0
        self.pendings = 0
        self.sum = 0

    def read(self, i: int) -> int:
        """``send_buffer.data[i]``, refusing reads C would get wrong."""
        if self.data is None:
            raise LircUndefinedBehaviour(
                "lircd reads the send buffer through a NULL data pointer")
        if i >= len(self.data):
            raise LircUndefinedBehaviour(
                f"lircd reads send_buffer.data[{i}] past the end of the "
                f"{len(self.data)}-entry array it points at")
        return self.data[i]


@dataclass(frozen=True)
class SendResult:
    """What the file driver wrote for one ``send_ir_ncode`` call.

    ``kind`` is ``"timings"`` (``durations`` alternate pulse/space from a
    pulse and end with the gap line), ``"code"`` (the scancode-only line,
    ``code``), or ``"failed"`` (nothing written; ``reason`` is lircd's log
    message).

    ``eof_index`` is the index of the first written value carrying the
    ``LIRC_EOF`` bit (for ``"code"``, 0), or ``None``. file.c:206-209 raises
    ``SIGUSR1`` right after writing that line, which ends irsimsend; whether
    the session stops there is the session's business (:class:`IrSimSend`),
    so the result keeps everything the driver would have written.
    """

    kind: str
    durations: tuple[int, ...] = ()
    code: int = 0
    reason: str = ""
    eof_index: int | None = None

    @property
    def terminated(self) -> bool:
        """Whether irsimsend, run unmodified, dies on this send."""
        return self.eof_index is not None

    def lines(self, exit_on_eof: bool = True) -> list[str]:
        """The exact lines of ``simsend.out`` (plus the oracle's ``# end``).

        With ``exit_on_eof`` the output stops after the ``LIRC_EOF`` line, as
        the unmodified driver's does.
        """
        if self.kind == "code":
            return [f"code {self.code}"]
        if self.kind == "failed":
            return []
        out = [
            f"{'space' if i % 2 else 'pulse'} {d}"
            for i, d in enumerate(self.durations)
        ]
        if exit_on_eof and self.eof_index is not None:
            return out[: self.eof_index + 1]
        return out + ["# end"]


class Lircd:
    """lircd's transmit-side state for one loaded config.

    Owns what lircd keeps in statics and in the remote structs: the global
    send buffer, ``repeat_remote`` (ir_remote.c:57), each remote's runtime
    state and each code's ``transmit_state``. Constructing one replays the
    ``init_sim`` calls ``calculate_signal_lengths`` makes while parsing
    (config_file.c:1321-1374), because they leave the buffer's data pointer
    where the first send may find it.

    ``trace`` counts how often each non-obvious rule fired during real
    (non-sim) sends, so a comparison run can say which rules it exercised.
    """

    def __init__(self, config: LircConfig | Iterable[Remote]) -> None:
        if isinstance(config, LircConfig):
            file_order = list(config.file_order)
        else:
            file_order = list(config)
        self.buf = _SendBuffer()
        self.repeat_remote: Remote | None = None
        self.trace: Counter[str] = Counter()
        self._live = False
        self._state: dict[Remote, RemoteState] = {}
        self._transmit_state: dict[IrCode, int | None] = {}
        for remote in file_order:
            self._calculate_signal_lengths(remote)

    def state(self, remote: Remote) -> RemoteState:
        st = self._state.get(remote)
        if st is None:
            st = self._state[remote] = RemoteState(
                toggle_bit_mask_state=remote.toggle_bit_mask_state,
                min_repeat=remote.min_repeat,
            )
        return st

    def _calculate_signal_lengths(self, remote: Remote) -> None:
        """config_file.c:1337-1374, for its side effects on the buffer only.

        The lengths it computes serve lircd's *decoder*; nothing in the send
        path reads them.
        """
        for c in remote.codes or ():
            for value in c.codes:
                for repeat in (0, 1):
                    self.init_send_or_sim(remote, c, sim=True,
                                          repeat_preset=repeat, sim_code=value)

    # --- transmit.c:74-195: the buffer primitives --------------------------
    def _t(self, rule: str) -> None:
        if self._live:
            self.trace[rule] += 1

    def _add(self, data: int) -> None:  # transmit.c:74-84
        b = self.buf
        if b.wptr < WBUF_SIZE:
            b.sum = i32(b.sum + data)
            b._data[b.wptr] = data
            b.wptr += 1
        else:
            b.too_long = 1

    def _pulse(self, data: int) -> None:  # transmit.c:86-97
        b = self.buf
        data = i32(data)
        if b.pendingp > 0:
            self._t("adjacent pulses merged")
            b.pendingp = i32(b.pendingp + data)
        else:
            if b.pendings > 0:
                self._add(b.pendings)
                b.pendings = 0
            b.pendingp = data

    def _space(self, data: int) -> None:  # transmit.c:99-114
        b = self.buf
        data = i32(data)
        if b.wptr == 0 and b.pendingp == 0:
            self._t("leading space dropped")
            return  # "first signal is a space!"
        if b.pendings > 0:
            self._t("adjacent spaces merged")
            b.pendings = i32(b.pendings + data)
        else:
            if b.pendingp > 0:
                self._add(b.pendingp)
                b.pendingp = 0
            b.pendings = data

    def _bad_send_buffer(self) -> bool:  # transmit.c:116-123
        b = self.buf
        return bool(b.too_long) or (b.wptr == WBUF_SIZE and b.pendingp > 0)

    def _check_send_buffer(self) -> bool:  # transmit.c:125-145
        b = self.buf
        if b.wptr == 0:
            return False  # "nothing to send"
        return all(b.read(i) != 0 for i in range(b.wptr))

    def _flush(self) -> None:  # transmit.c:147-157
        b = self.buf
        if b.pendingp > 0:
            self._add(b.pendingp)
            b.pendingp = 0
        if b.pendings > 0:
            self._add(b.pendings)
            b.pendings = 0

    def _sync(self) -> None:  # transmit.c:159-167
        b = self.buf
        if b.pendings > 0:
            self._t("pending final space not emitted")
        if b.pendingp > 0:
            self._add(b.pendingp)
            b.pendingp = 0
        if b.wptr > 0 and b.wptr % 2 == 0:
            self._t("even-length buffer trimmed")
            b.wptr -= 1  # drop a trailing space; `sum` keeps it

    def _header(self, r: Remote) -> None:
        if r.has_header():
            self._pulse(r.phead)
            self._space(r.shead)

    def _foot(self, r: Remote) -> None:  # space first: transmit.c:177-183
        if r.has_foot():
            self._space(r.sfoot)
            self._pulse(r.pfoot)

    def _lead(self, r: Remote) -> None:  # `!= 0`, not `> 0`: transmit.c:187
        if r.plead != 0:
            self._pulse(r.plead)

    def _trail(self, r: Remote) -> None:
        if r.ptrail != 0:
            self._pulse(r.ptrail)

    # --- transmit.c:197-295 ----------------------------------------------
    def _send_data(self, r: Remote, st: RemoteState, data: int, bits: int,
                   done: int) -> None:
        all_bits = r.bit_count()
        toggle_bit_mask_bits = bin(r.toggle_bit_mask).count("1")
        data = reverse(data, bits)
        if r.is_rcmm():
            # transmit.c:205-233: two bits per symbol, MSB pair first.
            self._t("RCMM data")
            if bits % 2 or done % 2:
                return  # "invalid bit number."
            for _ in range(0, bits, 2):
                sym = data & 3
                if sym == 0:
                    self._pulse(r.pzero)
                    self._space(r.szero)
                elif sym == 2:  # 2 and 1 swapped by reverse()
                    self._pulse(r.pone)
                    self._space(r.sone)
                elif sym == 1:
                    self._pulse(r.ptwo)
                    self._space(r.stwo)
                else:
                    self._pulse(r.pthree)
                    self._space(r.sthree)
                data >>= 2
            return
        if r.is_xmp():
            # transmit.c:234-247: a nibble n is pzero, then szero + n * sone.
            self._t("XMP data")
            if bits % 4 or done % 4:
                return
            for _ in range(0, bits, 4):
                nibble = reverse(data & 0xF, 4)
                self._pulse(r.pzero)
                self._space(r.szero + nibble * r.sone)
                data >>= 4
            return

        if bits <= 0:
            return
        mask = shl64(all_bits - 1 - done, f"{r.name}: send_data bit mask")
        for _ in range(bits):
            if r.has_toggle_bit_mask() and mask & r.toggle_bit_mask:
                if toggle_bit_mask_bits == 1:
                    # "backwards compatibility": the state *forces* the bit
                    self._t("toggle_bit_mask (1 bit) forces the bit")
                    data &= ~1 & U64
                    if st.toggle_bit_mask_state & mask:
                        data |= 1
                elif st.toggle_bit_mask_state & mask:
                    self._t("toggle_bit_mask (>1 bit) XORs a bit")
                    data ^= 1
            if r.has_toggle_mask() and mask & r.toggle_mask and st.toggle_mask_state % 2:
                self._t("toggle_mask XORs a bit")
                data ^= 1
            if data & 1:
                if r.is_biphase():
                    if mask & r.rc6_mask:
                        self._t("rc6_mask double-width bit")
                        self._space(2 * r.sone)
                        self._pulse(2 * r.pone)
                    else:
                        self._space(r.sone)
                        self._pulse(r.pone)
                elif r.is_space_first():
                    self._space(r.sone)
                    self._pulse(r.pone)
                else:
                    self._pulse(r.pone)
                    self._space(r.sone)
            else:
                # transmit.c:282: the rc6_mask test is not guarded by
                # is_biphase -- harmless, since a nonzero rc6_mask makes
                # is_rc6() true.
                if mask & r.rc6_mask:
                    self._t("rc6_mask double-width bit")
                    self._pulse(2 * r.pzero)
                    self._space(2 * r.szero)
                elif r.is_space_first():
                    self._space(r.szero)
                    self._pulse(r.pzero)
                else:
                    self._pulse(r.pzero)
                    self._space(r.szero)
            data >>= 1
            mask >>= 1

    def _pre(self, r: Remote, st: RemoteState) -> None:  # transmit.c:297-306
        if r.has_pre():
            self._send_data(r, st, r.pre_data, r.pre_data_bits, 0)
            if r.pre_p > 0 and r.pre_s > 0:
                self._pulse(r.pre_p)
                self._space(r.pre_s)

    def _post(self, r: Remote, st: RemoteState) -> None:  # transmit.c:308-317
        if r.has_post():
            if r.post_p > 0 and r.post_s > 0:
                self._pulse(r.post_p)
                self._space(r.post_s)
            self._send_data(r, st, r.post_data, r.post_data_bits,
                            r.pre_data_bits + r.bits)

    def _repeat(self, r: Remote) -> None:  # transmit.c:319-325
        self._lead(r)
        self._pulse(r.prepeat)
        self._space(r.srepeat)
        self._trail(r)

    def _send_code(self, r: Remote, st: RemoteState, code: int, repeat: int) -> None:
        """transmit.c:327-341."""
        if not repeat or not r.flags & NO_HEAD_REP:
            self._header(r)
        elif r.has_header():
            self._t("NO_HEAD_REP drops the header")
        self._lead(r)
        self._pre(r, st)
        self._send_data(r, st, code, r.bits, r.pre_data_bits)
        self._post(r, st)
        self._trail(r)
        if not repeat or not r.flags & NO_FOOT_REP:
            self._foot(r)
        elif r.has_foot():
            self._t("NO_FOOT_REP drops the foot")
        if not repeat and r.flags & NO_HEAD_REP and r.flags & CONST_LENGTH:
            # the header is not counted against the constant length
            self._t("NO_HEAD_REP|CONST_LENGTH: header left out of sum")
            self.buf.sum = i32(self.buf.sum - (r.phead + r.shead))

    # --- transmit.c:385-521 ----------------------------------------------
    def init_send_or_sim(self, r: Remote, code: IrCode, *, sim: bool,
                         repeat_preset: int = 0,
                         sim_code: int | None = None) -> str | None:
        """Build the send buffer. Returns ``None`` on success, else lircd's
        log message for why it returned 0."""
        b = self.buf
        repeat = repeat_preset
        if r.is_grundig() or r.is_serial() or r.is_bo():
            return "sorry, can't send this protocol yet"
        b.clear()
        if r.name == "lirc":
            # transmit.c:395-399: lircd's internal EOF remote.
            raise LircUndefinedBehaviour(
                "a remote named 'lirc' is lircd's reserved EOF remote; its "
                "send path writes through an unset buffer pointer")
        if r.is_biphase():
            b.is_biphase = 1
        st = self.state(r)
        self._live = not sim
        if not sim:
            if self.repeat_remote is None:
                st.repeat_countdown = st.min_repeat
            else:
                repeat = 1

        rounds = 0
        while True:  # init_send_loop:
            rounds += 1
            if rounds > _MAX_CONCAT_ROUNDS:
                raise LircUndefinedBehaviour(
                    "lircd's low-gap concatenation loop never terminates here")
            if repeat and r.has_repeat():
                self._t("repeat sends the repeat timings")
                if r.flags & REPEAT_HEADER and r.has_header():
                    self._t("REPEAT_HEADER header before repeat")
                    self._header(r)
                self._repeat(r)
                if b.data is not b._data:
                    self._t("repeat read through a raw code's pointer")
            elif not r.is_raw():
                ts = self._transmit_state.get(code)
                if sim:
                    next_code = code.code if sim_code is None else sim_code
                elif ts is None:
                    next_code = code.code
                else:
                    next_code = code.next[ts]
                if repeat:
                    self._t("repeat re-sends the code")
                if ts is not None and not sim:
                    self._t("multi-code: later code sent")
                if repeat and r.has_repeat_mask():
                    self._t("repeat_mask XOR on repeat")
                    next_code ^= r.repeat_mask
                self._send_code(r, st, next_code, repeat)
                if not sim and r.has_toggle_mask():
                    st.toggle_mask_state += 1
                    if st.toggle_mask_state == 4:
                        st.toggle_mask_state = 2
                b.data = b._data
            else:
                if code.signals is None:
                    return "no signals for raw send"
                self._t("raw code sent")
                if b.wptr > 0:
                    for s in code.signals:  # send_signals()
                        self._add(s)
                else:
                    b.data = code.signals
                    b.wptr = len(code.signals)
                    for s in code.signals:
                        b.sum = i32(b.sum + s)
            self._sync()
            if self._bad_send_buffer():
                return "buffer too small"
            if sim:
                break

            if r.has_repeat_gap() and repeat and r.has_repeat():
                self._t("repeat_gap replaces the gap")
                st.min_remaining_gap = i32(r.repeat_gap)
                st.max_remaining_gap = i32(r.repeat_gap)
            elif r.is_const():
                if r.min_gap() > b.sum:
                    self._t("CONST_LENGTH gap = min_gap - sum")
                    st.min_remaining_gap = i32(r.min_gap() - b.sum)
                    st.max_remaining_gap = i32(r.max_gap() - b.sum)
                else:
                    st.min_remaining_gap = r.min_gap()
                    st.max_remaining_gap = r.max_gap()
                    self._t("CONST_LENGTH too short gap: refused")
                    return f"too short gap: {r.gap}"
            else:
                st.min_remaining_gap = r.min_gap()
                st.max_remaining_gap = r.max_gap()

            # update transmit state (transmit.c:476-485)
            if code.next:
                ts = self._transmit_state.get(code)
                if ts is None:
                    ts = 0
                else:
                    ts = ts + 1 if ts + 1 < len(code.next) else None
                    if r.is_xmp() and ts is None:
                        ts = 0  # XMP cycles its codes forever
                self._transmit_state[code] = ts
            ts = self._transmit_state.get(code)
            if ((st.repeat_countdown > 0 or ts is not None)
                    and st.min_remaining_gap < LIRCD_EXACT_GAP_THRESHOLD):
                self._t("low-gap concatenation")
                if b.data is not b._data:
                    # "unrolling raw signal optimisation"
                    self._t("raw signals unrolled for concatenation")
                    n = b.wptr
                    signals = [b.read(i) for i in range(n)]
                    b.data = b._data
                    b.wptr = 0
                    for s in signals:
                        self._add(s)
                # "concatenating low gap signals"
                if not code.next or ts is None:
                    st.repeat_countdown -= 1
                self._space(st.min_remaining_gap)
                self._flush()
                b.sum = 0
                repeat = 1
                continue
            break

        # final_check:
        if not self._check_send_buffer():
            return "invalid send buffer"
        return None

    def reset_transmit_state(self, code: IrCode) -> None:
        """``code->transmit_state = NULL`` (irsimsend.cpp:187,193)."""
        self._transmit_state[code] = None

    # --- plugins/file.c:213-238 and lib/ir_remote.c:819-848 ----------------
    def send_ir_ncode(self, r: Remote, code: IrCode) -> SendResult:
        """``send_ir_ncode(remote, code, 0)`` through the file driver.

        irsimsend passes ``delay = 0``, so no timing is involved and
        ``last_code`` / ``last_send`` bookkeeping has no effect on output.
        """
        if r.pzero == 0 and r.szero == 0 and not r.flags & RAW_CODES:
            # file.c:219-224: `int duration` receives the 64-bit code.
            value = i32(code.code)
            return SendResult("code", code=value,
                              eof_index=0 if value & LIRC_EOF else None)
        reason = self.init_send_or_sim(r, code, sim=False)
        if reason is not None:
            return SendResult("failed", reason=reason)
        b = self.buf
        n = b.wptr
        if n % 2 == 0:
            # file.c:229-234 would read one entry past the buffer.
            raise LircUndefinedBehaviour(
                f"send buffer of even length {n}: the file driver reads past it")
        out = [b.read(i) for i in range(n)]
        out.append(self.state(r).min_remaining_gap)
        # file.c:206-209: raise(SIGUSR1) after the first LIRC_EOF value
        eof = next((i for i, v in enumerate(out) if v & LIRC_EOF), None)
        return SendResult("timings", durations=tuple(out), eof_index=eof)


# --- tools/irsimsend.cpp ---------------------------------------------------

def _is_repeat_name(name: bytes, last: bytes) -> bool:
    """``strcmp(code->name, last_code) == 0`` (irsimsend.cpp:195).

    ``last_code`` is a ``char[32]`` filled by ``strncpy``, so a previous name
    of 32 bytes or more leaves it unterminated and ``strcmp`` runs on into the
    next static, irsimsend's non-empty ``plugindir`` buffer (``nm -n``: 0x20
    bytes further on). No button name can equal that, so a long name is never
    seen as a repeat of itself.
    """
    return len(last) < LAST_CODE_LEN and name == last


class IrSimSend:
    """An irsimsend session: ``irsimsend -c <count> file`` (irsimsend.cpp).

    Every button of every remote, in read_config's order, each sent once as
    a press and ``count - 1`` more times as repeats, with lircd state carried
    from one button to the next exactly as irsimsend carries it: toggle
    state, the name of the previous button, the send buffer.

    An unmodified irsimsend exits at the first value with the ``LIRC_EOF``
    bit (see :class:`SendResult`); ``exit_on_eof=False`` models one whose
    ``SIGUSR1`` is ignored, which is how the oracle harness reaches the
    buttons after it. ``keep_min_repeat=True`` models one that leaves each
    remote's ``min_repeat`` as parsed, so a press carries lircd's minimum
    repeats (concatenated into the press when the gap is under 10 ms).
    """

    def __init__(self, config: LircConfig, count: int = 1, *,
                 exit_on_eof: bool = True, keep_min_repeat: bool = False) -> None:
        self.config = config
        self.count = count
        #: False replays an irsimsend whose SIGUSR1 is ignored, so the
        #: buttons after a LIRC_EOF value are still sent.
        self.exit_on_eof = exit_on_eof
        #: True sends with each remote's parsed min_repeat, as lircd does,
        #: instead of the zero irsimsend.cpp:168,194 forces.
        self.keep_min_repeat = keep_min_repeat
        self.lircd = Lircd(config)
        self.last_code = b""
        self.terminated = False
        # setup(): irsimsend.cpp:168 zeroes the first remote's min_repeat;
        # send_code() below does the same for every remote it touches.
        if config.remotes:
            self.lircd.state(config.remotes[0]).min_repeat = 0

    def send_code(self, r: Remote, code: IrCode) -> list[SendResult]:
        """irsimsend.cpp:182-203."""
        if self.terminated:
            raise LircTerminated("irsimsend already exited on LIRC_EOF")
        lircd = self.lircd
        st = lircd.state(r)
        lircd.reset_transmit_state(code)
        if r.has_toggle_mask():
            st.toggle_mask_state = 0
        if r.has_toggle_bit_mask():
            st.toggle_bit_mask_state ^= r.toggle_bit_mask
        st.min_repeat = r.min_repeat if self.keep_min_repeat else 0
        name = encode_name(code.name)
        if _is_repeat_name(name, self.last_code):
            lircd.repeat_remote = r
        results = []
        for i in range(max(self.count, 1)):
            if i == 1:
                lircd.repeat_remote = r
            res = lircd.send_ir_ncode(r, code)
            results.append(res)
            if res.terminated and self.exit_on_eof:
                self.terminated = True
                return results
        lircd.repeat_remote = None
        self.last_code = name[:LAST_CODE_LEN]
        return results

    def run(self) -> Iterator[tuple[Remote, IrCode, list[SendResult]]]:
        """simsend_remote (irsimsend.cpp:206-218)."""
        for r in self.config.remotes:
            for code in r.codes or ():
                yield r, code, self.send_code(r, code)
                if self.terminated:
                    return


def transmit(remote: Remote, code: str | IrCode, sends: int = 2) -> list[tuple[int, ...]]:
    """The durations lircd transmits for one press of one button.

    Emulates ``irsimsend -c <sends> -k <button>`` on a freshly loaded remote:
    the first entry is the press, each later one a repeat. Every entry
    alternates pulse/space starting with a pulse and ends with the gap lircd
    waits before the next send -- exactly the lines the file driver writes.

    ``code`` is an :class:`IrCode` or a button name (first case-insensitive
    match, as ``get_code_by_name``). Raises :class:`LircUnsupportedProtocol`
    for GRUNDIG/BO/SERIAL, :class:`LircNoTimings` for a scancode-only remote,
    and :class:`LircTransmitError` where lircd would refuse a send.
    """
    if isinstance(code, str):
        found = remote.get_code_by_name(code)
        if found is None:
            raise LircTransmitError(f"no such button: {code!r}")
        code = found
    if remote.is_grundig() or remote.is_bo() or remote.is_serial():
        raise LircUnsupportedProtocol(
            f"{remote.name}: lircd cannot send "
            f"{'GRUNDIG' if remote.is_grundig() else 'BO' if remote.is_bo() else 'SERIAL'}"
            " (transmit.c:389-393)")
    session = IrSimSend(LircConfig(path=remote.path, remotes=[remote],
                                   file_order=[remote]), count=sends)
    out: list[tuple[int, ...]] = []
    for i, res in enumerate(session.send_code(remote, code)):
        if res.kind == "code":
            raise LircNoTimings(res.code)
        if res.kind == "failed":
            raise LircTransmitError(res.reason, send_index=i)
        if res.terminated:
            raise LircTerminated(
                f"send {i}: a value with the LIRC_EOF bit set ends irsimsend")
        out.append(res.durations)
    return out
