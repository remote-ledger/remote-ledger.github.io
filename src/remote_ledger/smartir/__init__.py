"""SmartIR's `codes/` database, imported under SPEC R19 (DESIGN.md section 15).

:mod:`.broadlink` decodes a Broadlink IR packet to microsecond durations;
:mod:`.codes` normalizes one upstream profile; :mod:`.importer` is the
whole-tree import, ``rl import smartir``'s implementation.
"""

from .importer import IMPORT_ROOT, REPORT, write_import

__all__ = ["IMPORT_ROOT", "REPORT", "write_import"]
