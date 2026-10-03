"""The japan family's encoders against IrpTransmogrifier 1.2.14, duration for duration.

One cited golden vector per protocol (tests/vectors/CITATIONS.md) pins one
parameter set. This file is the wider net: 34 parameter sets per protocol --
the corners, the decodes IrpTransmogrifier publishes, and a seeded random
sample -- rendered by the pinned 1.2.14 release as signed microseconds
(``render -r``), where every intro and repeat duration, including every
extent-padded gap, must be identical. Microseconds rather than Pronto, so
no quantization rule stands between the two encoders.

The fixture is generated, not published (the same status as the NECx2
vector), and its header says how to regenerate it.
"""

import json
from pathlib import Path

import pytest

from remote_ledger.protocols import REGISTRY

DATA = json.loads(
    (Path(__file__).parent / "vectors" / "irpt_render_sweeps_japan.json").read_text()
)["protocols"]


def _kwargs(name: str, params: dict) -> dict:
    if name == "Pioneer-2Part":
        # device = D0:D and function = F0:F, first frame in the high byte
        return dict(device=(params["D0"] << 8) | params["D"], subdevice=None,
                    function=(params["F0"] << 8) | params["F"])
    return dict(device=params["D"], subdevice=None, function=params["F"])


def test_every_protocol_has_a_sweep():
    assert sorted(DATA) == ["Denon", "JVC", "Pioneer-2Part", "Sharp"]
    assert all(len(cases) == 34 for cases in DATA.values())


@pytest.mark.parametrize("name", sorted(DATA))
def test_the_encoder_reproduces_every_render_duration_for_duration(name):
    for case in DATA[name]:
        signal = REGISTRY[name].encode(carrier_hz=case["carrier"], **_kwargs(name, case["params"]))
        assert list(signal.intro) == case["intro"], case["params"]
        assert list(signal.repeat) == case["repeat"], case["params"]


@pytest.mark.parametrize("name", sorted(DATA))
def test_the_sweep_would_notice_a_wrong_field(name):
    """The comparison is not vacuous: nudging any one parameter breaks it."""
    case = DATA[name][-1]
    kwargs = _kwargs(name, case["params"])
    kwargs["function"] ^= 1
    signal = REGISTRY[name].encode(carrier_hz=case["carrier"], **kwargs)
    assert (list(signal.intro), list(signal.repeat)) != (case["intro"], case["repeat"])
