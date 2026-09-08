# Production deployment

The private administrative service has a separate explicit installation and
recovery procedure: [Oak Control operations](../docs/admin-control.md). Website
deployment includes its static interface and fixed API proxy, but does not install
the privileged agent or restart Minecraft. Activate the agent only from a reviewed
clean commit using that procedure.

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

If hosted CI is unavailable before any job starts, record the infrastructure failure and run both documented checks locally against the exact commit before deployment. On 2026-09-07 UTC, GitHub rejected the initial run because the account was locked due to a billing issue; no CI test executed. Local checks passed, and the deployer also executes Python and isolated tests on the VM. This is a temporary operator-verified fallback, not a successful GitHub Actions run.

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

This external command is independent of website deployment. BlueMap currently uses resources for Minecraft `26.3-pre-2`, with a snapshot adapter maintained outside this website repository. The website applies an early quality profile through `public/map-profile.js`, injected by Nginx before the BlueMap module on both `/map/` and `/map/index.html`. Generated map files are not modified by website deployment.

| Profile | Default high detail | Maximum high detail | Default low detail | Resolution multiplier |
| --- | ---: | ---: | ---: | ---: |
| Desktop | 250 | 500 | 2,000 | 1 (0.5 above DPR 2) |
| Mobile / constrained device | 100 | 200 | 1,000 | 0.5 |
| Manual recovery | 50 | 100 | 1,000 | 0.5 |

Coarse-pointer devices, Android/iOS (including iPad desktop mode), reported RAM of at most 4 GB, at most four logical processors, or Save-Data select the conservative profile. Missing hardware hints do not imply a weak device. Mobile detection does not depend on iframe width. Limits are startup budgets and slider bounds, not a claim that every device can support the maximum. Returning visitors retain cheaper preferences, including disabled high detail; out-of-budget values such as 1,600 migrate to the new default before map loading. The resolution setting remains available in BlueMap, but expensive saved values are normalized on the next load. With unavailable preference storage, adapted configuration defaults still apply.

The synchronous script intercepts only same-origin GET `/map/settings.json` responses. The underlying generated settings remain the source of map lists, URLs, and other configuration. The ordinary BlueMap custom-script hook runs too late for startup budgeting. Review this integration when upgrading BlueMap, especially its settings loader and preference names.

To align the external BlueMap baseline with the reviewed desktop default, explicitly run the following from the deployed, reviewed checkout. This is separate from website deployment and must not run the renderer or restart Minecraft:

```sh
ssh oracle "sudo python3 /srv/oak/site-repo/scripts/map-defaults.py"
ssh oracle "sudo python3 /srv/oak/site-repo/scripts/map-defaults.py --apply"
```

The first command validates the installed 5.23 configuration and prints proposed values. The second backs up `webapp.conf` and `settings.json` under `/srv/oak/map-profile-backups`, then changes only quality defaults and slider maxima. Both files are updated so a future render will not restore the former baseline. It preserves map lists, custom scripts, and all unrelated configuration. A write failure restores previously replaced files. For an intentional rollback, review the printed backup and restore those public configuration files separately from the website commit.

Run `node --test tests/test_map_profile.js` and `node --check public/map-profile.js` alongside the standard checks. Deployment verifies both map entry points and exact profile assets. After publishing, test exploration on real iPhones: Chromium mobile emulation does not validate iOS memory limits or Safari GPU recovery. `window.oakMapProfile` exposes the chosen budget and context-loss count for debugging; no player data or browser telemetry is uploaded.

## Migration from the initial workspace

The initial working files lived at `C:\Users\fabio\Projects\vostro\output\oak`. The maintained Git checkout was moved from `C:\Users\fabio\Projects\vostro\oak` to `C:\Users\fabio\Projects\oak` on 2026-09-07 UTC. Its Git history and remote were preserved. Production paths are unchanged. Historical `deploy-web.py` and `deploy-chat.py` provisioned containers and are not part of this repository or the recurring deployment workflow. Do not use the old SCP workflow for files managed here; make changes in Git to prevent drift.
