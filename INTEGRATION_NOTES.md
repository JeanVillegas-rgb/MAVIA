# Classmate UI integration

Source: https://github.com/Heathiko/MAVIA---MAIN/tree/mavia-latest
Source commit: `4069b96b27b6fdc7a4f675b9d2a97ac264bfee55`.
Integration branch: `integration/classmate-ui-preserved`.
Local UUID checkpoint: `871c4f9`.

## Included

- New public pages, role-aware navigation, authentication styling, teacher/admin
  layout, course pages, and updated lesson-object editing layout.
- Source `web-app/src` integrated into the existing `frontend/src` directory.
  Continue using `cd frontend` and `npm run dev`; no frontend folder rename.
- Read-only teacher course review, backed by additive review endpoints reading
  existing lesson materials and detected questions. This is a teacher preview,
  not a replacement for the existing student lesson-package API.
- Real course data and signed-in identity replace source dashboard mock data.
- Existing registration remains immediate; no verification email is promised.

## Preserved

- Existing database, media files, UUIDs, and full migration history.
- `course`, `LessonVariant`, `question_generation`, and the existing adaptive
  engine, APIs, and mobile projects.
- Existing frontend API helpers and content review/publish workflow.
- Existing teacher-written image descriptions (no new vision-model dependency).

## Not integrated

- Replacement adaptive schema, enrollment/progress dashboard, adaptive weight
  editor, email verification, vision-model generation, and `mobile-app`.
- Unimplemented source navigation and simulated dashboard figures are omitted;
  unavailable progress/weight features have explicit notices, not fake results.

These require a separate backend/mobile integration with additive data migration
and mapping of the existing learner IDs, generated questions, and lesson variants.
The source repository has no shared Git ancestor, so this is a selective port,
not an `--allow-unrelated-histories` merge or wholesale replacement.

## Validation

Run `npm run build` in `frontend` and `python manage.py test lessons course user
adaptive --noinput` in `backend`. New review tests cover role restrictions,
cross-course module rejection, existing material readout, and empty content.
No migration is needed for this UI integration. Other checkouts still need the
earlier `0012_metadata_ids` migration if it has not been applied.

Browser visual/interactive checks could not be performed in this session because
no browser connection was available. Manually check login, teacher dashboard,
PDF review steps, and `/review` before deploying. Nothing was pushed to GitHub.
