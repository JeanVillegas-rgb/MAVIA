# Getting started on MAVIA

Written for a teammate joining this repository — you know the project exists,
you have not read the code. Follow this top to bottom. It should take about an
hour to reach the point where you can run the app and see a lesson publish.

If you only have ten minutes, read §1 and §2 and stop.

---

## 1. What MAVIA is, in one paragraph

An adaptive science tutoring platform for **blind and low-vision elementary
learners**. A teacher uploads science PDFs; the system pulls the lesson out of
them, works out which passages across the different files teach the same thing,
writes a simpler and a fuller version of each, generates questions, turns all of
it into speech, works out what order the ideas should be learned in, and
publishes a course a learner can listen through.

**Accessibility is the product, not a feature.** A silent audio track, a figure
description that reads out the model's own chatter, or a control a screen reader
cannot reach are functional defects here, not polish. That standard explains a
lot of decisions in the code that would otherwise look over-careful.

---

## 2. Read these, in this order

| # | File | Why | Time |
|---|---|---|---|
| 1 | `docs/MAVIA_Processing_Guide.docx` | The whole pipeline, worked on one real lesson, with labelled screenshots of the actual PDFs. **Start here** — it is the only document that shows what the words mean. | 30 min |
| 2 | `docs/PROJECT_CONTEXT.md` | The reference: who owns what, the invariants, the current state of the database, what is still broken. | 20 min |
| 3 | `docs/AGENT_LOG.md` | What happened recently and why. Newest entry last. Read at least the last entry. | 10 min |
| 4 | `docs/learning_path_revision_2026-09-17.md` | How the learning path decides an edge, and every measurement taken against the teacher's gold standard. | 15 min |

Read them **in that order**. The guide teaches the vocabulary; the others assume
it.

### The five words everything else is built on

You cannot read the code without these. The guide explains each with a picture.

- **Learning material** — one uploaded PDF.
- **Learning object** — one extracted chunk of a PDF: a passage, a list, or a
  figure with its written description. The smallest thing the system stores.
- **Concept** — the same teachable idea gathered across PDFs. **A concept is
  one step of the learning path.**
- **Bundle** — all the objects of **one** PDF inside one concept, in that PDF's
  order. A concept holds one bundle per PDF. This is the idea that trips
  everyone up: a concept is *not* one object per file.
- **Version** — what a bundle is used for: Normal, Simplified, Elaborated or
  Extra.

### Only if you are working on that area

- `docs/superpowers/specs/2026-09-20-concept-bundles-design.md` — why the manual
  "merge" feature was deleted and replaced by bundles. Read before touching
  grouping or versions.
- `docs/superpowers/specs/2026-09-17-grouping-merge-and-learning-path-design.md`
  — the learning-path criteria and their sources. Read before touching the
  criteria.
- `backend/learning_path/HANDOFF.md` — the shape of the published path, which is
  the contract the student app will read.

---

## 3. Get it running

System Python, no virtualenv. Git Bash on Windows.

```bash
# backend
cd backend
pip install -r requirements.txt
pip install -r requirements-semantic.txt     # sentence-transformers, needed for grouping
python manage.py migrate
python manage.py runserver                   # http://localhost:8000

# frontend, in a second terminal
cd frontend
npm install
npm run dev                                  # http://localhost:5173
```

**You also need [Ollama](https://ollama.com) running**, with two models:

```bash
ollama pull gemma3:4b       # content versions, figure descriptions
ollama pull llama3.2:3b     # question generation
```

Nothing calls a paid API. Everything runs locally except text-to-speech, which
uses Edge TTS and needs to reach `speech.platform.bing.com`.

### Check it works

```bash
cd backend && python manage.py test -v 1
```

**827 tests, all passing.** If they do not pass on a clean checkout, stop and
ask — do not start changing things.

That run takes a couple of minutes because some tests load the real sentence
encoder. That is deliberate: `learning_path/test_gold_paths.py` is the one test
that says whether the learning path still works, and mocking the model would
make it meaningless.

---

## 4. See the pipeline actually happen

The database already has three published topics, so you can look at real output
before uploading anything of your own.

1. Open `http://localhost:5173`, go to course 6, then topic **152**
   (Solid, Liquid and Gas).
2. Walk the five review steps in order. What to look for in each:

| Step | What it is | Look at |
|---|---|---|
| 1 Review Connections | Which objects teach the same thing | "Comparing the Three States" — one block per PDF, four objects from one file and two from the other |
| 2 Content versions | Which bundle is Normal / Simplified / Elaborated | The roles and Gemma's stated reason for each |
| 3 Questions | A bank per concept, from its Normal text | — |
| 4 Content & questions | Final read-through | One block per version, each naming the file it came from |
| 5 Learning path | The derived order and its edges | Whether the order is one you would teach in |

3. Then look at the published contract, which **no screen renders**:

```bash
curl -s -H "Authorization: Token <teacher token>" \
  http://localhost:8000/api/learning-path/topics/152/published/ | python -m json.tool
```

That payload is what the student app will consume. The web app only ever reads
the *preview* endpoint, so a change to the published path is invisible in the UI
— check it with curl, not by clicking.

---

## 5. How to trace a change

Every change to this project leaves the same trail. Follow it in this order.

1. **`docs/AGENT_LOG.md`** — one entry per working session, newest last. Each
   says what changed, what was decided on the team's behalf and what it costs if
   that decision was wrong, what was *not* done, and whether the live database
   was touched. Start here.
2. **`git log`** — commits are small and each message says *why*, not what. A
   commit that fixes a defect names the defect.
3. **The test that came with it.** Almost every fix here has a test that fails
   without it, and the test's docstring usually records the real-world symptom
   that prompted it. `backend/learning_path/test_published_bundles.py` is a good
   example: it opens by describing what a learner was actually being served.
4. **`docs/learning_path_revision_2026-09-17.md`** — every measurement against
   the gold standard, with dates. If you want to know whether the path got
   better or worse, it is in there.

### If you change the learning path

Run this before and after, and put the numbers in the revision doc:

```bash
cd backend && python manage.py test learning_path.test_gold_paths -v 2
```

The teacher hand-specified the correct edges and order for two lessons. Those
fixtures are the acceptance test. The test fails on a **new** gap, a forbidden
edge, a wrong order, *or* a gap that closes — because a closing gap means the
recorded list needs updating, not that you can ignore it.

**Do not tune the constants to make a gap close.** They are already fitted to
those two lessons; tuning further fits noise. This is written down in the
revision doc with the reasoning.

---

## 6. Rules that are not obvious

These were each learned by breaking something.

- **A version supplied by a PDF stores no copied text.** Its text *is* its
  objects. Anything that counts `LessonVariant` rows to decide whether a version
  exists is wrong — ask `version_bundles(group)`. Three separate readers have
  now been caught doing this.
- **Bundle order has one authority**:
  `backend/lessons/services/concept_bundles.py`. Do not re-derive it anywhere
  else.
- **Teacher decisions are never overwritten.** A rejected suggestion, a locked
  label, a teacher-set role: automatic paths must skip them.
- **Never write a teacher decision from a batch job.** A repair script once
  recorded its own guesses as the teacher's; those rows had to be deleted.
- **`represented_by` means "taught through another object"**, and every consumer
  filters it out. A dangling pointer makes content vanish silently from the
  lesson, the audio and the package.
- **CSS is mirrored into two files.** `frontend/src/styles/pipeline.css` is the
  one that is imported; `index.css` is a partial, unused copy. Change the first;
  the second is kept in step by habit.
- **Under-reach beats over-reach when cleaning model text.** A helper that
  stripped the model's chatter once deleted real sentences. Leaving some chatter
  is acceptable; deleting a real sentence is not.
- **`git add <path>` explicitly.** Never `git add -A`: `frontend/dist` is
  tracked, and `MAVIA MANUSCRIPT.docx` must never be committed.

---

## 7. Where things live

```
backend/
  lessons/        uploads, extraction, learning objects, grouping, publishing
    services/concept_bundles.py     bundle order — the single authority
    services/unit_matching.py       runs inside a PDF, and corroboration
    services/topic_publish.py       the publish run and its completeness gate
    services/audio_generator.py     Edge TTS
  course/
    version_assignment.py           which bundle is Normal/Simplified/...
    variant_generator.py            Gemma writes the missing versions
    services.py                     the lesson package the student app reads
  learning_path/
    services/criteria.py            the three criteria and the vote
    services/concept_units.py       concepts, as the path sees them
    services/publishing.py          the graph and Kahn ordering
    services/published.py           reads the saved path back out
    fixtures/gold_*.json            the teacher's hand-specified answers
  question_generation/              the question pipeline
frontend/src/pages/TopicDetailPage.jsx    the whole five-step review screen
docs/                               everything in §2
```

---

## 8. Before you hand work back

- `cd backend && python manage.py test -v 1` — all green.
- `cd frontend && npm run build` — clean.
- If you touched grouping, versions, publishing or the path, run
  `learning_path.test_gold_paths` and say what the numbers were.
- **Add an entry to `docs/AGENT_LOG.md`.** Append at the bottom, never rewrite
  someone else's. Say what you decided on the team's behalf and what it costs if
  you were wrong, what you deliberately did not do, and whether you touched the
  live database.
- Be honest about what you did not verify. "Tests pass" and "I reasoned it
  through" are different claims, and the log distinguishes them.
