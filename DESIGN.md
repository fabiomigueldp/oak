# Oak design

Oak is a quiet companion for players checking the world and chatting from a desktop beside the game or from a phone. Preserve the existing dark forest palette to keep the map comfortable in this setting. Use restrained color, with pale green for the brand, primary send action, and live state.

Tokens in public/style.css are authoritative: background oklch(21% .014 150), panel oklch(24% .014 150), text oklch(93% .014 95), muted oklch(72% .017 125), accent oklch(82% .067 115), and line oklch(34% .015 140).

Use system-ui typography, subtle one-pixel borders, and visible keyboard focus. The compact header contains the oak tree mark, connection state, and copy-address action. No promotional hero or operational footer. Map controls belong in its toolbar; contextual help stays collapsed. Preserve player-relevant warnings.

The map and conversation share a two-column workspace on desktop and stack at 850px. Desktop height follows the viewport with a minimum for usable chat; mobile map sizing uses stable viewport units, with a direct conversation anchor. Controls have at least 44px touch targets and mobile form text is 16px to avoid focus zoom. Chat history scrolls independently and accepts keyboard focus. Enter submits unless an input method is composing text. The server accepts single-line messages.

Keep recovery feedback compact, readable, and actionable without automatic page reloads or decorative animation. Untrusted player names and chat messages must use textContent.
