# MAVIA — mobile (student app)

Expo + React Native + TypeScript, using `expo-router` for file-based routing —
same conventions as the earlier `mavia-mobile` prototype in `Milestone1-Jean`.

This app is the **student** experience only. Teachers and admins use the
`web-app`. If a teacher/admin account logs in here, they land on a
"this app is for students" screen instead of a dashboard.

## What's here

Mirrors the auth flow built for `web-app`, adapted to native screens:

- `app/login.tsx`, `app/register.tsx` — register is student-only (no role
  picker; this app only signs people up as `STUDENT`).
- `app/verify-email.tsx` — resend a verification link. The link itself opens
  in the phone's browser (it points at the web-app's `/verify-email/:token`
  page), so no deep-linking is needed for verification itself.
- `app/not-supported.tsx` — shown if a non-student account logs in.
- `app/(student)/` — the signed-in student experience. Two bottom tabs, **Home**
  and **Profile** (account info + log out). Home is a nested stack —
  course list → lesson list → player — modeled on a music-app browsing flow
  (list of "albums" → "song list" → "now playing"), since that's the same
  shape as browsing courses → lessons → an audio lesson:
  - `home/index.tsx` — searchable course list + a "continue learning" row.
  - `home/[courseId]/index.tsx` — a course's lessons under a gradient hero
    header, with a play button that starts from the first lesson.
  - `home/[courseId]/[lessonId].tsx` — the lesson player (cover art,
    position, transport controls). No audio backend exists yet, so this
    renders its honest empty/disabled state rather than fake numbers.
  - `src/data/library.ts` — `fetchCourses`/`fetchLessons`/etc. all currently
    resolve empty — screens call these (not a hardcoded mock array), so
    pointing them at real endpoints later is a one-line change per function.
- `src/theme.ts` — same blue palette as `web-app/src/styles/mavia.css`, plus
  `gradients` for the cover-art tiles (`src/components/GradientTile.tsx`).
- `src/hooks/useResponsive.ts` — phone-first; the course list switches to a
  two-column grid once the viewport is tablet-sized (≥700px), per "flex
  depending on device."

### Accessibility

This app's whole premise is students who are blind or have low vision, so
every interactive element needs to work with a screen reader (TalkBack),
not just be visually clear:

- Icon-only controls (back, more, favorite, play/pause/skip, tab icons) all
  require an `accessibilityLabel` — enforced at the type level in
  `src/components/IconButton.tsx`, which also guarantees a ≥44×44 touch
  target regardless of the icon's visual size.
- List rows (`src/components/ListRow.tsx`) announce more than their visible
  title — e.g. a course row reads out its subtitle and lesson count, not
  just the name — with an `accessibilityHint` describing what double-tapping
  does.
- The player's position bar is `accessibilityRole="adjustable"` with an
  `accessibilityValue` describing playback position in words.
- Empty states (`src/components/EmptyState.tsx`) are announced as one
  combined message rather than a silent blank area.
- Text sizing isn't locked — system font-scaling (a low-vision accessibility
  setting) is left on throughout.

## Running it

```sh
cd mobile-app
npm install
npm run android      # or: npm start, then press "a"
```

Needs an Android emulator (Android Studio) or the **Expo Go** app on a
physical device on the same network.

### Talking to the backend

The app can't use `web-app`'s Vite dev proxy — it needs the Django backend's
real address:

- **Android emulator**: works out of the box. `10.0.2.2` (the emulator's
  alias for your machine's `localhost`) is the default in `src/config.ts`.
- **Physical device / iOS simulator**: create `mobile-app/.env` with your
  machine's LAN IP:
  ```
  EXPO_PUBLIC_API_BASE_URL=http://192.168.1.20:8000/api
  ```
  and start the backend reachable on that interface:
  ```sh
  python manage.py runserver 0.0.0.0:8000
  ```
  Also add that IP to `DJANGO_ALLOWED_HOSTS` in `backend/.env`.

### Email verification links on a physical device

The verification email points at `FRONTEND_URL` (the web-app). For a link
opened on a phone to work, `FRONTEND_URL` in `backend/.env` needs to be
reachable from the phone too — e.g. `http://192.168.1.20:5173` — and
`web-app` needs to be started with `npm run dev -- --host` so it listens on
the LAN, not just `localhost`.
