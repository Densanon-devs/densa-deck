# Densa Deck v0.6.0

**Your deck's numbers will move when you update.** The simulator used to
ignore what cards actually do — so it was understating most decks. See
"Why your results changed" below before you compare against old runs.

## Fixed: card downloads were broken

Scryfall changed their bulk-data API and every card download failed with
`Failed to parse card data: 'download_uri'`. A fresh install couldn't get
card data at all; an existing install could never update, so no new sets and
no ban-list refresh.

Fixed, and hardened: the downloader now detects the file format instead of
assuming it, so the next change on Scryfall's side degrades to a clear
message rather than a crash.

## The simulator now resolves what cards do

Previously a spell that resolved went to the battlefield or graveyard and
nothing else happened. Cultivate never fetched a land. Divination never drew
a card. Sol Ring produced one mana.

34 effect families are now modelled — ramp, draw, tutors, treasure, tokens,
rituals, +1/+1 counters, proliferate, cost reduction, delve, convoke,
affinity, cascade, self-mill, reanimation, extra turns, and more. Run
`densa-deck coverage` to see exactly what is and isn't modelled against your
own card database.

## Colour is real now

Mana used to be a single number, so a five-colour pile played exactly like a
mono deck. Sources now carry the colours they actually produce, and paying a
cost is solved properly: three lands that each tap only for white cannot cast
a {W}{U}{B} spell, however many of them you have.

Fetchlands were the worst case — Scryfall lists no produced mana for them
because they sacrifice rather than tap, so they were being counted as
colourless. They now read their colours from the land types they search for.
Check lands and fast lands look at the board instead of being assumed tapped.

## New: colour-weighted mana curve

An ordinary curve tells you how many three-drops you have. It can't tell you
those three-drops are {B}{B}{B} and you're on nine black sources.

Every goldfish and gauntlet run now reports, per turn and per colour, what
your cards at that cost demand, how many sources were actually in play, and
what share of games could pay for them — plus your hardest-to-cast cards and
a colour-screw rate measured separately from mana screw.

It asks "could the board have paid for this card", whether or not you drew
it, which separates your mana base from draw luck.

Shown in the CLI, the desktop app, exports, and to AI clients over MCP.

## New: official rulings (optional)

Wizards' per-card rulings, as an opt-in ~5 MB download. Not part of card data,
never fetched automatically, removable in one click.

- Desktop: Settings → Official rulings
- CLI: `densa-deck rulings {status|download|show "Card Name"|remove}`

Free tier. Rulings © Wizards of the Coast, via Scryfall.

## Other fixes

- **Stax opponents now actually tax you.** The Stax archetype's mana tax was
  computed and thrown away, so its defining trait did nothing. Gauntlet
  results against Stax will be harder.
- **Correct starting life per format.** Brawl was being played at 40 life
  (it's 25), and Oathbreaker and Duel Commander likewise (both 20).
- **MCP server fixed for new installs.** The `mcp` dependency was unbounded,
  so a fresh install pulled a 2.0 release that the server can't start on.
- **~2x faster.** 1000 goldfish games run in about 1.4 seconds including the
  new colour report — quicker than 0.5.0 was while doing far more.

## Why your results changed

If you saved numbers from 0.5.0, expect them to move:

- **Kill rates go up.** Ramp, draw, tokens and counters now do their jobs, so
  decks perform closer to how they really play.
- **Commander cast rates go down on multicolour decks.** The old model
  couldn't see colour. A four-colour commander really is that hard to cast.
- **Gauntlet results against Stax get worse**, because the tax now applies.

These aren't regressions. The old numbers were flattering.

## Under the hood

- Lint is clean and enforced, and CI now runs the suite on every change —
  neither was true before, which is how the card-download break shipped.
- 786 tests, up from 582.
