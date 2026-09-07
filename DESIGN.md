# Oak design

The existing dashboard uses a dark forest palette with a restrained pale-green accent. Tokens in public/style.css are authoritative: background oklch(21% .014 150), panel oklch(24% .014 150), text oklch(93% .014 95), muted oklch(68% .017 125), accent oklch(82% .067 115), and line oklch(34% .015 140).

Use system-ui typography, subtle one-pixel borders, and visible keyboard focus. The map and conversation share a two-column workspace on desktop and stack below 850px. Keep mobile map sizing stable when browser toolbars change. Recovery feedback should be compact, readable, and actionable without automatic page reloads or decorative animation.
