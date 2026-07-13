import hashlib

import pytest

from ui.uploads import consume_upload_once


class FakeUpload:
    def __init__(self, value):
        self.value = value

    def getvalue(self):
        return self.value


def test_no_upload_leaves_state_unchanged():
    state = {"other": "kept"}

    assert consume_upload_once(None, "signature", state=state) is None
    assert state == {"other": "kept"}


def test_new_upload_returns_bytes_and_records_sha256():
    state = {}
    content = b"new config"

    result = consume_upload_once(FakeUpload(content), "signature", state=state)

    assert result == content
    assert state["signature"] == hashlib.sha256(content).hexdigest()


def test_same_upload_is_consumed_only_once():
    state = {}
    upload = FakeUpload(b"same file")

    assert consume_upload_once(upload, "signature", state=state) == b"same file"
    assert consume_upload_once(upload, "signature", state=state) is None


def test_different_upload_replaces_signature_and_returns_new_bytes():
    state = {}
    consume_upload_once(FakeUpload(b"first"), "signature", state=state)

    result = consume_upload_once(FakeUpload(b"second"), "signature", state=state)

    assert result == b"second"
    assert state["signature"] == hashlib.sha256(b"second").hexdigest()


def test_parser_failure_does_not_retry_the_bad_file():
    state = {}
    upload = FakeUpload(b"not valid json")
    blob = consume_upload_once(upload, "signature", state=state)

    assert state["signature"] == hashlib.sha256(b"not valid json").hexdigest()
    with pytest.raises(ValueError, match="bad file"):
        raise ValueError("bad file")  # caller's parser fails after the guard is set

    assert blob == b"not valid json"
    assert consume_upload_once(upload, "signature", state=state) is None


def test_empty_and_mutable_byte_uploads_are_normalized():
    state = {}

    assert consume_upload_once(FakeUpload(bytearray()), "signature", state=state) == b""
    assert consume_upload_once(FakeUpload(memoryview(b"next")), "signature", state=state) == b"next"


def test_non_bytes_upload_is_rejected_without_mutating_state():
    state = {}

    with pytest.raises(TypeError, match="must return bytes-like data"):
        consume_upload_once(FakeUpload("text"), "signature", state=state)
    assert state == {}


def test_force_intentionally_reloads_the_same_upload():
    state = {}
    upload = FakeUpload(b"same file")
    assert consume_upload_once(upload, "signature", state=state) == b"same file"
    assert consume_upload_once(upload, "signature", state=state) is None
    assert consume_upload_once(upload, "signature", state=state, force=True) == b"same file"
