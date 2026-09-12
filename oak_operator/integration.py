"""Native calls and transactional ingestion of Minecraft's bounded event ring."""
from contextlib import nullcontext
from .world import World

READS = {'discover', 'players', 'region.inspect', 'entities', 'events', 'receipt'}


class NativeIntegration:
    def __init__(self, kernel):
        self.kernel = kernel
        self.world = World()
        self.poller = World(timeout=2)
        self.last_error = None

    def call(self, data, actor):
        method = data.get('method')
        payload = data.get('data', {})
        if not isinstance(method, str) or not method or not isinstance(payload, dict):
            raise ValueError('Native method and object data are required.')
        key = data.get('idempotency')
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 256):
            raise ValueError('Native idempotency must be text within 256 characters.')
        if self.kernel.demo:
            return {'demo': True, 'method': method, 'simulated': True,
                    'message': 'No native game operation is performed in demo mode.'}
        mutation = method not in READS
        metadata = {'method': method, 'idempotency': key}
        if mutation:
            self.event('world.call.requested', metadata, actor)
        try:
            lock = self.kernel.runtime.lock() if mutation and self.kernel.runtime else nullcontext()
            with lock:
                result = self.world.call(method, payload, idempotency=key)
        except Exception as exc:
            if mutation:
                self.event('world.call.unconfirmed', {**metadata, 'error': str(exc)[:2000]}, actor)
            raise
        if mutation:
            self.event('world.call.completed', {**metadata, 'epoch': result.get('epoch'),
                                              'tick': result.get('tick')}, actor)
        return result

    def event(self, name, data, actor='system:native'):
        with self.kernel.store.transaction() as db:
            self.kernel._event(db, name, data, actor)

    def poll(self):
        with self.kernel.store.transaction() as db:
            saved = db.execute("SELECT epoch,cursor FROM source_cursors WHERE source='minecraft'").fetchone()
        epoch, cursor = (saved['epoch'], saved['cursor']) if saved else (None, 0)
        page = self.poller.call('events', {'after': cursor, 'limit': 256})
        if epoch is not None and page['epoch'] != epoch:
            # A cursor from the previous process can exceed every new event ID.
            page = self.poller.call('events', {'after': 0, 'limit': 256})
            cursor = 0
        with self.kernel.store.transaction() as db:
            if epoch is not None and epoch != page['epoch']:
                self.kernel._event(db, 'minecraft.restarted', {'previous_epoch': epoch,
                                   'epoch': page['epoch']}, 'system:native')
            oldest = page.get('oldest', 0)
            if oldest > cursor + 1:
                self.kernel._event(db, 'minecraft.events_gap', {'epoch': page['epoch'],
                                   'after': cursor, 'oldest': oldest}, 'system:native')
            for event in page['events']:
                if event['id'] <= cursor:
                    continue
                self.kernel._event(db, 'minecraft.' + event['name'],
                                   {'native': {'epoch': page['epoch'], 'id': event['id'], 'at': event['at']},
                                    'data': event['data']}, 'system:native')
            db.execute("INSERT INTO source_cursors VALUES('minecraft',?,?) ON CONFLICT(source) DO UPDATE SET epoch=excluded.epoch,cursor=excluded.cursor",
                       (page['epoch'], max(cursor, page['next'])))
        return page

    def run(self):
        while not self.kernel._stop.is_set():
            try:
                if self.poller.available():
                    self.poll()
                    if self.last_error is not None:
                        self.event('minecraft.bridge_recovered', {})
                        self.last_error = None
            except Exception as exc:
                error = str(exc)[:1000]
                if error != self.last_error:
                    self.event('minecraft.bridge_unavailable', {'error': error})
                    self.last_error = error
            self.kernel._stop.wait(1)
