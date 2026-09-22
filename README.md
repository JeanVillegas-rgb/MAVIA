# MAVIA

> Shaped for Touch, Heard to Learn.

Main application. A learning platform for young blind and visually impaired
students, pairing a physical 6-button braille interface with responsive audio
lessons.

```
mavia/
├── backend/     Django + DRF API (token auth, roles, email verification)
├── web-app/     React + Vite frontend (landing, auth, teacher/admin dashboards)
└── mobile-app/  Expo + React Native — the student app (Android-first)
```

## What's built so far

- **Auth**: register / verify email / login / logout / current-user,
  token-based.
- **Email verification**: registration creates the account unverified and
  emails a 24h link. Login is blocked until the link is clicked. Unclaimed
  links can be resent. In dev the email prints to the runserver console; set
  SMTP creds in `.env` to send real mail.
- **Roles**: `STUDENT`, `TEACHER`, `ADMIN` on the user model. Admins are
  provisioned (not self-registerable).
- **Role interfaces**: teacher and admin dashboards on `web-app`, each behind
  a role-guarded route (content mostly placeholder — shells, routing,
  palette, and auth are wired). The student experience is the mobile app.
- **Design**: blue MAVIA theme in `web-app/src/styles/mavia.css`, mirrored as
  RN style values in `mobile-app/src/theme.ts`.
- **Student = mobile**: the student role lives in `mobile-app` (Expo / React
  Native, phone-first with a responsive breakpoint for larger screens — see
  `mobile-app/README.md`). Teacher and admin stay on `web-app`; a non-student
  account that logs into the mobile app is redirected to a "this app is for
  students" screen, and a student logging into `web-app` lands on
  `GetMobileAppPage` instead of a dashboard. Web registration is teacher-only.
- **Adaptive engine weights** (admin, real/functional — not a placeholder):
  `/admin/adaptive-weights` on `web-app` edits the Bayesian-knowledge-tracing
  parameters (P(guess), P(slip), P(learn), starting mastery, mastery ceiling,
  default question difficulty) the adaptive lesson-sequencing engine will run
  on, backed by `adaptive_config.AdaptiveConfig` (singleton row, admin-only
  API, validated ranges, reset-to-defaults). The scoring engine itself
  (`Milestone1-Jean/backend/adaptive`) isn't ported yet — when it is, its
  scorer should read `AdaptiveConfig.load()` instead of its hardcoded
  `P_GUESS`/`P_SLIP`/`P_LEARN` module constants.

Ported from the `Milestone1-Jean` prototype (`user` app + auth/UI frontend,
and the `expo-router` conventions from its old `mobile/`) and the
`alliance_project` prototype (email-verification flow). The science-lesson
pipeline apps (course, lessons, question_generation, adaptive) stayed in the
prototype — port them here when needed.

## Backend

Reuses the existing virtualenv at `../Milestone1-Jean/mavia/`.

```sh
cd backend
"../../Milestone1-Jean/mavia/Scripts/python.exe" manage.py migrate
"../../Milestone1-Jean/mavia/Scripts/python.exe" manage.py runserver   # :8000
```

`manage.py test` runs the full suite (29 tests: 21 auth + 8 adaptive-config).
Copy `.env.example` to `.env` to
override secrets / CORS origins / email (SMTP). With no email config the
verification link is printed to the runserver console.

Create an admin account:

```sh
"../../Milestone1-Jean/mavia/Scripts/python.exe" manage.py createsuperuser
# then set role=ADMIN in the Django admin, or:
"../../Milestone1-Jean/mavia/Scripts/python.exe" manage.py shell -c \
  "from user.models import User; User.objects.filter(username='<name>').update(role='ADMIN')"
```

## Web app

```sh
cd web-app
npm install
npm run dev        # :5173, proxies /api and /media to :8000
```

## Mobile app (student)

```sh
cd mobile-app
npm install
npm run android     # Android emulator, or Expo Go on a physical device
```

See `mobile-app/README.md` for connecting a physical device / emulator to the
backend (it can't use `web-app`'s dev proxy, so it needs a real host address).

## API

| Method | Path                                  | Auth  | Purpose                                         |
|--------|---------------------------------------|-------|------------------------------------------------|
| POST   | `/api/auth/register/`                 | none  | create account (unverified), emails a link     |
| GET    | `/api/auth/verify-email/<token>/`     | none  | consume link, mark verified                     |
| POST   | `/api/auth/resend-verification/`      | none  | `{email}` → re-issue link (generic response)    |
| POST   | `/api/auth/login/`                    | none  | returns token + user (403 until verified)       |
| POST   | `/api/auth/logout/`                   | token | deletes the caller's token                      |
| GET    | `/api/auth/me/`                       | token | current user                                    |
| GET/PATCH | `/api/adaptive-config/`            | admin | read/update the adaptive-engine weights          |
| POST   | `/api/adaptive-config/reset/`         | admin | restore weights to defaults                      |

### Frontend routes

`/register` → "check your inbox" screen · `/verify-email/:token` consumes the
link · `/verify-email/resend` re-requests one · login shows a resend link and
surfaces the "verify your email" message.
