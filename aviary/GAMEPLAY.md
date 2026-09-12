# Aviary gameplay

[Index](README.md) · For implementation use [Development](DEVELOPMENT.md).

## Start

1. Join with the supported Java client and accept the server resource pack.
   Recipes unlock on login. If declined, use **Load resource pack** in the menu
   or `/aviary pack`; declining never prevents joining.
2. Craft **Perch** and **Whistle**. `_` below means an empty slot.

| Item | Recipe rows | Ingredients |
| --- | --- | --- |
| Perch | `CLC / _S_ / PPP` | C: copper ingot; L: leather; S: stick; P: any planks |
| Whistle | `BC / _S` | B: bone; C: copper ingot; S: string |

3. Hold Perch in the main hand and use the top of a solid block in the Overworld.
   Its preview marks direction, resting volume and a dismount point. Placement
   needs safe space, not a fixed platform size. It opens the destination menu.
4. Use Whistle, choose a destination and select **Call bird**. Stay nearby, then
   use the saddle to board. Calling alone does not move or protect the player.

## Travel

- **Travel: Full/Quick** and **Camera: Follow/Free** persist per player.
  Free retains the player's first-person/F5 choice; the server cannot set F5.
- Hold Shift for about 0.4 seconds while riding to request a safe shortened trip.
- Before boarding, use the whistle's **Cancel** action or walk away. Damage,
  disconnects and the boarding timeout also cancel an unboarded call.
- If occupied, **Wait for a bird** queues the call for up to one minute.
- Without a departure perch, field pickup can find nearby loaded open-air ground.
  Stand on the ground; the destination must still be known and accessible.
- **Fly with a friend** requires two nearby Java players at the same departure
  perch, pack acceptance, destination access and two available flight slots.
  The friend accepts explicitly. Each gets a bird; takeoff and landing are
  staggered, not a close formation. Field pickup does not support group departure.

## Destinations and birds

New perches are private. Use **Access** to share them or **Guests** to invite
online players. Public destinations may require visiting before appearing in the
whistle, depending on network policy. A public hub bypasses discovery, never
private access. Favorites and discovered destinations belong to the player profile.

Use **Rename** and **Appearance** for destination/bird names, oak/spruce/birch
wood and sixteen cloth colors. Bird plumage is stable per owner; it is not a
separate player-selectable setting. Offer cooked chicken to a waiting bird for a
short response. It consumes one item in survival, has an eight-second cooldown
and grants no travel advantage. Use another item or an empty hand to board.

## Move, recover or remove

- **Move → Pack perch** needs a free inventory slot. Place the returned kit at
  the new support; identity, guests and favorites remain intact.
- If the support breaks or a kit is lost, craft a new Perch and use
  **Whistle → Packed perches → destination → Recover kit**.
- **Remove** requires a separate confirmation and an idle destination. Refunds
  depend on the stored kit state; removal does not duplicate lost kits.
- Legacy destinations remain usable. Placing a Perch within six blocks of an
  owned legacy destination attaches that address to the new support. Operators
  can attach destinations they administer. This explicitly moves its landing
  position while preserving identity, name and access.

## Compatibility commands

| Command | Purpose |
| --- | --- |
| `/aviary` | Open destinations |
| `/aviary fly home` | Travel to an accessible destination |
| `/aviary skip` | Request a safe shortened route |
| `/aviary pack` | Request the resource pack |
| `/aviary claim home` | Register a legacy private destination |
| `/aviary name home Home` | Rename it |
| `/aviary share home true` or `false` | Set public/private access |
| `/aviary unclaim home` | Remove an owned registration |
| `/aviary port add spawn` or `port remove spawn` | Administrator: manage legacy registrations |
| `/aviary status` | Administrator: inspect service state |

Access controls travel, not land ownership. Placement observes vanilla build
permissions and spawn protection; third-party land-claim integration is not provided.
