# Known gaps

Status of the loose ends around the adaptive-learning + course-review work.

## Closed in this change

- **Scoring engine ported.** Bayesian Knowledge Tracing now lives in
  `backend/adaptive/services.py` (mavia's own app), not only in the
  `Milestone1-Jean` branch. `adaptive_config` finally has an engine behind its
  knobs. Adapted to `lessons.Question` (linear question order) instead of the
  Jean branch's Bloom-tier `GeneratedQuestion`.
- **Student read API + mobile wiring.** New `GET /api/adaptive/my-courses/`,
  `.../my-courses/<id>/lessons/`, `.../lessons/<id>/`, plus `start/` and
  `submit-response/`. `mobile-app/src/data/library.ts` now calls them, so the
  student course/lesson lists are no longer hard-coded empties.
- **`mavia/` is a git repo** with a root `.gitignore`.

## Still open

### 1. Real audio playback on mobile
`mobile-app/app/(student)/home/[courseId]/[lessonId].tsx` still renders the
"not loaded" transport UI — it shows the real lesson title/track count now but
does not actually play audio. Needs an audio library:

```
cd mobile-app
npx expo install expo-av        # SDK 51-compatible
```

then load `track.audio_url` (already absolute `/media/...` from the API — join
with `API_BASE_URL`'s origin) into an `Audio.Sound`, and drive the existing
play/seek/skip controls from it.

### 2. Expo SDK upgrade (51 → latest) for a real release
Expo Go on the stores now needs a newer SDK than this project's 51, so the app
is dev-client only and can't be handed to a phone via Expo Go. Deferred by
decision — do it later as its own change:

```
cd mobile-app
npx expo install expo@^57 --fix   # or the newest stable at the time
npx expo-doctor
npx expo start                     # retest login, course list, lesson list
```

Six majors of breaking changes (expo-router, RN version, gesture-handler,
reanimated). Budget a dedicated pass; don't fold it into feature work.

### 3. Standalone build
No EAS/standalone build has been produced. For a shareable APK:

```
cd mobile-app
npm i -g eas-cli
eas login
eas build:configure
eas build -p android --profile preview   # needs an Expo account
```

Local alternative without EAS: `npx expo prebuild && cd android && ./gradlew assembleRelease`.

### 4. Phone-over-Wi-Fi networking
Tonight's device run used USB + `adb reverse tcp:8000 tcp:8000`. For Wi-Fi:

- Set the Windows network profile for the Wi-Fi adapter to **Private**
  (`Get-NetConnectionProfile`; `Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private`).
- Allow inbound 8000/5173 (run elevated):
  ```
  netsh advfirewall firewall add rule name="MAVIA dev 8000" dir=in action=allow protocol=TCP localport=8000
  netsh advfirewall firewall add rule name="MAVIA dev 5173" dir=in action=allow protocol=TCP localport=5173
  ```
- Run Django on the LAN: `python manage.py runserver 0.0.0.0:8000`.
- On the phone, set `EXPO_PUBLIC_API_BASE_URL=http://<machine-LAN-IP>:8000/api`
  in `mobile-app/.env`.
