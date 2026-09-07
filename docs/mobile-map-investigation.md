# iPhone map reload investigation

Investigated on 2026-09-07 using read-only production inspection, the deployed BlueMap JavaScript bundle, website source, and Nginx access-log aggregates. No production configuration or runtime data was changed.

## Finding

The leading hypothesis is client-side memory/GPU pressure causing Safari to terminate and restore the page. Repeated document requests are confirmed, but the process termination and its exact cause are not: no device crash report, Safari memory trace, or controlled reduced-quality comparison was available. The user reproduced the issue on an iPhone XS with iOS 18 and an iPhone 14 with iOS 26.

## Evidence

- The deployed BlueMap version is 5.23. Both `/map/settings.json` and `/srv/oak/map-test/config/webapp.conf` specify a high-detail default and maximum distance of 1,600 blocks. Low-detail default is 2,000; resolution multiplier is 1. The upstream documented high-detail fallback is 100 blocks. Oak therefore uses 16 times that radius; this comparison does not imply a measured 256-fold memory increase.
- Between 02:58 and 02:59 UTC (23:58 and 23:59 on September 6 in Brasilia), iPhone requests included seven top-level document loads, 8,557 requests overall and approximately 185.6 MB of response body bytes. Top-level loads occurred at 02:58:31, :40, :44, :56, 02:59:00, :10 and :20. Logs cannot distinguish automatic recovery from manual refresh.
- The inspected 12-hour log window contained 10,069 iPhone requests and approximately 227.3 MB of response bodies. Status totals: 7,134 HTTP 200; 836 HTTP 304; 2,099 HTTP 404; no iPhone HTTP 5xx. The 404s were map tile requests. Their underlying coverage cause was not established; they are a secondary issue worth checking, not evidence of a document reload mechanism.
- The web container was running, with `OOMKilled=false` and a start time of September 6 at 21:58 UTC. At inspection the VM reported about 7.2 GB available memory. These observations do not establish historical memory usage, but do not suggest a current server-wide memory incident.
- `public/app.js` contains no document reload or iframe replacement. SSE reconnection updates status text, and status rendering updates player/chat nodes without rebuilding the map.
- The deployed BlueMap bundle has one `location.reload()` call, in its explicit `resetSettings()` action. No automatic page reload loop was identified.
- BlueMap sets renderer pixel ratio to `window.devicePixelRatio * superSampling`, uses antialiasing and `preserveDrawingBuffer`, and resizes its renderer on window resize. At DPR 3 and multiplier 1, the drawing buffer has nine times the pixel area of the CSS viewport. This is pixel area, not a measured total memory multiplier.
- The embedded mobile map uses `height: 58dvh`. Browser toolbar expansion/collapse can change its viewport height and trigger renderer resizing. This is a plausible amplifier, not a demonstrated cause on these devices.
- BlueMap restores `bluemap-hiresViewDistance`, `bluemap-lowresViewDistance`, and `bluemap-superSampling` from localStorage after applying server defaults. Changing server defaults alone can leave returning visitors with the old expensive settings.
- BlueMap loads configured custom scripts after settings restoration and initial map selection. A mobile resource budget should be applied before initial tile loading; a late script is not sufficient to guarantee a lighter startup.

## Recommended controlled test and remediation

1. Compare the same exploration route on each affected iPhone with high detail at 100–200 blocks and resolution multiplier 0.5. Keep low detail initially at 2,000 to isolate the primary changes. Verify effective values, including saved browser preferences. These are proposed starting values, not a validated device budget.
2. Compare the embedded map with `/map/` opened directly. Use only one active map tab to avoid adding a second WebGL scene to the experiment. This helps isolate iframe/viewport effects; standalone mode is not assumed to fix the high-detail load.
3. If the lower budget resolves the reloads, implement an early mobile quality profile for both entry points, with deliberate handling of older saved preferences. Preserve the desktop experience. Use stable mobile iframe sizing (for example `svh`) to avoid toolbar-driven resizing, then validate orientation changes and keyboard behavior.
4. If failures persist, collect a Safari Web Inspector timeline or device WebContent/GPU termination report. Record document loads and `webglcontextlost` events, understanding that abrupt process termination may prevent JavaScript telemetry from being sent.
5. Check tile 404 coverage separately. Do not regenerate maps or modify Minecraft runtime state as part of this investigation.

Any implementation must be reviewed and tested before deployment. The external map webapp/configuration is not managed by the ordinary website deployer; directly editing generated settings alone is also liable to be overwritten because `update-settings-file` is enabled.

## Sources

- [BlueMap webapp configuration](https://bluemap.bluecolored.de/wiki/configs/Webapp.html): supported quality settings and defaults.
- [WebKit issue 267391](https://bugs.webkit.org/show_bug.cgi?id=267391): reported iPhone WebGL memory growth and page reloads.
- [WebKit issue 218168](https://bugs.webkit.org/show_bug.cgi?id=218168): historical iframe/WebGL process termination and Safari recovery behavior.
- [WebKit issue 219780](https://bugs.webkit.org/show_bug.cgi?id=219780): historical canvas-resize memory issue. This is background evidence, not proof that the same bug affects iOS 18 or 26.
- [WebKit canvas debugging](https://webkit.org/blog/8452/canvas-debugging/): canvas inspection and memory tooling.

Raw access logs, IP addresses, player data, and generated map assets are intentionally excluded from this report.

## Implemented mitigation

The follow-up implementation sets desktop defaults to 250 high-detail blocks and mobile/constrained devices to 100, with a 0.5 resolution multiplier for the latter. The early profile migrates expensive saved settings before map loading, while retaining cheaper choices. Nginx injects the versioned website script before BlueMap on both map entry points without editing generated assets. Mobile iframe and standalone sizing use stable viewport units. Expanding the map uses the same tab to avoid intentionally creating a second active scene. Recoverable context loss shows a manual low-quality retry link, never an automatic reload loop.

The separate reviewed `scripts/map-defaults.py` command aligns the external configuration and generated settings with the desktop baseline, with public configuration backups and restoration on write failure. It does not render tiles or restart services. See `deploy/README.md` for the profile table and procedure.

Validation used Chromium with mobile emulation and the real deployed BlueMap 5.23 bundle/map resources, serving candidate profile assets locally through request routing. Both 100-block mobile and 250-block desktop startup settings were observed in the actual map viewer, with no JavaScript page errors. At a 390-by-844 CSS viewport with DPR 3, the canvas changed from 1170-by-2532 to 585-by-1266 pixels, a 75% reduction in drawing-buffer pixel area. Forced WebGL context loss displayed the manual recovery control. Short network samples were not comparable enough to establish bandwidth or total-memory savings. These checks do not reproduce iPhone hardware or prove that Safari's process termination is resolved; real-device exploration remains the final validation.
