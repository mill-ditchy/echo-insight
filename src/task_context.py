"""Carry task ownership checks to result caches without affecting interactive callers."""

from contextlib import contextmanager
from contextvars import ContextVar

_result_guard = ContextVar("analysis_result_guard", default=None)


@contextmanager
def guarded_results(guard):
    token = _result_guard.set(guard)
    try:
        yield
    finally:
        _result_guard.reset(token)


def verify_result_owner():
    guard = _result_guard.get()
    if guard is not None:
        guard()
