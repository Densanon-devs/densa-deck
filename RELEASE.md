# Releasing Densa Deck

Same pattern as Table of War, on Densa Deck's **own** repo
(`densanon-devs/densa-deck`, public):

- Installs do **one anonymous GET** of a static JSON on the toolkit site, compare it to their own
  version, and if a newer build exists show a banner with a **manual** download link. Nothing
  installs itself. The check is **on by default** with an off switch (desktop: Settings → App
  updates; phone: Settings → App updates). Off means no request at all.
- Binaries are **GitHub Releases on `densanon-devs/densa-deck`**, linked by **pinned tag**
  (`/releases/download/<tag>/<file>`), never `/releases/latest/`. `latest` moves the moment
  anything is released, so a feed that hasn't been bumped yet would point at a file that no longer
  exists there.
- Two products, released independently:

| Product | Tag | Feed (in `densanon-devs/densanon-toolkit`) | Checked by |
|---|---|---|---|
| Desktop (Windows) | `vX.Y.Z` | `densa-deck-version.json` | `AppApi.check_for_updates` |
| Android companion | `companion-vX.Y.Z` | `densa-deck-mobile-version.json` | `AppState.checkAppUpdate` |

Feeds are served at `https://toolkit.densanon.com/<feed>`; Pages redeploys ~1 min after a push.

**Build one thing at a time.** Other sessions on this box run training jobs, RAG builds and Rust
compiles; the Android build needs ~5-6 GB of commit headroom and has been killed three times for
want of it. Check first:

```powershell
$os = Get-CimInstance Win32_OperatingSystem
"CPU $((Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average)% | commit headroom $([int]($os.FreeVirtualMemory/1024)) MB"
```

---

## Desktop

### 1. Versions -- all three together

| File | Field |
|---|---|
| `pyproject.toml` | `version` |
| `src/densa_deck/__init__.py` | `__version__` (what the update check compares) |
| `packaging/installer.iss` | `#define AppVersion` (names the Setup exe and registers the version) |

`tests/test_version_parity.py` fails if they disagree. The combo-fetch User-Agent derives from
`__version__`; the same test fails on any hardcoded `DensaDeck/x.y.z`.

### 2. Test

```bash
PYTHONPATH=src py -3.13 -m pytest -q tests/
```

### 3. Build -- close the app first

```bash
powershell -Command "Stop-Process -Name densa-deck -Force -ErrorAction SilentlyContinue"
powershell -ExecutionPolicy Bypass -File packaging/build_installer.ps1
```

That runs `scripts/build_desktop.py` (refuses to build without qrcode / mcp / winrt OCR, then
smoke-tests the frozen exe: `--help`, `analyst show`, `mcp selftest`) and then Inno Setup. Outputs:

- `dist/densa-deck/` -- the folder; zip it as `Densa-Deck-X.Y.Z-windows.zip` (top-level folder
  `densa-deck/`, which carries `README.txt`)
- `dist/Densa-Deck-Setup-X.Y.Z.exe`

Build with `py -3.13`: that interpreter has every bundle extra. Switching interpreters once
shipped a 0.7.0 with no QR code, no MCP server and no OCR, and every build "passed".

### 4. Release

```bash
gh release create vX.Y.Z --repo densanon-devs/densa-deck --title "Densa Deck vX.Y.Z -- <headline>" \
  --notes "<changelog>" dist/Densa-Deck-Setup-X.Y.Z.exe dist/Densa-Deck-X.Y.Z-windows.zip
```

### 5. Feed -- `densanon-toolkit/densa-deck-version.json`

Set `version`, `releaseDate`, `changelog`, and PINNED links:

```
"downloadUrl": "https://github.com/Densanon-devs/densa-deck/releases/download/vX.Y.Z/Densa-Deck-Setup-X.Y.Z.exe",
"zipUrl":      "https://github.com/Densanon-devs/densa-deck/releases/download/vX.Y.Z/Densa-Deck-X.Y.Z-windows.zip"
```

`curl -sIL <url>` both before pushing -- a 200, not a 404. Commit, push. **This is the step that
tells every existing install**; everything before it is invisible to customers.

---

## Android companion

### 1. Versions -- all four together

`companion/app.json` (`version` + `android.versionCode` +1), `companion/package.json`,
`companion/src/lib/version.ts` (`VERSION`, what the update check compares).
`android/app/build.gradle` is regenerated from app.json by prebuild.
`scripts/ship_companion.py` refuses to ship unless all agree and the version string is inside the
built JS bundle.

### 2. Test, commit, THEN build -- never edit during a build

```bash
cd companion && npx tsc --noEmit && npm test
git commit ...            # the build must be of committed source
npx expo prebuild --platform android --clean
cd android && export JAVA_HOME="C:/Program Files/Java/jdk-17" && ./gradlew assembleRelease --no-daemon
cd ../.. && py -3.13 scripts/ship_companion.py      # verifies, copies to Drive, hashes both ends
```

Read the gradle log for `BUILD SUCCESSFUL`, not a piped exit code.

### 3. Release

```bash
gh release create companion-vX.Y.Z --repo densanon-devs/densa-deck \
  --title "Densa Deck companion X.Y.Z (Android)" --notes "<changelog>" \
  "G:/My Drive/Densanon LLC/DensaDeck/DensaDeck-Companion-X.Y.Z.apk"
```

### 4. Feed -- `densanon-toolkit/densa-deck-mobile-version.json`

```json
{
  "version": "X.Y.Z",
  "versionCode": 142,
  "releaseDate": "YYYY-MM-DD",
  "downloadUrl": "https://github.com/Densanon-devs/densa-deck/releases/download/companion-vX.Y.Z/DensaDeck-Companion-X.Y.Z.apk",
  "changelog": ["..."]
}
```

The phone only accepts an `https://` link and a strictly newer version (`lib/app-update.ts`).
Phones older than 0.63.0 have no update check and must be told by hand once.

---

## Drive and pruning

- Drive (`G:\My Drive\Densanon LLC\DensaDeck\`): the manual channel, per the LLCWork rule --
  `DensaDeck-Desktop-X.Y.Z-windows.zip`, `DensaDeck-Companion-X.Y.Z.apk`. `ship_companion.py` removes
  the superseded APK; delete the superseded desktop zip by hand. Hash both ends.
- GitHub Releases: **keep every one.** Pinned links in old feeds and old installs depend on them,
  and they are the only rollback.
- `dist/`, `build/`: rebuilt from scratch by `build_desktop.py` every time.
