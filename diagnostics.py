"""Operational events for container logs. Callers supply identifiers and measured values only."""
import json
import logging
import os
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar

log = logging.getLogger('proxmate')
log.setLevel(logging.INFO)
log.propagate=False
if not log.handlers:
    log.addHandler(logging.StreamHandler())
trace = ContextVar('trace', default=None)
job = ContextVar('job', default=None)


def event(name, **fields):
    record = dict(time=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), event=name)
    if trace.get(): record['request_id'] = trace.get()
    if job.get(): record['job_id'] = job.get()
    log.info(json.dumps({**record, **fields}, separators=(',', ':')))


def failure(exc):
    frame = traceback.extract_tb(exc.__traceback__)[-1:]
    cause = exc.__cause__ or exc.__context__
    return dict(error_type=type(exc).__name__, cause=type(cause).__name__ if cause else None,
                location=f'{os.path.basename(frame[0].filename)}:{frame[0].lineno}' if frame else None)


@contextmanager
def stage(name, **fields):
    start = time.monotonic()
    event('stage.start', stage=name, **fields)
    try:
        yield
    except Exception as exc:
        event('stage.failed', stage=name, elapsed_ms=round((time.monotonic()-start)*1000), **fields, **failure(exc))
        raise
    else:
        event('stage.finished', stage=name, elapsed_ms=round((time.monotonic()-start)*1000), **fields)
