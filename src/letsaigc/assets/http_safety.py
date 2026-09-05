"""Suppress credential-bearing HTTP diagnostics only inside private requests."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

_private_http = ContextVar("letsaigc_private_http", default=False)


def _install_factory():
    previous = logging.getLogRecordFactory()

    def safe_record(*args, **kwargs):
        record = previous(*args, **kwargs)
        if _private_http.get() and (record.name == "httpx" or record.name.startswith(("httpx.", "httpcore"))):
            record.msg, record.args = "redacted_http_event", ()
            record.exc_info = record.exc_text = record.stack_info = None
        return record

    logging.setLogRecordFactory(safe_record)


_install_factory()


@contextmanager
def private_http():
    token = _private_http.set(True)
    try:
        yield
    finally:
        _private_http.reset(token)
