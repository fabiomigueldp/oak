"""Single durable operation worker and bounded telemetry collector."""
import json
import os
import threading
import time


class Worker:
    def __init__(self, store, agent, poll_seconds=5):
        self.store, self.agent, self.poll_seconds = store, agent, poll_seconds
        self.stop = threading.Event()
        self.threads = []

    def start(self):
        self.lease = self.store.path.with_suffix('.worker.lock').open('a+b')
        try:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                self.lease.seek(0)
                self.lease.write(b'0')
                self.lease.flush()
                self.lease.seek(0)
                msvcrt.locking(self.lease.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.lease.close()
            raise RuntimeError('Another worker already owns this state directory.') from exc
        self.store.recover()
        self.threads = [threading.Thread(target=self.jobs, name='oak-jobs', daemon=True), threading.Thread(target=self.collect, name='oak-collector', daemon=True)]
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=3)
        if not any(thread.is_alive() for thread in self.threads):
            self.lease.close()

    def jobs(self):
        while not self.stop.is_set():
            try:
                self.store.tick_schedules()
                job = self.store.claim()
                if not job:
                    self.stop.wait(1)
                    continue
                try:
                    result = self.agent.call('execute', {'job': job['id'], 'kind': job['kind'], 'params': job['params']},
                                             progress=lambda step, detail: self.store.progress(job['id'], step, detail))
                    self.store.finish(job['id'], 'completed', result=result)
                except (OSError, ConnectionError, TimeoutError):
                    self.store.finish(job['id'], 'interrupted', error='Connection lost. Delivery is uncertain; inspect the host receipt before retrying.')
                except Exception as exc:
                    self.store.finish(job['id'], 'failed', error=str(exc)[:1000])
            except Exception:
                # Continue service after transient database/agent errors; expose collector state.
                self.stop.wait(2)

    def collect(self):
        last_extra = 0
        while not self.stop.is_set():
            try:
                snapshot = self.agent.call('snapshot')
                self.store.set('snapshot', snapshot)
                self.store.set('collector_error', None)
                self.store.sample(snapshot)
                if time.time() - last_extra > 20:
                    self.store.set('backups', self.agent.call('backups'))
                    self.store.set('configuration', self.agent.call('configuration'))
                    last_extra = time.time()
                for job in self.store.rows("SELECT id FROM jobs WHERE state='interrupted' ORDER BY created DESC LIMIT 5"):
                    receipt = self.agent.call('receipt', {'job': job['id']})
                    if receipt and receipt['state'] in ('completed', 'failed'):
                        self.store.finish(job['id'], receipt['state'], result=receipt.get('result'), error=receipt.get('error'))
            except Exception:
                self.store.set('collector_error', {'at': time.time(), 'message': 'A conexão com o agente está indisponível. Os últimos dados foram preservados.'})
            self.stop.wait(self.poll_seconds)
