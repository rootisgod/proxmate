"""Short-lived read-only reviews, separate from the VM mutation queue."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import diagnostics


class Reviews:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='review')
        self.lock = threading.Lock()
        self.entries = {}

    def submit(self, server, plan, settings):
        ident = uuid.uuid4().hex
        request_id = diagnostics.trace.get()
        owner = server.current()['id']
        now = time.monotonic()
        with self.lock:
            self.entries = {k:v for k,v in self.entries.items() if v['state']=='checking' or v['expires']>now}
            if sum(v['state']=='checking' for v in self.entries.values())>=4:
                raise ValueError('Other build reviews are running. Try again shortly.')
            if len(self.entries)>=64:
                del self.entries[next(k for k,v in self.entries.items() if v['state']!='checking')]
            entry = dict(state='checking',stage='inventory',owner=owner,request_id=request_id)
            self.entries[ident] = entry

        def progress(stage):
            with self.lock: entry['stage'] = stage

        def work():
            token = diagnostics.trace.set(request_id)
            try:
                with diagnostics.stage('review',review_id=ident):
                    result = plan(settings,progress=progress)
                outcome = dict(state='ready',plan=result)
            except Exception as exc:
                outcome = dict(state='failed',error=str(exc) if isinstance(exc,ValueError) else
                               'Build review failed while checking the server. Retry or check the container logs.')
            finally:
                diagnostics.trace.reset(token)
                with server.lock: server.machine_requests -= 1
            with self.lock: entry.update(outcome,expires=time.monotonic()+1800)

        # submit is called while the HTTP connection boundary holds server.lock.
        server.machine_requests += 1
        try: self.pool.submit(work)
        except Exception:
            server.machine_requests -= 1
            with self.lock: del self.entries[ident]
            raise
        return dict(review_id=ident,request_id=request_id)

    def get(self, owner, ident):
        with self.lock:
            entry = self.entries.get(ident)
            if not entry or entry['owner']!=owner or entry.get('expires',float('inf'))<=time.monotonic():
                return None
            return {k:v for k,v in entry.items() if k not in ('owner','expires')}
