# Building the companion

## The one that matters

    cd companion/android
    JAVA_HOME="C:/Program Files/Android/Android Studio/jbr" NODE_ENV=production \
      ./gradlew assembleRelease
    # -> app/build/outputs/apk/release/app-release.apk

**Release, not debug.** A debug APK does *not* contain the JavaScript — it
loads it from a Metro dev server over the network at launch, so installing one
on a phone and walking away from the PC gives a red error screen. The release
build has `assets/index.android.bundle` inside it and runs standalone. Check
for that file before shipping one:

    jar tf app-release.apk | grep index.android.bundle

It is signed with the debug key, which is fine for sideloading and **not** fine
for Play. A real upload key is a separate job.

## Prerequisites, as found on this machine

- Android SDK at `C:\Users\Jordan\AppData\Local\Android\Sdk` (platforms 34-36).
- JDK 17 bundled with Android Studio at
  `C:\Program Files\Android\Android Studio\jbr`. The `java` on PATH is a stub
  that does nothing, so `JAVA_HOME` must point at the JBR.
- `android/local.properties` needs **forward slashes**:
  `sdk.dir=C:/Users/Jordan/AppData/Local/Android/Sdk`. Backslashes are read as
  escapes and the path is rejected as malformed.

## Regenerating the native project

    npx expo prebuild --platform android --no-install

`android/` is generated and gitignored. `local.properties` does not survive
this, so rewrite it afterwards.

## Tests

    npm test          # 83 checks, no device needed
    npx tsc --noEmit

The data layer is deliberately free of React Native imports so Node can run it
directly. That is what lets the part that can lose someone's cards be tested on
every run rather than only when a phone is plugged in.
