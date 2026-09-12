# Oak

A minimal player dashboard for [oak.fabiomigueldp.me](https://oak.fabiomigueldp.me): an embedded BlueMap atlas, online players, public game chat, and a bounded website-to-Minecraft chat bridge.

The private **Oak Control** application at `/admin/` adds passkeys, live oversight,
local recovery points, reviewed operations, settings and role-based access.
See the [administration guide](docs/admin-control.md) for the demo, installation,
tests, recovery procedures and deliberately deferred features.

**Aviary** adds crafted perches, whistles and condor transport for unmodified Java
clients. Its [documentation index](aviary/README.md) routes to player usage,
development and operations. For general server automation, start with the
[operator interfaces](docs/operator-agents.md).

## Architecture

```text
Browser -> Caddy HTTPS -> Nginx (oak-web)
                         |-- /            static dashboard
                         |-- /map/        externally generated BlueMap files
                         |-- /status.json collector output
                         `-- /api/        Python chat bridge (SSE + POST)
                                            `-- local Minecraft RCON
```

- `public/`: dependency-free HTML, CSS, and JavaScript; no build step.
- `server/`: Python standard-library services for public status and chat.
- `tests/`: HTTP validation and rate-limit checks with mocked RCON delivery.
- `scripts/`: validation and commit-based deployment.
- `deploy/`: production Nginx/systemd configuration and the deployment runbook.
- `map/`: complete portable external NBT/MCA palette adapter, usage instructions, and documented limitations; no game assets or production orchestration.
- `docs/bluemap-26.3/`: isolated upstream compatibility evidence, owner-supplied screenshots, and a synthetic normalization reference. It does not contain production operations or a native BlueMap patch.

The maintained local checkout is `C:\Users\fabio\Projects\oak`.

Technical documentation is in English. Player-facing copy is Brazilian Portuguese. The visual direction is a restrained dark forest palette, map-first hierarchy, visible keyboard focus, and a stacked mobile layout.

## Local checks

Requires Python 3.12+ and Node.js 22+ (Node is only used for JavaScript syntax checks).

```sh
python scripts/check.py
node --check public/app.js
```

For a static layout preview:

```sh
python -m http.server 8080 --directory public
```

The preview does not provide `/map/`, `/status.json`, or `/api/`. Those depend on the production runtime; connection indicators will remain unavailable locally. Tests do not contact the production Minecraft server.

## Runtime boundaries

The existing Oracle Ubuntu VM runs Fabric Minecraft and BlueMap CLI separately. This repository does not provision Minecraft, contain worlds, or distribute Mojang assets. The map is generated outside Git and updates after world saving, snapshot adaptation, and rendering; it is not a live terrain stream.

Chat uses one shared monitor and Server-Sent Events, capped at 32 simultaneous streams. Website messages are labeled `[Web]`; visitor names are self-selected and are not authenticated Minecraft identities. Names accept up to 64 Unicode characters and messages up to 10,000, including line breaks, tabs, emoji, and symbols. Enter sends; Shift+Enter inserts a line break. Requests are capped at 128 KiB, with flood ceilings of 60 messages per IP and 300 overall per minute and no mandatory delay between messages. Origin checks and JSON serialization into fixed `tellraw` commands remain enforced. Long messages are split into commands of at most 1,446 ASCII bytes (plus 14 RCON framing bytes), preserving the full text in website history. A partially delivered message is not retried automatically. RCON credentials remain in the VM's restricted `server.properties` file. Never expose that file or raw server logs.

When Minecraft is online with no connected players, messages are accepted into web history and the sender is told there was nobody in game to receive them. These messages are not queued for later delivery to the game. RCON authentication, transport, and command errors still report an unconfirmed send.

## Deployment

GitHub Actions checks pushes and pull requests. Deployment is an explicit SSH operation against an exact commit, not an automatic deployment on every push. See [the deployment runbook](deploy/README.md) and [agent instructions](AGENTS.md).
