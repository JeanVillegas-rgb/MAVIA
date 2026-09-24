# Review Grouping Changes

A teacher-triggered check that updates the grouping after a learning object is
edited in a file that was already uploaded and confirmed.

Added: 2026-09-14 · Status: implemented, tested, **not yet committed** and not
yet clicked through in the browser.

---

## Why this exists

Grouping is **sticky** by design. A background refresh never takes an object
away from its group, so a teacher's grouping can't change without them knowing.

The side effect: editing an object that is already grouped **never updated its
group**. For example, if a teacher rewrote an "Examples" section to cover only
solids, it stayed grouped with the other example sections, and the learning
path kept showing them as one concept.

Before this feature:

| Teacher action | Did the grouping update? |
|---|---|
| Edit the content of an object that is already grouped | ❌ Stayed in its old group |
| Edit an object that is alone in its group | ✅ Matched again on confirm |
| Add a new object | ✅ Matched on confirm |
| Delete an object | ⚠️ An empty group row could be left behind |
| Edit objects in PDF A | ⚠️ PDFs B and C were not checked again against A's changes |
| Re-run extraction | ⚠️ An object with the same title and position went back to its old group without its text being checked |

---

## Decisions (agreed with the teacher/developer)

| Question | Decision |
|---|---|
| When should an edited object move? | **Only if it no longer fits.** It is scored against its current group first; if it still matches at 60% or higher, it stays. |
| What about groups the teacher made (Connect / accepted suggestion)? | **Propose, never apply automatically.** The change is shown but **unticked** by default. |
| How does the teacher approve? | **Per change.** Each proposal has a checkbox, then **Apply selected**. |
| What if the topic is already published? | **Unpublish, require republish**, so students never see a half-updated lesson. |

Defaults chosen by the implementation:

- **Where the button goes:** step 1 of the topic review (Related Concepts), because groups are shared across all PDFs of a subtopic.
- **Data that existed before this feature:** each object's current text is the starting point, so the button only lights up after the next edit.
- **Re-extraction counts as an edit:** an object recreated with the same title and position but different text is flagged.
- **Deleting an object** on step 1 now removes a group it leaves empty.

---

## How it works for the teacher

1. **Edit a grouped learning object and confirm its file again.**
   - The notice under the step-1 heading turns yellow: *"2 edited learning objects may belong to a different concept now."*
   - The **Review grouping changes** button becomes clickable.
   - When nothing was edited, the button is greyed out with a tooltip explaining why.
2. **Click the button.** A progress popup shows while it checks, then a preview lists each edited object with one of four outcomes:

   | Outcome | Meaning |
   |---|---|
   | **Stays** | Still matches its group at 60% or higher. Nothing changes. |
   | **Move** | No longer fits, and another concept is a clear match. |
   | **Stand alone** | No longer fits and nothing else clearly matches. If there is a possible match, it becomes a pairing suggestion for the teacher to review afterwards. |
   | **Check manually** | Could not be scored, for example because the text is too long for the model. |

3. **Each proposed change shows what it affects:**
   - whether you grouped the object yourself (unticked by default),
   - whether it is the concept's **Normal** version (that concept will need a new original),
   - which version text it supplied that will be removed,
   - how many linked questions move with it.

   A red warning is shown if the topic is published.

4. **Apply.**
   - Only **ticked** changes are carried out.
   - Every reviewed object is marked as reviewed, including unticked ones, so the button doesn't keep asking about the same decision.
   - If anything changed on a published topic, the topic is **unpublished**.

---

## How it works in the code

### 1. Detecting an edit — a fingerprint

`LearningObject.grouping_content_hash` stores a fingerprint (SHA-256) of the
object's title and text **at the moment its grouping was decided**.

- Whitespace is collapsed first, so extra spaces or line breaks don't count as an edit.
- A new object records its own text when it is saved.
- The migration gives every existing object a starting fingerprint, so the button starts disabled.

An object counts as **changed** when all of these are true:

- its file is **confirmed** (edits in an unconfirmed file wait until it is confirmed again),
- it **shares a group** with at least one other object (a standalone object is already matched again when its file is confirmed),
- its stored fingerprint no longer matches its current text.

The fingerprint is updated whenever a grouping is decided against the current text:

| Where | File |
|---|---|
| Teacher connects objects | `views.py` → `connect_learning_objects` |
| Teacher separates an object | `views.py` → `separate_learning_object` |
| Teacher accepts a suggestion | `views.py` → `accept_match_suggestion` |
| Background matching joins a standalone object to a group | `learning_resource_linker.py` → `refresh_learning_object_match_suggestions` |
| Review grouping changes is applied | `services/regrouping.py` → `apply_regrouping` |

**Re-extraction:** `_sync_learning_objects` recreates every object and restores
its old group by title and position. `prior_grouping_fingerprints()` carries
the **old** fingerprint across, so a passage whose wording changed is still
flagged.

### 2. Building the preview — `propose_regrouping(node)`

This writes nothing. For each changed object:

1. **Does it still fit its current group?**
   - It is scored with `rank_groups` against **every** other member.
   - The group's score is its **weakest** member's score, the same rule grouping already uses.
   - It fits if the score is 60% or higher (the automatic-grouping threshold), every member could be checked, and the SBERT cosine floor is met.
2. **If not, where does it belong?**
   - `semantic_decision(..., allow_grouped_source=True)` searches the whole subtopic.
   - Its **current group is excluded** from the candidates.
   - Groups the teacher previously **rejected** for it stay excluded.
   - A **high**-confidence result becomes **Move**. Anything else becomes **Stand alone**.

`allow_grouped_source` is opt-in. Background matching still refuses grouped
objects exactly as before.

### 3. Applying — `apply_regrouping(node, learning_object_ids)`

- **The proposals are recomputed on the server.** Object IDs the review did not propose are ignored, so a stale page can't move anything it shouldn't.
- For each **ticked** change:
  - **Version text is cleaned up.**
    - If the object was the group's **Normal** version: text it held for its old companions is removed, their "represented by" link is cleared, and the old group's version selection is reset so it picks a new original.
    - Otherwise: text this object supplied to its old group's original is removed.
    - **Generated versions are never deleted,** including any a teacher edited.
  - **The object moves** to its new group, or to a new group of its own.
  - **A group left empty is deleted.**
  - **The decision is recorded as the teacher's.** The object is marked "rejected" against its old companions, so background matching won't pull it back, and "accepted" with its new group.
- **Every reviewed object** gets its fingerprint updated.
- **If any change was applied and the topic was published,** it is unpublished.
- The view then refreshes the relationship snapshots of the affected files (grouping suggestions and question links).

### 4. API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/courses/{course}/outline-nodes/{node}/learning-resources/` | Now includes `"regrouping": {"changed_count": N}`. This is a database comparison only; no AI model runs. |
| GET | `/api/courses/{course}/outline-nodes/{node}/regrouping/` | The preview: `{"proposals": [...], "published": bool}` |
| POST | `/api/courses/{course}/outline-nodes/{node}/regrouping/apply/` | Body `{"learning_object_ids": [..]}` → `{"summary": {...}, "resources": {...}}` |

If the semantic models cannot run, both regrouping endpoints return **503**.

Each proposal contains:

```json
{
  "learning_object_id": 12,
  "title": "Examples",
  "material_title": "Lesson two",
  "action": "move | separate | stay | unscored",
  "reason": "No longer matches “Examples” (41%). Best match is “Solid” (78%).",
  "current_group": {"id": 4, "label": "Examples", "member_count": 2},
  "destination_group": {"id": 7, "label": "Solid", "member_count": 3},
  "fit_score": 0.41,
  "destination_score": 0.78,
  "teacher_made": false,
  "selectable": true,
  "default_selected": true,
  "suggestion_after": false,
  "impact": {"question_count": 2, "was_original": false, "removed_version_slots": ["simplified"]}
}
```

### 5. Terminal trace

```
[Regrouping topic 2] Checking 2 edited learning object(s)
[Regrouping topic 2] (1/2) "Examples" -> move: No longer matches “Examples” (41%). Best match is “Solid” (78%).
[Regrouping topic 2] (2/2) "Liquid" -> stay: Still matches “Liquid” (88%).
[Regrouping topic 2] Applied 1 of 2 proposal(s); topic unpublished
```

---

## Files

**Backend**

- `lessons/services/regrouping.py` (**new**): detection, preview, apply
- `lessons/migrations/0017_learningobject_grouping_content_hash.py` (**new**): field plus starting fingerprints
- `lessons/models.py`: `grouping_fingerprint()`, the `grouping_content_hash` field, `mark_grouping_current()`, and a `save()` default
- `lessons/services/semantic_grouping.py`: the opt-in `allow_grouped_source` setting on `semantic_decision`
- `lessons/services/learning_resource_linker.py`: `prior_grouping_fingerprints()`; background joins now record the fingerprint
- `lessons/services/content_generator.py`: re-extraction carries the old fingerprint across
- `lessons/views.py`: two new endpoints, `changed_count` in the payload, fingerprint updates in Connect/Separate/Accept, and empty-group cleanup on the step-1 delete

**Frontend**

- `web-app/src/api.js`: `fetchRegroupingPreview`, `applyRegrouping`
- `web-app/src/pages/TopicDetailPage.jsx`: the step-1 notice and button, the `RegroupingBusy` progress popup, the `RegroupingReview` preview dialog
- `web-app/src/styles/pipeline.css`: `.regrouping-*` styles

**Tests**

- `lessons/test_regrouping.py` (**new**, 27 tests). The AI text model is replaced with a fake, so the tests are fast and give the same result every time.
  - **Detection:** unedited objects, real edits, whitespace-only edits, standalone objects, unconfirmed files, and fingerprints carried across re-extraction.
  - **Preview:** stays, move, stand alone, the current group never being the destination, teacher groups unticked, and the preview changing nothing.
  - **Apply:** chosen moves, the decision recorded as the teacher's, unticked changes settled but not applied, IDs that weren't proposed ignored, empty groups removed, unpublish and no-unpublish, and version cleanup both when the object was the Normal version and when it wasn't.
  - **Fingerprint updates** after Connect and Separate, plus all three endpoints.

Full backend suite: **586 tests pass.** Frontend build: clean.

---

## Known limitations and follow-ups

- ~~**The Separate button does not clean up version text.**~~ **Fixed 2026-09-14** (see "Follow-up fix" below).
- **Edited objects are judged one at a time.** If two edited objects share a group, each is scored against the other's **new** text, and applying one does not re-score the other.
- **Label corroboration** (matching exact titles) still applies when searching for a new group. It is not used in the "still fits" check, which is scored on content only.
- **In `review` mode** (`SEMANTIC_GROUPING_MODE=review`), no result is ever high-confidence, so an object that no longer fits is always proposed as **Stand alone**, never Move.
- **Chunking quality still limits what this can fix.** Regrouping moves whole learning objects; it cannot repair a badly split passage. Better chunking is being discussed separately.

---

## Follow-up fix: leftover version links (2026-09-14)

### The symptom

Clicking **Generate missing versions** on step 4 showed:

> *3 versions could not be generated. This object is taught through another one; generate versions there.*

### The cause

1. Grouping had merged all of PDF 2's "examples" sections into one concept around object 14, "Everyday examples of solids". This was the over-merge caused by chunking.
2. When versions were settled, the other example sections were marked **"taught through object 14"**, and their text became object 14's versions.
3. They were later taken out into their own concepts using **Separate** (and one was connected to another group). **Separate, Connect and Accept suggestion did not undo those links.**
4. Objects 18, 22 and 28 were each alone, so each counted as its own original with missing versions. The button sent them, and the backend refused them because they were still marked "taught through" object 14. That's exactly 3 failures.

It also had a **hidden side effect**: object 14 (solid examples) was serving other concepts' text as its own versions. Its Simplified version was a water passage, and its Extras were the liquid, gas and figure passages.

### The fix

1. **`release_from_group(learning_object, companions)`** in `course/version_assignment.py`, called **before** an object leaves its group. It undoes only the links between the leaving object and the members staying behind:
   - text the object supplied to the group's original is removed, and its "taught through" link is cleared;
   - if the object **was** the original, text its companions supplied to it is removed, their links are cleared, and the group's version selection is reset;
   - **generated versions are never deleted,** including teacher-edited ones.

   It is now called by **Separate**, **Connect** (for any object pulled out of another group), **Accept suggestion** (for the object that moves), and **Review grouping changes**, which now uses this helper instead of its own copy. `group_original_id()` moved to the same file so they share one definition.
2. **One-time data repair:** migration `course/0007_repair_cross_group_version_links`. It clears every "taught through" link that points to an object in a different concept, and deletes every source version whose text came from an object in a different concept. Generated versions have no source object, so they are never touched.

   On this database it cleared 4 links (objects 18, 22, 28, 29 → 14) and removed 4 texts from object 14. **Object 14 now reports a missing Simplified version**, which Generate missing versions will fill.
3. **Error message:** it now counts **concepts**, and names them, instead of counting errors. Each concept can fail on two slots, so the old count overstated it.

### Tests

- `course/test_representation.py`:
  - `ReleaseFromGroupTests`: a leaving member, a leaving original, and nobody staying behind.
  - `CrossGroupRepairMigrationTests`: only links and texts that cross concepts are removed.
- `lessons/test_regrouping.py`, `SeparateReleasesVersionLinksTests`: Separate and Connect undo the links, and generating versions for a separated object is no longer refused.

Full backend suite: **593 tests pass.**
