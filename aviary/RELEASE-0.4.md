# Aviary 0.4.0

## Perches and whistles

Perch and Whistle are reusable gameplay objects backed by native item IDs and
resource-pack models. Crafting recipes are provided by the server data pack.
The Java client remains unmodified. The Bedrock gateway remains available;
Aviary's custom item interactions are restricted to Java players.

A physical perch is separate from its destination address and bird landing
position. A placed perch is private by default. Its owner can rename the
destination and bird, invite online players, change access, choose wood and cloth,
or pack it for relocation through native in-game menus. Wood styles are oak,
spruce and birch; cloth supports the sixteen native dye colors.

Moving preserves the destination identity and player favorites. Stored ownership
is authoritative; a renamed item or copied client field cannot confer access.
Existing destinations remain registered and continue to work. Legacy shared
destinations remain known, so upgrading does not revoke existing routes.

The whistle opens destinations. Discovery can require visiting public perches,
while invited players can access private destinations. Public hubs bypass the
visit requirement. Favorites and discovered destinations belong to the player's
server profile, independently of the item in their inventory.

## Travel and space

Call a bird with the whistle, then use its saddle to board. Until boarding,
walking away, cancelling, disconnecting or taking damage cancels the call without
teleporting or protecting the player. The durable recovery journal and protection
begin only after explicit boarding. Full/Quick and Follow/Free preferences persist.

Field pickup finds nearby loaded outdoor ground; no origin registration is created.
A known placed destination is still required. Pickup needs a nearby loaded,
open-air landing spot and never changes terrain. Queued calls are bounded, expire
after a minute, and can be cancelled with the whistle.

Two nearby Java players can choose **Fly with a friend**. The invited player must
accept. Both riders need destination access and the resource pack; two flight
slots must be available. Birds take off separately and reserve the landing point
in order. This is coordinated transport, not a close airborne formation.

The old 3-by-3 floor and 7-by-7-by-24 empty-column requirements are removed. A
physical perch rests on a safe solid support and needs a safe adjacent dismount.
Placement shows its heading, occupied resting space and dismount point. Block
queries always run against current loaded terrain; only resting geometry is cached.
Five conservative pose volumes follow the authored body, wings, tail and feet.
Planning uses geometry-only rigs; actual motion checks swept pose volumes again.
Candidate planning and arrival rechecks yield across ticks with a shared 2 ms
cooperative deadline. One pose query or scene construction completes before
yielding; the budget is not a hard real-time guarantee. No world reads run on
background threads and blocked attempts never load an entire long route.
Wings remain folded near the support and open as clearance increases. Full route
approaches are checked when a bird is called, not promised by a placement preview.

After landing, control returns immediately and the bird pauses, then departs along
a checked path when one is available. Quiet seeded glances and head motion give
birds individual acting. Names appear at boarding; plumage is stable per owner
(original, ash or amber). Offering cooked chicken to a waiting bird produces a
short reaction, consumes one item in survival and has an eight-second cooldown.
Feeding grants no health, speed or access advantage and is never required.

Broken or unsafe loaded supports deactivate their addresses in one batched durable
update. Their owners can recover them through **Packed perches**, using a newly
crafted kit. Unloaded supports are not treated as broken. Placement respects
adventure-mode building restrictions and vanilla spawn protection.

Owners can remove an unused destination through a separate native confirmation.
Removal checks ownership, active flights and the current revision again; valid
kit refunds happen once after durable deletion. Missing inactive kits are not
refunded, and duplicated old pointers cannot recreate a removed destination.

## Oak administration

Oak's Aviary view includes physical and legacy destinations, active journeys,
owner and invited-player identities, map location, and timestamped landing-area
inspection. Physical anchor states distinguish placed, packed for relocation,
missing support, and unloaded terrain. A placed anchor does not imply that the
full flight corridor has been validated.

The network editor controls field pickup, public-destination discovery, owned
perch limits (1–16), simultaneous flights (1–4), and the transition threshold
(100–500 blocks). Reducing an ownership limit preserves existing destinations.
Network changes govern future requests; active journeys are not cancelled.

Destination edits retain name, access, and preferred departure/arrival headings.
Physical perches additionally expose wood style, cloth color, bird name, and
public-hub discovery. Owner and guest changes remain in-game interactions.
Coordinates, identity, runtime enablement, resource-pack configuration, and
arbitrary commands are not accepted by the administrative edit contract.

Both network and destination edits use the existing authenticated `aviary_edit`
job with administrator permissions and an exact current revision. Busy
destinations cannot be edited. A network update does not rewrite their anchors.
The UI keeps pending edits separate from observed server state, prevents duplicate
submission, blocks stale revisions, and reads the server after successful jobs.
Landing inspection is read-only and does not generate terrain. A policy revision
invalidates previous inspection results; actual terrain can still change after
an inspection, so departure retains its own collision checks.

## Runtime compatibility

Policy, anchors, invitations, discovered destinations, and preferences remain in
`config/oak-aviary` outside Git. Resource-pack and mod artifacts must be installed
together with the matching Oak Control backend. Website deployment alone does
not upgrade the Minecraft mod. No new public listener, firewall rule, or client
mod is required.

## Focused checks

`python tests/test_aviary.py` checks administrative permissions, strict schemas,
integer/boolean distinctions, finite limits, Unicode label bounds, stale edits,
idempotent receipts, busy destinations, and preservation of anchor identity.
`node --test tests/test_aviary_web.mjs` checks draft authority, old-schema fallback,
physical-state labels, stale edits, unavailable state and active-flight policy
behavior. These checks use mocked delivery and do not message real players.

These tests do not render a Minecraft client or establish visual animation
quality. Client-side appearance and motion still require in-game evaluation.
