/**
 * Letting the app talk to the desktop at all.
 *
 * Android 9 made `cleartextTrafficPermitted` default to **false**. Expo's
 * template puts `android:usesCleartextTraffic="true"` in the DEBUG manifest
 * only, so a debug build can reach a dev server — and a release build cannot
 * make a single plain-HTTP request.
 *
 * This app talks plain HTTP on purpose. The desktop is reached at a Tailscale
 * 100.64/10 address or a private LAN one; WireGuard already encrypts the
 * tunnel hop, and the alternative — a real certificate via `tailscale serve`
 * — publishes this machine's name to the public Certificate Transparency log
 * permanently. That trade was made deliberately and is not being revisited.
 *
 * So the platform blocked every request, instantly and with no error worth
 * the name. Pairing still worked (it only parses a URL and writes it down),
 * the collection still opened (it reads the local mirror), and every sync
 * failed the moment it started — which reads as "Offline" forever while
 * sitting next to the PC on the same Wi-Fi.
 *
 * Android's network security config cannot express "private ranges only": it
 * matches domains and literal hosts, not CIDR blocks, and the desktop's
 * address is a DHCP lease. So the permission is granted app-wide here and the
 * restriction is enforced in `src/lib/hosts.ts`, where it can be tested —
 * the app refuses to send a token anywhere that is not a tunnel, LAN or
 * loopback address.
 */

const { withAndroidManifest } = require('expo/config-plugins');

module.exports = function withCleartext(config) {
  return withAndroidManifest(config, (mod) => {
    const application = mod.modResults.manifest?.application?.[0];
    if (!application) {
      throw new Error(
        'with-cleartext: no <application> in the manifest. Rather than ship ' +
          'a build that silently cannot reach the desktop, this fails here.',
      );
    }
    application.$['android:usesCleartextTraffic'] = 'true';
    return mod;
  });
};
