"""Small, testable helpers shared by file-upload flows.

This module deliberately has no Streamlit import. Callers inject their state
mapping, which keeps the once-per-file behavior usable from Streamlit while
remaining straightforward to unit test.
"""

from __future__ import annotations

import hashlib
from collections.abc import MutableMapping
from typing import Protocol


class ByteUpload(Protocol):
    """The part of Streamlit's uploaded-file interface this helper needs."""

    def getvalue(self) -> bytes: ...


def consume_upload_once(
    uploaded: ByteUpload | None,
    signature_key: str,
    *,
    state: MutableMapping[str, object],
) -> bytes | None:
    """Return bytes once for each distinct uploaded file.

    The SHA-256 signature is stored *before* the bytes are returned. Callers
    therefore parse only after the state is guarded: if parsing raises, the
    same file is ignored on the next rerun instead of producing an error loop.

    ``None`` means either that no file is present or that the current file was
    already consumed. An empty new file returns ``b""`` and must not be tested
    by truthiness.
    """
    if uploaded is None:
        return None

    raw = uploaded.getvalue()
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("uploaded.getvalue() must return bytes-like data")
    blob = bytes(raw)
    signature = hashlib.sha256(blob).hexdigest()
    if state.get(signature_key) == signature:
        return None

    # Set first: a parser failure in the caller must not retry forever.
    state[signature_key] = signature
    return blob


__all__ = ["ByteUpload", "consume_upload_once"]
