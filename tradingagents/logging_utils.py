"""Small helpers for safe, low-cardinality diagnostics."""

import os


def safe_exception_type(exc: BaseException) -> str:
    """Return a bounded exception class name without rendering its message."""
    name = type(exc).__name__
    if (
        1 <= len(name) <= 64
        and name.isascii()
        and name[0].isalpha()
        and all(char.isalnum() or char == "_" for char in name)
    ):
        return name
    return "Exception"


def safe_exception_site(exc: BaseException) -> str:
    """Return the type plus the last in-package raise site, never the message.

    A ``file:line`` inside this package is credential-free by construction and
    turns an otherwise opaque sanitized kind (e.g. ``ValueError``) into an
    actionable pointer at the failing invariant.
    """
    site = None
    marker = f"{os.sep}tradingagents{os.sep}"
    tb = exc.__traceback__
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename
        if marker in filename:
            site = f"{os.path.basename(filename)}:{tb.tb_lineno}"
        tb = tb.tb_next
    kind = safe_exception_type(exc)
    return f"{kind}@{site}" if site else kind
