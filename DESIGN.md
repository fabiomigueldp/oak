# Oak design

Oak is a quiet companion for players checking the world and chatting from a desktop beside the game or from a phone. Preserve the existing dark forest palette to keep the map comfortable in this setting. Use restrained color, with pale green for the brand, primary send action, and live state.

Tokens in public/style.css are authoritative: background oklch(21% .014 150), panel oklch(24% .014 150), text oklch(93% .014 95), muted oklch(72% .017 125), accent oklch(82% .067 115), and line oklch(34% .015 140).

Use system-ui typography, subtle one-pixel borders, and visible keyboard focus. The compact header contains the oak tree mark, connection state, and copy-address action. No promotional hero or operational footer. Map controls belong in its toolbar; contextual help stays collapsed. Preserve player-relevant warnings.

The map and conversation share a two-column workspace on desktop and stack at 850px. Desktop height follows the viewport with a minimum for usable chat; mobile map sizing uses stable viewport units, with a direct conversation anchor. Controls have at least 44px touch targets and mobile form text is 16px to avoid focus zoom. Chat history scrolls independently and accepts keyboard focus. Enter submits unless an input method is composing text. The server accepts single-line messages.

Keep recovery feedback compact, readable, and actionable without automatic page reloads or decorative animation. Untrusted player names and chat messages must use textContent.

Oak Control uses the same forest tokens in `public/admin/style.css`, with a pale
day theme for daytime administration. A quiet persistent sidebar locates seven
workspaces, an immersive map anchors the overview, and a contextual drawer keeps
details near their source. Native dialogs explain consequential changes before
execution. The persistent operation bar shows work across navigation. Typography
and generous spacing establish hierarchy; evidence labels distinguish current,
stale, simulated and historically sampled data. Do not substitute illustrative
metrics for unavailable live measurements.

Scrollbars use thin native tracks, transparent gutters and muted forest thumbs
across the dashboard, chat and map. Keep scrolling available where content needs
it; reduce excess spacing on short screens before introducing another scroll area.
Search has one scrolling results region; modal overlays contain their scrolling.
Selects retain native semantics and keyboard interaction. Browsers supporting
`appearance: base-select` also receive themed pickers, option states and chevrons;
other browsers retain their native picker with the document color scheme.
Respect forced colors, reduced motion and 44px touch options.

Page titles name the workspace directly. Omit repeated brand eyebrows, generic
taglines and descriptions that merely restate the available controls. Keep copy
that explains consequences, verification evidence, freshness or recovery. Search
results separate the name from the category in aligned columns. Technical job
payloads are available through a disclosure instead of dominating the result.
