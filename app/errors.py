"""Errors the POS raises on purpose.

The web layer will map these to HTTP status codes:
ValidationError -> 422, NotFoundError -> 404, ConflictError -> 409.
"""


class POSError(Exception):
    """Base class for every deliberate POS error. The message is safe to show to the cashier."""


class ValidationError(POSError):
    """The input is wrong (bad price, negative quantity, empty name...)."""


class NotFoundError(POSError):
    """The menu item, order or line does not exist."""


class ConflictError(POSError):
    """The request is valid but not allowed right now (table busy, order already paid...)."""


class StorageError(POSError):
    """The data file could not be read or written."""
