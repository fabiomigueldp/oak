# Oak Aviary

Server-side condor transport using crafted **Perch** and **Whistle** items,
native Minecraft menus and a server-delivered resource pack. Java players use
an unmodified client. Pack acceptance is required for travel, not for joining.
Bedrock login remains available; Aviary interactions are Java-only.

## Read by task

This index is the starting context. Read one relevant guide, then its named
source files if implementation details are needed. Do not load every guide,
release note or model file by default.

| Task | Read | Covers |
| --- | --- | --- |
| Use or explain the feature | [Gameplay](GAMEPLAY.md) | Recipes, boarding, access, moving, friends, commands |
| Change behavior, art or Oak UI | [Development](DEVELOPMENT.md) | Source map, state, flight lifecycle, rendering, contracts, checks |
| Inspect, diagnose, deploy or recover | [Operations](OPERATIONS.md) | Live state, socket requests, releases, rollback, crash triage |

## Boundaries

- Pinned build: Minecraft `26.3-rc-1`, Fabric loader `0.19.5`, Java 25.
  [Metadata](fabric.mod.json) and [build script](build.py) define compatibility;
  query the installation before assuming it matches the checkout.
- Overworld only. New destinations need a safe support and adjacent dismount;
  the flight also needs a checked approach. No prescribed 9x9 platform.
- State is server-owned. Items do not confer ownership. Existing destinations
  and recovery journals must survive upgrades.
- English in-game copy; Brazilian Portuguese in the Oak administration UI.
- Server tests verify behavior and geometry, not perceived client smoothness.
  Client memory and multi-viewer frame/network cost are not benchmarked.

## Historical and specialist references

[0.4 feature record](RELEASE-0.4.md), [0.3 release](RELEASE-0.3.md),
[original feasibility study](../docs/aviary-cinematic-design.md),
[flight direction](art/FLIGHT_DIRECTION.md), [item authoring](art/PERCH_OBJECTS.md),
and [concept prompts](art/concepts/README.md). Consult these for a specific design
decision; the three guides above describe current behavior.
