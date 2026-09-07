# Oak

A minimal player dashboard for [oak.fabiomigueldp.me](https://oak.fabiomigueldp.me): an embedded BlueMap atlas, online players, public game chat, and a bounded website-to-Minecraft chat bridge.

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

The existing Oracle Ubuntu VM runs Vanilla Minecraft and BlueMap CLI separately. This repository does not provision Minecraft, contain worlds, or distribute Mojang assets. The map is generated outside Git and updates after world saving, snapshot adaptation, and rendering; it is not a live terrain stream.

Chat uses one shared monitor and Server-Sent Events, capped at 32 simultaneous streams. Website messages are labeled `[Web]`; visitor names are self-selected and are not authenticated Minecraft identities. The server validates the origin and input, limits each IP to one message per 10 seconds and all visitors to 15 messages per minute, and serializes text into a fixed `tellraw` command. RCON credentials remain in the VM's restricted `server.properties` file. Never expose that file or raw server logs.

## Deployment

GitHub Actions checks pushes and pull requests. Deployment is an explicit SSH operation against an exact commit, not an automatic deployment on every push. See [the deployment runbook](deploy/README.md) and [agent instructions](AGENTS.md).
