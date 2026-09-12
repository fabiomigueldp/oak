# Player activity

The public `/activity/` timeline opens on the preceding 24 hours in Brasília time
(UTC−03), keeping recent sessions visible across midnight. A seven-day ribbon
provides direct day selection and week navigation; `7 dias` expands the visible
week, and `Agora` returns to the live window. There are no search, sort or date
form controls. Recent players appear first automatically. Dashboard links open
this page without loading the map, chat, chart libraries, WebGL or external fonts.
Select a bar for boundaries/duration or a player for their sessions. Nearby sessions may
share a striped display group, but their pauses never count toward duration.

Player identities, visible focus, date/session arrow keys, Escape dismissal and
textual details support mobile and keyboard use. The whole chart fits the mobile
viewport with fewer time ticks, and tapping a player opens narrow sessions in
text. Date/view are shareable as `?date=YYYY-MM-DD&period=week`. Collection notes
stay behind the information disclosure; unknown coverage remains visible.

## Accuracy and persistence

`server/collect.py` reuses its existing RCON `list` observation, roughly every five
seconds. No extra game commands run per visitor. History starts when installed;
there is no automatic historical log import. An observed empty list means nobody
is connected. Failed/incomplete replies and gaps over 20 seconds mean unknown.
Unknown intervals are hatched and excluded from duration. Recovery opens a new
session; short restarts preserve continuity only within the observation tolerance.
Unknown endings are last-seen times, not confirmed departures. Single observations
remain visible with zero duration; sessions between samples may be missed.

`server/presence.py` stores names, UUIDs where available, validated texture hashes,
sessions and coverage. Usercache and optional bounded telemetry enrich identity;
no coordinates, chat, profile signatures, IPs, raw logs or credentials are public.
SQLite commits/publication run every 30 seconds or on transitions. A transactional
publication journal recovers only pending files on restart. Atomic daily-file
replacement precedes index publication. No missing time is extrapolated; browser
live indicators expire after 90 seconds.

Systemd `StateDirectory=oak-presence`, mode 0700, creates `/var/lib/oak-presence/`
for `history.sqlite3` and `skin-metadata.json`, outside Git and the web root.
Generated public files live under `/srv/oak/web/data/activity/`. Deployments and
rollbacks preserve these runtime locations.

## Contract and budgets

`index.json`: `version:1`, integer UTC `updated`, `observedSince`, `sampleSeconds`,
`gapSeconds`, `retentionDays`, `collecting`, `latestDay`, ordered UTC `days`.
Each `YYYY-MM-DD.json`: `version`, `day`, UTC `start`, `end`, `updated`, `truncated`,
`coverage:[[start,end]]`, and `players:[{id,uuid,name,skin,sessions}]`.
Sessions contain stable `id`, clipped `start`/`end`, `open`, `continuedBefore`,
`continuesAfter`, and `endReason` (`left`, `unknown`, or null).

The UI combines the UTC shards intersecting the last 24 hours, two for a Brasília
calendar day or eight for a week. It rejoins
fragments without double counting, intersects sessions with observed coverage,
caps time at publication, and unions each player's intervals for totals. Exact
concurrency processes ends before starts at equal times. The encounter strip uses
the maximum simultaneous count per time bucket.

Retention is 90 UTC days. Daily caps: 100 observed players, 256 identities,
10,000 sessions and 2,000 coverage intervals. Truncation produces an explicit
incomplete-data notice. Browser caps: 3 MB per body, 24 cached shards, 40 initial
players, 120 display groups per lane, 30 details per expansion. One shared SVG
path draws gaps across lanes. There is no frame loop. Refresh pauses in hidden
tabs; historical files revalidate within five minutes and on explicit navigation
because identity resolution can revise past records.

Local synthetic timing for one simulated hour averaged 1.1 ms per observation
with 12 players and 3.9 ms with 100; corresponding current JSON was 2.7 KB and
20.1 KB. This excludes RCON/network and is not a production latency guarantee.

## Skins

`server/activity_skins.py` uses one optional worker, a 100-item queue and 512-file
cache. Known UUIDs resolve through the fixed Mojang profile endpoint; textures use
the fixed Minecraft host. No visitor-triggered lookups or redirects. Limits are
32 KB profile JSON, 64 KB PNG, and validated 64×32/64×64 PNG headers. Successful
profile lookups last seven days; failures retry after six hours. Assets expire
after 90 days or cache pressure. Bedrock skins use available telemetry, otherwise
initials and stable colors remain.

Only cached hashes are published. Same-origin PNGs at
`/data/activity/skins/<hash>.png` have seven-day immutable cache headers. The
browser loads at most three at once, decodes each once, crops an 8×8 face/overlay
and samples an 8×8 torso for color. Asset failures never block the timeline.

## Verification and operation

```sh
python scripts/check.py
node --check public/app.js
node --check public/activity.js
node --test tests/test_activity_model.mjs
python scripts/preview-activity.py --port 8081
```

The loopback-only preview prints its URL and labels its synthetic players,
sessions and skins. It reads no runtime player data and writes no generated
fixtures. Tests use temporary state and mocked network/RCON; model tests include
an independent per-second accounting oracle, midnight, gaps, duplicates, point
observations, freshness, filtering and bounds.

Use the exact-commit [website release](../deploy/README.md). It installs the page,
collector modules and systemd state directory, restarts affected website services,
and checks exact activity assets, JavaScript module MIME, fresh index and daily
shard shape. It does not restart Minecraft or import historical logs.
