"""Error types. Every message names the design decision it enforces."""


class LedgerError(Exception):
    """Base for every error this package raises deliberately."""


class ValidationError(LedgerError):
    """A file, signal, or form violates a stated rule."""


class BoundsError(ValidationError):
    """A numeric value falls outside D28's bounds."""


class ProntoParseError(ValidationError):
    """A Pronto Hex string could not be parsed under D25's rules."""


class EncodeError(LedgerError):
    """A protocol encoder was given parameters it cannot render."""
