/**
 * Failing where nobody is looking.
 *
 * The companion runs on a phone that is not plugged into anything. When it
 * dies, there is no logcat, no console, and no stack — just an app that closed.
 * Every test here is about turning that into something a user can read out.
 *
 * Also here: the trap that caused it. `useState`'s setter treats a function as
 * an updater and calls it, so storing a component class in state invokes a
 * constructor without `new`. That is the exact shape of the bug that left the
 * QR scanner permanently on "Starting the camera…", and it is checked against
 * the real source because it cannot be caught any other way.
 */

import { strict as assert } from 'node:assert';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { describe, test, beforeEach } from 'node:test';

import {
  crashHistory,
  describe as describeError,
  forgetCrashes,
  installGlobalErrorTrap,
  lastCrash,
  onCrash,
  recordCrash,
  resetGlobalErrorTrap,
} from '../src/lib/crash.ts';

beforeEach(() => {
  forgetCrashes();
  resetGlobalErrorTrap();
});

describe('reading back what went wrong', () => {
  test('an Error keeps its message and its stack', () => {
    const crash = recordCrash(new Error('camera is on fire'), 'scanning');
    assert.equal(crash.message, 'camera is on fire');
    assert.equal(crash.where, 'scanning');
    assert.ok(crash.stack.includes('Error'));
  });

  test('a thrown string is not swallowed', () => {
    assert.equal(recordCrash('nope', 'somewhere').message, 'nope');
  });

  test('a thrown object says what it was, not [object Object]', () => {
    const crash = recordCrash({ code: 'E_NO_CAMERA' }, 'somewhere');
    assert.ok(crash.message.includes('E_NO_CAMERA'));
  });

  test('a rejection with no reason still reports something', () => {
    assert.equal(recordCrash(undefined, 'somewhere').message, 'undefined');
  });

  test('an object that cannot be stringified does not throw in turn', () => {
    const looping = {};
    looping.self = looping;
    assert.doesNotThrow(() => recordCrash(looping, 'somewhere'));
  });

  test('the empty-message Error still names itself', () => {
    // `new Error()` has an empty message; reporting "" tells nobody anything.
    assert.ok(describeError(new Error()).message.length > 0);
  });
});

describe('telling the app about it', () => {
  test('listeners hear about a crash', () => {
    const heard = [];
    onCrash((c) => heard.push(c.message));
    recordCrash(new Error('boom'), 'x');
    assert.deepEqual(heard, ['boom']);
  });

  test('a listener that throws does not stop the others', () => {
    const heard = [];
    onCrash(() => {
      throw new Error('a listener that is itself broken');
    });
    onCrash((c) => heard.push(c.message));
    recordCrash(new Error('boom'), 'x');
    assert.deepEqual(heard, ['boom']);
  });

  test('unsubscribing works', () => {
    const heard = [];
    const off = onCrash((c) => heard.push(c.message));
    off();
    recordCrash(new Error('boom'), 'x');
    assert.deepEqual(heard, []);
  });

  test('history is kept but bounded', () => {
    for (let i = 0; i < 50; i += 1) recordCrash(new Error(`e${i}`), 'x');
    const history = crashHistory();
    assert.ok(history.length <= 20, `kept ${history.length}`);
    assert.equal(history.at(-1).message, 'e49');
    assert.equal(lastCrash().message, 'e49');
  });
});

describe('taking over the global handler', () => {
  test('a fatal error is recorded instead of ending the process', () => {
    let handler;
    const host = { ErrorUtils: { setGlobalHandler: (fn) => (handler = fn) } };
    assert.equal(installGlobalErrorTrap(host), true);

    handler(new Error('fatal thing'), true);

    assert.equal(lastCrash().message, 'fatal thing');
    assert.equal(lastCrash().fatal, true);
  });

  test('the previous handler is left out of fatal errors', () => {
    // Calling it is what terminates the app, which is the whole point of
    // stepping in front of it.
    const seen = [];
    let handler;
    const host = {
      ErrorUtils: {
        getGlobalHandler: () => (e, f) => seen.push([e.message, f]),
        setGlobalHandler: (fn) => (handler = fn),
      },
    };
    installGlobalErrorTrap(host);

    handler(new Error('fatal'), true);
    assert.deepEqual(seen, []);

    handler(new Error('survivable'), false);
    assert.deepEqual(seen, [['survivable', false]]);
  });

  test('installing twice does not stack handlers', () => {
    const host = { ErrorUtils: { setGlobalHandler: () => {} } };
    assert.equal(installGlobalErrorTrap(host), true);
    assert.equal(installGlobalErrorTrap(host), false);
  });

  test('a host without ErrorUtils is survivable, not a crash of its own', () => {
    assert.equal(installGlobalErrorTrap({}), false);
  });
});

/**
 * Comments describe the bug, so a check that reads raw source finds the
 * description and calls it a defect. Strip them first.
 */
function code(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*(\/\/|\*).*$/gm, '');
}

describe('the trap that started this', () => {
  const screens = readdirSync(new URL('../src/screens/', import.meta.url))
    .filter((f) => f.endsWith('.tsx'));

  test('there are screens to check', () => {
    assert.ok(screens.length >= 5, `found ${screens.length}`);
  });

  for (const file of screens) {
    const source = code(
      readFileSync(new URL(`../src/screens/${file}`, import.meta.url), 'utf8'),
    );

    test(`${file} does not put a component into useState`, () => {
      // `setThing(SomeComponent)` calls SomeComponent as an updater. With a
      // class component that throws; with a function component it silently
      // stores whatever it returned. Neither is ever what was meant, and the
      // only safe form is `setThing(() => SomeComponent)`.
      const offences = [...source.matchAll(/set([A-Z]\w*)\(\s*(?!\(\)\s*=>)([\w.]+)\s*(?:as\s+\w+\s*)?\)/g)]
        .filter(([, , value]) => /(^|\.)[A-Z]/.test(value));
      assert.deepEqual(
        offences.map((m) => m[0]),
        [],
        'a capitalised value passed straight to a setState is treated as an updater',
      );
    });

    test(`${file} imports expo-camera statically or not at all`, () => {
      // The lazy import existed to keep native code away from Node. No test
      // imports a screen, so it bought nothing and cost the camera.
      assert.ok(
        !/import\(['"]expo-camera['"]\)/.test(source),
        'dynamic expo-camera import is back',
      );
    });
  }

  test('every camera surface is behind the permission gate', () => {
    // Android has required a runtime grant since 6.0. Mounting a camera
    // without one gets a black rectangle at best and takes the process down
    // at worst, from a thread with no handler on it.
    for (const file of screens) {
      if (file === 'Camera.tsx') continue;
      const source = code(
        readFileSync(new URL(`../src/screens/${file}`, import.meta.url), 'utf8'),
      );
      if (!source.includes('<CameraView')) continue;
      assert.ok(
        source.includes('<CameraGate'),
        `${file} mounts a camera without asking for permission`,
      );
    }
  });
});

describe('knowing which build is on the phone', () => {
  const read = (path) =>
    readFileSync(new URL(path, import.meta.url), 'utf8');

  test('every place the version is written agrees', () => {
    // A version shown in the app that has drifted from the one built is worse
    // than showing none, because it gets believed and the wrong build gets
    // blamed.
    const shown = /VERSION = '([^']+)'/.exec(read('../src/lib/version.ts'))[1];
    assert.equal(JSON.parse(read('../app.json')).expo.version, shown);
    assert.equal(JSON.parse(read('../package.json')).version, shown);
    // android/ is generated by prebuild and not tracked, so it is checked
    // only when it is there. On a clean checkout app.json is the source it
    // would be generated from anyway.
    if (existsSync(new URL('../android/app/build.gradle', import.meta.url))) {
      assert.equal(
        /versionName "([^"]+)"/.exec(read('../android/app/build.gradle'))[1],
        shown,
      );
    }
  });

  test('the pairing screen says it', () => {
    // The screen a phone lands on when it is not paired, which is exactly when
    // someone is asking "did the new build install".
    assert.ok(read('../src/screens/Pair.tsx').includes('{VERSION}'));
  });
});
