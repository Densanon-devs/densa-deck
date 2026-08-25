/**
 * Identifiers, on a runtime with no crypto.
 *
 * These are identity, not secrets: which device wrote a sync event, which deck
 * a rename applies to, whether a replayed push is the one already applied.
 * They have to be unique. They do not have to be unguessable, which is why a
 * missing CSPRNG is survivable — but uniqueness cannot then rest on
 * `Math.random` alone, and that is what these check.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import { uuid } from '../src/lib/uuid.ts';

const SHAPE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('shape', () => {
  test('it looks like a v4 UUID', () => {
    // Not cosmetic: the desktop stores these as text keys and the sync log
    // round-trips them. Anything that is not this shape is a new format.
    for (let i = 0; i < 200; i += 1) assert.match(uuid(), SHAPE);
  });

  test('the version and variant nibbles are right', () => {
    const id = uuid();
    assert.equal(id[14], '4');
    assert.ok('89ab'.includes(id[19]));
  });
});

describe('uniqueness', () => {
  test('a hundred thousand draws do not repeat', () => {
    const seen = new Set();
    for (let i = 0; i < 100_000; i += 1) seen.add(uuid());
    assert.equal(seen.size, 100_000);
  });

  test('ids made in the same millisecond still differ', () => {
    // The clock cannot separate these, so the counter has to.
    const frozen = () => 1_700_000_000_000;
    const seen = new Set();
    for (let i = 0; i < 4000; i += 1) seen.add(uuid(frozen));
    assert.equal(seen.size, 4000);
  });

  test('they differ even if Math.random is stuck', () => {
    // The failure this guards against is a PRNG that has not been seeded, or
    // has been seeded identically on two fresh installs.
    const real = Math.random;
    Math.random = () => 0.5;
    try {
      const seen = new Set();
      for (let i = 0; i < 3000; i += 1) seen.add(uuid());
      assert.equal(seen.size, 3000);
    } finally {
      Math.random = real;
    }
  });

  test('the clock going backwards does not produce a repeat', () => {
    // Phones adjust their clocks. An id that collides after an NTP step would
    // silently merge two devices' events in the sync log.
    let t = 1_700_000_000_000;
    const jumpy = () => (t -= 1000);
    const seen = new Set();
    for (let i = 0; i < 5000; i += 1) seen.add(uuid(jumpy));
    assert.equal(seen.size, 5000);
  });
});

describe('the clock it carries', () => {
  test('a later id sorts after an earlier one', () => {
    // Not relied on anywhere, but free, and it makes a raw table of ids
    // readable in the order things happened.
    const early = uuid(() => 1_700_000_000_000);
    const late = uuid(() => 1_800_000_000_000);
    assert.ok(early < late, `${early} !< ${late}`);
  });
});
