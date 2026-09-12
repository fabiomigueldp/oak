"""On-demand operation schemas; discovery stays compact."""
TEXT = {'type': 'string'}
ID = {'type': 'string', 'format': 'uuid'}
REVISION = {'type': 'integer', 'minimum': 1}
OFFSET = {'type': 'integer', 'minimum': 0, 'maximum': 2 ** 31}
BYTE_OFFSET = {'type': 'integer', 'minimum': 0, 'maximum': 2 ** 63 - 1}
NAME = {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9_-]{0,63}$'}


def obj(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required)}


def enum(*values):
    return {'type': 'string', 'enum': list(values)}


JOB = obj({'kind': {**enum('shell', 'python', 'javascript', 'command'), 'default': 'shell'},
           'source': {'type': 'string', 'minLength': 1}, 'label': TEXT,
           'cwd': {**TEXT, 'description': 'Absolute host directory; defaults to the operator workspace.'},
           'timeout_seconds': {'type': ['number', 'null'], 'minimum': .01, 'default': None},
           'environment': {'type': 'object', 'additionalProperties': TEXT, 'maxProperties': 256},
           'resources': {'type': 'array', 'items': TEXT, 'maxItems': 64}, 'idempotency': TEXT}, ('source',))
TRIGGER = {'oneOf': [obj({'type': {'const': 'interval'}, 'seconds': {'type': 'number', 'minimum': .1}}, ('type', 'seconds')),
                     obj({'type': {'const': 'once'}, 'at': {'type': 'number', 'minimum': 0}}, ('type', 'at')),
                     obj({'type': {'const': 'event'}, 'name': TEXT}, ('type', 'name'))]}
METHODS = {
    'discover': ('Read available capabilities, methods and execution defaults.', obj()),
    'describe': ('Read one operation schema.', obj({'method': TEXT}, ('method',))),
    'jobs.submit': ('Execute trusted code or Minecraft commands; returns the durable job object. Omitted or null timeout is unlimited. Complete job JSON is limited to 1 MiB.', JOB),
    'jobs.list': ('List recent job metadata with next_offset and has_more; use jobs.get for source and environment. Status filters before limiting.', obj({'status': TEXT, 'offset': OFFSET, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50}})),
    'jobs.get': ('Read full job state and one output page. Offsets count bytes.', obj({'id': ID, 'offset': BYTE_OFFSET, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 65536}}, ('id',))),
    'jobs.cancel': ('Cancel queued work or terminate the running process group. Effects already delivered remain.', obj({'id': ID}, ('id',))),
    'routines.upsert': ('Create or replace a routine. Updates require all fields and current revision.', obj({'id': ID, 'revision': REVISION, 'name': TEXT, 'trigger': TRIGGER, 'job': JOB, 'enabled': {'type': 'boolean'}}, ('name', 'trigger', 'job'))),
    'routines.list': ('Read routine definitions with next_offset and has_more.', obj({'offset': OFFSET, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 100}})),
    'routines.run': ('Queue one immediate run, including a disabled routine.', obj({'id': ID, 'idempotency': TEXT}, ('id',))),
    'routines.delete': ('Delete a routine; existing jobs remain.', obj({'id': ID, 'revision': REVISION}, ('id', 'revision'))),
    'notebooks.list': ('Read all current notebook summaries.', obj()),
    'notebooks.get': ('Read Markdown and revision.', obj({'id': ID, 'revision': REVISION}, ('id',))),
    'notebooks.save': ('Save Markdown context; updates require current revision.', obj({'id': ID, 'title': TEXT, 'content': TEXT, 'revision': REVISION}, ('title',))),
    'notebooks.delete': ('Remove a notebook from the current list; history is retained.', obj({'id': ID, 'revision': REVISION}, ('id', 'revision'))),
    'events.list': ('Read events in ascending cursor order; continue with next_cursor.', obj({'after': BYTE_OFFSET, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 100}, 'name': TEXT})),
    'events.publish': ('Publish an event with up to 32768 bytes of JSON data; matching routines may execute code.', obj({'name': TEXT, 'data': {}}, ('name',))),
    'files.list': ('List an absolute host directory with next_offset and has_more.', obj({'path': TEXT, 'offset': OFFSET, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 200}}, ('path',))),
    'files.read': ('Read a UTF-8 page from an absolute host path. Hashing may be disabled; streams may have no hash.', obj({'path': TEXT, 'offset': BYTE_OFFSET, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 65536}, 'include_sha256': {'type': 'boolean', 'default': True}}, ('path',))),
    'files.write': ('Atomically write UTF-8 text. Optional expected_sha256 detects conflicting edits; empty hash requires absence.', obj({'path': TEXT, 'content': TEXT, 'expected_sha256': TEXT}, ('path', 'content'))),
    'minecraft.query': ('Read current status, or submit the supplied command as a job.', obj({'command': TEXT})),
    'packages.list': ('Read installed package metadata.', obj()),
    'packages.get': ('Read metadata and text files for a package.', obj({'name': NAME}, ('name',))),
    'packages.install': ('Store a versioned text bundle. Files use canonical relative paths. Does not activate datapacks or execute scripts.', obj({'name': NAME, 'version': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'}, 'kind': enum('script', 'datapack'), 'files': {'type': 'object', 'additionalProperties': TEXT, 'minProperties': 1}}, ('name', 'version', 'files'))),
    'packages.activate': ('Install the package ZIP into world datapacks and execute reload.', obj({'name': NAME}, ('name',))),
    'packages.deactivate': ('Remove the managed datapack ZIP and execute reload. Existing gameplay effects remain.', obj({'name': NAME}, ('name',))),
    'services.list': ('Read managed services and systemd state.', obj()),
    'services.upsert': ('Write a persistent root service. Does not start or restart it.', obj({'name': NAME, 'kind': enum('shell', 'python', 'javascript'), 'source': TEXT, 'cwd': TEXT}, ('name', 'source'))),
    'services.control': ('Control a managed service; enable and disable affect boot policy only.', obj({'name': NAME, 'action': enum('start', 'stop', 'restart', 'enable', 'disable')}, ('name', 'action'))),
    'services.logs': ('Read the last 200 service journal lines.', obj({'name': NAME}, ('name',))),
    'services.delete': ('Stop, disable and remove a managed service; source is retained.', obj({'name': NAME}, ('name',))),
    'world.call': ('Call the native Fabric bridge. Use native discover for available methods and docs/operator-world.md for inputs.', obj({'method': TEXT, 'data': {'type': 'object'}, 'idempotency': TEXT}, ('method',))),
}


def describe(method):
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError('No schema for method: ' + str(method))
    description, schema = METHODS[method]
    return {'method': method, 'description': description, 'inputSchema': schema}
