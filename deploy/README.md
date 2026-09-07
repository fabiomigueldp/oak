# Production deployment

## Host and paths

- SSH alias: `oracle`; public website: `https://oak.fabiomigueldp.me`.
- Repository: `https://github.com/fabiomigueldp/oak.git`.
- Root-owned checkout: `/srv/oak/site-repo` (outside the public web root).
- Static files: `/srv/oak/web`; backend: `/srv/oak/chat-server.py` and `/srv/oak/collect.py`.
- Nginx: `/srv/oak/nginx.conf`, mounted into container `oak-web`.
- Services: `oak-chat`, `oak-web-collector`; units under `/etc/systemd/system/`.
- Release metadata and up to five file snapshots: `/srv/oak/deployments`.
- External map runtime: `/srv/oak/map-test`, served from `/srv/oak/map-test/web`.
- Minecraft and its credentials: `/srv/oak/server`, outside this repository and deployment scope.

Caddy terminates TLS and proxies to `oak-web:80` on the existing Docker network `adventurynetwork-app_edge`. Nginx forwards `/api/` to `172.19.0.1:8091`. The existing firewall permits this private network path; the backend is not published on a public Docker port. Cloudflare provides DNS, not hosting. These are existing infrastructure dependencies, not resources managed by this deployer.

## Routine release

Edit this repository, run checks, commit, and push to `main`. Wait for the GitHub **Checks** workflow to succeed, then obtain the full SHA:

```sh
git rev-parse HEAD
```

From the operator's computer, replace `<commit-sha>` with that 40-character value:

```sh
ssh oracle "sudo /usr/local/sbin/oak-site-deploy <commit-sha>"
```

The launcher takes an exclusive lock, fetches `origin/main`, rejects commits outside its history, and checks out the requested commit in detached mode. It runs the repository deployer, which checks Python/tests, validates Nginx and systemd configuration, snapshots the existing tracked files, installs changed files, and restarts only affected website services. It does not restart Minecraft or the map renderer.

Health checks cover HTTPS, status freshness, services, and the SSE snapshot. On failure, the deployer restores previous files and attempts to reactivate and verify the previous services. Failures remain visible to the SSH caller; rollback cannot guarantee recovery from an unrelated infrastructure outage. JavaScript syntax is checked locally and in CI because the VM does not require Node.js. The launcher does not query GitHub Actions; successful CI is an operator prerequisite.

Static files are installed individually with atomic replacement; this is not an atomic whole-site directory swap. Nginx configuration is written in place to preserve its Docker bind-mount inode. Bump asset versions in HTML when changing CSS/JS. Generated `status.json`, map tiles, warnings, worlds, logs, and game backups are never copied from Git or removed.

## Status and rollback

```sh
ssh oracle "sudo cat /srv/oak/deployments/current.json"
ssh oracle "sudo systemctl status oak-chat oak-web-collector --no-pager"
```

To roll back, run the same deploy command with a previously deployed full commit SHA. Retained commits must remain ancestors of `origin/main`; do not force-push deployment history. The previous commit is recorded in `current.json`. Pre-Git files are retained in the first deployment snapshot until normal five-snapshot retention removes it.

The bootstrap launcher is installed separately from `scripts/oak-site-deploy.py` as `/usr/local/sbin/oak-site-deploy` (root-owned, mode 0750). Changes to the launcher require an explicit reviewed installation; it does not replace itself during deployment. This workflow targets an already provisioned VM and is not a fresh-host installer.

## Map operation

To save Minecraft and prioritize incremental updates around the spawn:

```sh
ssh oracle "sudo /usr/local/sbin/oak-map-update"
```

This external command is independent of website deployment. BlueMap currently uses resources for Minecraft `26.3-pre-2`, with a snapshot adapter maintained outside this website repository. High-detail view distance is configured at 1,600 blocks; visitors may retain older browser preferences. Check live configuration before changing it.

## Migration from the initial workspace

The initial working files lived at `C:\Users\fabio\Projects\vostro\output\oak`. The maintained Git checkout is now `C:\Users\fabio\Projects\vostro\oak`. Historical `deploy-web.py` and `deploy-chat.py` provisioned containers and are not part of this repository or the recurring deployment workflow. Do not use the old SCP workflow for files managed here; make changes in Git to prevent drift.
