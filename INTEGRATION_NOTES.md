# Full local merge of mavia-latest

Incoming repository: https://github.com/Heathiko/MAVIA---MAIN/tree/mavia-latest
Incoming commit: `4069b96b27b6fdc7a4f675b9d2a97ac264bfee55`.
Local integration branch: `integration/classmate-full-local`.
Recovery branch: `backup/local-before-full-classmate-merge` (commit `34c0695`).

This supersedes the earlier UI-only integration. It is a real, local Git merge
of the unrelated histories, with compatibility changes. Nothing was pushed.

## What was preserved

- UUID metadata IDs, original numeric keys, lesson content and relationships.
- Existing course/variant/question-generation apps and their migrations.
- Existing adaptive tables, saved progress, APIs, and old mobile projects.
- SQLite and media storage (PDFs/audio remain local, not uploaded anywhere).

## What was integrated

- Incoming `web-app` and `mobile-app` projects, plus image-description support.
- New roster, student progress, course-review, and adaptive-weight screens.
- Email-verification models and endpoints, configurable without locking out
  existing local users. `EMAIL_VERIFICATION_REQUIRED=False` is the default.
  With verification enabled, configure SMTP and a reachable `FRONTEND_URL`;
  the default console mail backend prints links locally, not to an inbox.
- New engine imported as `adaptive_portal` with additive migrations and routes
  under `/api/adaptive-portal/`. Its web/mobile clients use those routes.
- Original `/api/adaptive/` continues to serve the original engine. Neither its
  schema nor its saved learner state was overwritten. New portal state starts
  separately; there is no automatic conversion of legacy progress.
- New scoring reads `AdaptiveConfig` weights. Reserved difficulty selection is
  disabled in the UI because the new engine sequences `lessons.Question` rows,
  not Bloom-tier questions. It does not yet select simplified/elaborated variants;
  those remain available through the preserved original course/adaptive system.
- Student access requires enrollment and publication. Submission is checked
  against the assigned question; authoring/review endpoints require teacher/admin.
- Mock dashboard users/counts were replaced with real account/course data.

## Run locally

1. Backend: activate `backend/.venv`, then `python manage.py runserver`.
   For a new checkout run `python manage.py migrate` first. This workspace used
   `python scripts/migrate_with_backup.py`, which backs up SQLite under
   `backend/backups/` and verifies existing content and learner rows afterwards.
2. Web: `cd web-app`, `npm ci`, `npm run dev`. The existing `frontend` command
   also works and includes the connected review/weights/verification screens.
   Run only one web dev server on port 5173 at a time.
3. New mobile: `cd mobile-app`, `npm ci`, `npm start`.
   Set `EXPO_PUBLIC_API_BASE_URL` to a backend URL reachable by the phone. The
   default `http://10.0.2.2:8000/api` is for the Android emulator, not a real phone.
4. Use the web Review screen to enroll students. Publish topics before checking
   student playback. The incoming app lists enrollment-based portal progress.

Restart a backend previously launched with `--noreload` to load the new routes.
Changing branch does not reverse database migrations. Use the saved SQLite
backup deliberately if a full rollback is needed; never delete the working DB.

## Verification

Verified in this workspace: all 228 backend tests passed; both web builds,
mobile TypeScript checking, and Android JavaScript export passed. Database
migration preserved the existing rows/columns in 25 checked tables. Backup:
`backend/backups/before-classmate-merge-20260908T050336Z-b7af5a23.sqlite3`.

Backend: `python manage.py test --noinput`, `python manage.py check`, and
`python manage.py makemigrations --check --dry-run`.
Web: `npm run build` in both web directories.
Mobile: `npx tsc --noEmit` and `npx expo export --platform android`.

Browser/physical-device checks remain manual: this session has no connected
browser or phone. An Android JS bundle is not a tested APK or an audio playback
test. SMTP delivery also requires deployment-specific configuration.
