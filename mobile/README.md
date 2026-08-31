# MAVIA — Student Mobile App (Frontend Scaffold)

Expo Router project. All screens run on mock data (`src/data/mockData.ts`)
served through `src/data/api.ts` — that file is the only place you need to
touch when the Django backend is ready. Swap each function body for a real
`fetch()` call; every screen already depends only on the return types in
`src/data/types.ts`, which mirror the course-package models 1:1.

## Run it

```
npm install
npx expo install   # aligns native deps to your Expo SDK version
npm start
```

Scan the QR with Expo Go on your iPhone. For Android, use `eas build` (cloud
build, no local Android Studio needed) since you don't have a physical
Android device to test on.

## Screens

- `app/index.tsx` — Dashboard / lessons grid (matches your MAVIA screenshot)
- `app/lesson/[lessonId]/index.tsx` — Lesson player: steps through chunks,
  variant toggle (Normal/Elaborated/Simplified), mock audio control,
  "I have a question" mic FAB (UI stub — voice capture not wired yet)
- `app/lesson/[lessonId]/questions.tsx` — Question flow: MCQ/TF, instant
  feedback, calls `submitAnswer()` per question (currently mock/no-op)
- `app/lesson/[lessonId]/results.tsx` — Score + tiered feedback message

## Wiring the real backend later

1. In `src/data/api.ts`, replace the mock bodies with real `fetch()` calls
   to your DRF endpoints.
2. `fetchLessonPackage()` should return NORMAL narration read live from
   `generated_json["lesson_playlist"]` and ELABORATED/SIMPLIFIED from your
   `LessonObjectVariant` rows — merge those server-side so this function's
   return shape doesn't need to change.
3. `submitAnswer()` should hit your `StudentResponse` creation endpoint —
   consider having it return updated `LearningState` (mastery, next
   variant/bloom) so the app can react (e.g. jump straight to SIMPLIFIED on
   the next lesson) instead of just fire-and-forgetting.
