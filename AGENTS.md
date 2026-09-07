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
