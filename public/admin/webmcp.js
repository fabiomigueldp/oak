/* Top-level site tools share the portal session and backend authorization. */
const object = (properties = {}, required = []) => ({ type: 'object', properties, required, additionalProperties: false });
const text = { type: 'string' };
const id = { type: 'string', minLength: 1 };
const registrations = new WeakMap();
export const JOB_SCHEMA = object({
  kind: { type: 'string', enum: ['shell', 'python', 'javascript', 'command'] },
  source: { type: 'string', minLength: 1 }, label: text, cwd: text,
  timeout_seconds: { type: ['number', 'null'], exclusiveMinimum: 0 },
  environment: { type: 'object', additionalProperties: { type: 'string' } },
  resources: { type: 'array', items: text }, idempotency: text,
}, ['kind', 'source']);

export function operatorReadPath({ resource, id: item, offset = 0 }) {
  const collections = { jobs: '/jobs', routines: '/routines', notebooks: '/notebooks', events: '/events', discover: '/discover' };
  if (Object.hasOwn(collections, resource)) return collections[resource];
  if (!['job', 'notebook'].includes(resource) || typeof item !== 'string' || !item) throw new Error('A valid resource and ID are required.');
  if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('Offset must be a non-negative integer.');
  return `/${resource === 'job' ? 'jobs' : 'notebooks'}/${encodeURIComponent(item)}${resource === 'job' ? `?offset=${offset}` : ''}`;
}

export async function registerOakTools(ctx, modelContext = globalThis.document?.modelContext) {
  const { state, api, navigate, focusMap, pages } = ctx;
  if (!modelContext?.registerTool || state.session?.user.role !== 'owner') return () => {};
  if (globalThis.window && window.self !== window.top) return () => {};
  const previous = registrations.get(modelContext);
  if (previous?.state === state) {
    previous.resume(state.session);
    return previous.stop;
  }
  let session = state.session;
  let stopped = false;
  const registered = [];
  const requireSession = () => {
    if (stopped || state.session !== session || state.session?.user.role !== 'owner') throw new Error('An active owner session is required.');
  };
  const mutation = { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true };
  const definitions = [
    {
      name: 'oak_context', description: 'Read the current Oak view, map selection, and connection state.',
      inputSchema: object(), annotations: { readOnlyHint: true, openWorldHint: false },
      execute: async () => ({ page: state.page, demo: Boolean(session.demo), role: session.user.role,
        selection: state.mapSelection || null, snapshot: state.overview?.snapshot || null }),
    },
    {
      name: 'oak_navigate', description: 'Open an Oak workspace in this page.',
      inputSchema: object({ page: { type: 'string', enum: Object.keys(pages) } }, ['page']),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
      execute: async ({ page }) => {
        if (!Object.hasOwn(pages, page)) throw new Error('Unknown workspace.');
        await navigate(page); return { page: state.page };
      },
    },
    {
      name: 'oak_map_focus', description: 'Center the portal map on coordinates. Does not change the game.',
      inputSchema: object({ position: { type: 'array', minItems: 3, maxItems: 3, items: { type: 'number' } },
        dimension: { type: 'string', enum: ['minecraft:overworld', 'minecraft:the_nether', 'minecraft:the_end'] } }, ['position', 'dimension']),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
      execute: async ({ position, dimension }) => {
        if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) throw new Error('Three finite coordinates are required.');
        if (!['minecraft:overworld', 'minecraft:the_nether', 'minecraft:the_end'].includes(dimension)) throw new Error('Unknown dimension.');
        await navigate('world'); requireSession();
        if (state.page !== 'world') throw new Error('Map navigation was not completed.');
        focusMap({ position, dimension }); return { page: 'world', position, dimension };
      },
    },
    {
      name: 'oak_operator_read', description: 'Read capabilities, jobs, output, routines, notebooks, or events. Job output uses a byte offset.',
      inputSchema: object({ resource: { type: 'string', enum: ['discover', 'jobs', 'job', 'routines', 'notebooks', 'notebook', 'events'] },
        id, offset: { type: 'integer', minimum: 0 } }, ['resource']),
      annotations: { readOnlyHint: true, openWorldHint: false },
      execute: args => api(`/operator${operatorReadPath(args)}`),
    },
    {
      name: 'oak_execute', description: 'Execute server code or a Minecraft command as the owner. Returns a persistent job ID; inspect its output to verify completion.',
      inputSchema: JOB_SCHEMA, annotations: mutation,
      execute: args => api('/operator/jobs', args),
    },
    {
      name: 'oak_cancel', description: 'Request cancellation of a running or queued operator job. Inspect the job to verify its final state.',
      inputSchema: object({ id }, ['id']), annotations: { ...mutation, idempotentHint: true },
      execute: ({ id: item }) => api(`/operator/jobs/${encodeURIComponent(item)}/cancel`, {}),
    },
    {
      name: 'oak_operator_call', description: 'Call the Oak operator API as the owner. Can change files, services, packages, routines, notebooks, and the game. Read discover for methods and schemas.',
      inputSchema: object({ method: { type: 'string', minLength: 1 }, data: { type: 'object', additionalProperties: true } }, ['method']),
      annotations: mutation,
      execute: ({ method, data = {} }) => api('/operator/call', { method, data }),
    },
  ];
  const stop = () => {
    stopped = true;
    // Some clients expose registration without unregistration. Keep those
    // handlers revoked and rebind them only after a new authenticated login.
    if (typeof modelContext.unregisterTool !== 'function') return;
    for (const name of registered.splice(0)) {
      try { modelContext.unregisterTool?.(name); } catch { /* Session checks also revoke retained handlers. */ }
    }
    registrations.delete(modelContext);
  };
  try {
    for (const definition of definitions) {
      if (state.session !== session || state.session?.user.role !== 'owner') { stop(); break; }
      const execute = definition.execute;
      await modelContext.registerTool({ ...definition, execute: async args => { requireSession(); return execute(args || {}); } });
      registered.push(definition.name);
    }
    if (registered.length === definitions.length) registrations.set(modelContext, {
      state, stop, resume(nextSession) { session = nextSession; stopped = false; },
    });
    if (state.session !== session) stop();
  } catch { stop(); }
  return stop;
}
