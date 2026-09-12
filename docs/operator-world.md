# Native world bridge

The telemetry mod adds an owner-only Unix socket at
`/run/oak-telemetry/operator.sock` (mode `0600`). The root operator daemon reaches
it; the browser never receives direct socket access. Existing positions and
environment sockets retain their responsibilities.

The bridge is compiled for the installed Minecraft `26.3-rc-1` runtime. Building
or installing the jar does not activate Java code in a running server. Activation
requires the separately planned Minecraft restart. `discover` must confirm the
native socket is active before an agent assumes these methods are available.

## Protocol and methods

One newline-delimited request per connection:

```json
{"method":"region.inspect","data":{"dimension":"minecraft:overworld","min":[0,64,0],"max":[15,64,15],"cursor":0,"limit":256},"idempotency":"optional-request-key"}
```

Responses are `{ok:true,result:{...}}` or `{ok:false,error:"..."}`. Every successful
result includes `epoch` (new UUID at each server startup) and `tick`. Requests are
at most 2 MiB. Socket reads, writes, request hashing and JSON serialization run
off the game thread. At most 32 requests queue; one request is processed per tick.
When vanilla pauses an empty server, a request-only wakeup schedules bounded work
on the game thread's executor. Native access continues without changing the pause
policy or advancing game ticks merely to answer a query.

| Method | Data | Result |
| --- | --- | --- |
| `discover` | `{}` | Methods and data JSON schemas, dimension IDs and inclusive build heights, batching configuration, receipt capacity |
| `players` | Optional `uuid`, `inventory:boolean` | Player UUID/name/type/position/dimension/alive/health/food; optional inventory slot/item ID/count/name |
| `region.inspect` | `min:[x,y,z]`, `max:[x,y,z]`, optional `dimension`, `cursor`, `limit` | Blocks with position, loaded flag and canonical state; `next_cursor`, `volume`, `has_more` |
| `blocks.apply` | `blocks:[{position:[x,y,z],state:string,expected?:string}]`, optional `dimension`, `cursor`, `flags` | Per-index `before`, `after`, `changed` or `error`; `next_cursor`, `has_more` |
| `entities` | `min`, `max`, optional `dimension`, `type`, `limit` | Loaded entities with UUID/name/type/position/alive/health and `limit_reached` |
| `command` | `command:string` | Captured `messages`, command callback `results:[{success,result}]`, `output_truncated` |
| `events` | Optional `after:integer`, `limit` | `events:[{id,name,at,data}]`, `next`, `latest`, `oldest` |
| `receipt` | `idempotency:string` | `found` and original response when retained |

Default dimension is `minecraft:overworld`. Coordinates are integers. Region
bounds are inclusive; cursor order is X, then Y, then Z. Query each region page
using the same bounds and the previous `next_cursor`. Pages reflect their own
observation tick, not a frozen snapshot of the entire region.

Block reads and writes never load chunks implicitly. Unloaded reads report
`loaded:false`; writes return a per-item error. An operator can explicitly load
chunks through game commands before requesting edits. Block states use vanilla
syntax such as `minecraft:oak_log[axis=y]`; NBT is not part of `blocks.apply`.
Use vanilla commands for block-entity data and other gameplay capabilities.

Block batches default to at most 4096 inputs and about 2 ms of cooperative work
per tick. A response can process only a prefix; resend the original array with
`cursor:next_cursor`. The SDK `World.apply()` does this. The optional `expected`
state is checked immediately before each individual write. Batches are not atomic
and are not rolled back when another item fails. Always inspect every result.
`flags` defaults to vanilla update flags `3` and accepts `0..1023`.

The owner can adjust JVM properties `oak.operator.maxBatch` and
`oak.operator.budgetMicros` at the next restart. These budgets are cooperative:
one vanilla block update, entity query or arbitrary command can exceed them.
Entity query sides are limited to 256 blocks; tile larger areas. `limit_reached`
means there may be more entities; subdivide the query or raise the result limit.
Queries do not provide stable entity pagination while the world changes.

## Receipts, restart and events

An optional top-level `idempotency` key memoizes the last 128 successful responses
in memory. Reusing the same key and identical request returns the original
response; different input is rejected. Request object order must remain stable.
After a lost response, inspect `receipt` before repeating an effect. Receipts are
lost at restart or eviction; durable operator job history remains separate.

Any method accepts `data.expected_epoch`. A changed epoch rejects the request
before processing. `World.apply()` supplies this precondition between mutation
pages. This prevents continuing a multi-page edit unknowingly after a restart.

The 4096-entry event ring includes `player.join`, `player.leave`, `player.death`,
`player.dimension`, and bridge-originated `world.blocks`/`world.command` summaries.
Player transitions are sampled each tick. External block changes are not captured
as block events. Poll with the previous `next`; compare `epoch` and `oldest` to
detect restart or gaps. The operator kernel polls every second and copies events
into its durable event log, saving epoch and cursor in the same transaction.
Relayed names use the `minecraft.` prefix, such as `minecraft.player.join`;
payloads retain original data and native metadata. Restart and ring gaps are
surfaced as events. Named-event routines can consume the relay. The native ring
itself is not durable.

## Python

```python
from oak_operator.world import World

world = World()  # Run on Oracle with the socket's host authority.
native = world.call("discover")
for page in world.inspect([0, 64, 0], [15, 64, 15]):
    process(page["blocks"])
```

`World(socket_path=None, timeout=35).call(method, data=None, idempotency=None)`
returns the native result. `available()` only checks for a socket; call `discover`
to verify the actual runtime. There is no automatic RCON or command fallback.
For remote clients, use the kernel's `world.call` through CLI or MCP, or run a
durable Python job that uses this SDK. Put extensive work in durable jobs so its
code, output and results remain visible in the portal.

## Validation

`python tests/test_operator_world.py` uses mocked transport. The opt-in
`tests/operator_world_smoke.py` refuses to start without a separate network
namespace. It copies runtime jars into a fresh temporary directory, starts an
isolated flat world, and checks commands, receipts, chunk inspection, conflict
detection, writes/restoration, top build height, entity queries, and event cursors.
It never opens production worlds or sends messages to real players.
