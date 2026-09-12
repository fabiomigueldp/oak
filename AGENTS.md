# Oak website

- Scope: static player dashboard (`public/`), Python chat/status services (`server/`), and Oracle VM deployment (`scripts/`, `deploy/`). Minecraft worlds and generated BlueMap assets are external runtime dependencies.
- Write technical documentation, comments, commit messages, and operational output in English. Keep player-facing copy in Brazilian Portuguese unless requested otherwise.
- Preserve the restrained forest theme, keyboard accessibility, and mobile layout. Use `textContent` for untrusted chat/player data.
- Never commit credentials, server.properties, logs, player data, world files, backups, or generated map tiles. Never send test chat messages to real players; tests mock RCON delivery.
- Run `python scripts/check.py` and `node --check public/app.js` before committing. CI runs the same checks.
- Production: `https://oak.fabiomigueldp.me`, SSH alias `oracle`. Deploy an exact reviewed commit with `sudo /usr/local/sbin/oak-site-deploy <commit-sha>`; see `deploy/README.md`.
- Do not restart Minecraft, replace map data, change firewall/DNS, or run historical provisioning scripts as part of a website deployment. Keep runtime state outside Git.
- Local checkout: `C:\Users\fabio\Projects\oak`. Upstream compatibility material lives in `docs/bluemap-26.3/`; keep it independent of deployment details. Read BlueMap's contribution guidelines before any upstream submission and disclose AI assistance accurately.
- The full external adapter is in `map/adapter.py`; its synthetic NBT/MCA regression suite is `tests/test_map_adapter.py`. Keep inputs read-only, preserve palette order and packed indices, and require separate output paths. Website deployment must not install or run this adapter automatically.

## Aviary and server tools

- Aviary is the vanilla-Java bird transport system in `aviary/`, with Oak integration in `admin/aviary.py` and `public/admin/aviary.js`. Start with [aviary/README.md](aviary/README.md); its task table routes to usage, development or operations. Read only the relevant guide; release notes and concept studies are historical context, not required startup reading.
- Keep Aviary in-game copy in English and its Oak administration UI in Brazilian Portuguese. Preserve unmodified Java clients, optional pack acceptance, Bedrock login, destination identities and recovery journals.
- For server work, use the available `oak-operator` skill and discovery. [docs/operator-agents.md](docs/operator-agents.md) covers MCP/CLI/SDK usage; [docs/operator-world.md](docs/operator-world.md) covers native operations. Aviary's typed control socket is a separate interface, documented in its operations guide.
- Update the matching current guide when changing behavior, contracts or deployment. Keep this entry short; do not copy schemas, incident logs or release history here.
