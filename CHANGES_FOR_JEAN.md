# Changes for Jean

Hi Jean! This lists what changed on `updated-jure` since your `MaviaDocling` push, with the parts that touch your code first. Everything was done on Jure's side with your OK ("ako na ang db cleanup").

- Branch: `updated-jure`
- Commits since your branch: `40261ce` (merge of `MaviaDocling`) up to `12df342`
- Your branch was merged whole, classifier and learning-object builder included.

## 1. Before you pull

- **Migrations were renumbered.** Your three question-bank migrations kept their numbers (`0010_question_bank_schema`, `0011_rename_…`, `0012_question_content_fingerprint`). Jure's two older ones moved after them and became `0013_remove_learnerresponse` and `0014_remove_question_generation_fingerprint`. New ones follow: `question_generation` `0015`–`0018`, and `lessons` `0027`–`0028`.
- **If your local database already ran Jure's old `0010_remove_learnerresponse` or `0011_remove_generatedquestion_generation_fingerprint`:** undo them on the old code first (`migrate question_generation 0009`), then pull and `migrate`.
- **Otherwise:** just pull and `migrate`.
- `lessons` `0027` drops columns and a table. Back up your `db.sqlite3` first.

## 2. Your table: `lessons_question` is now `lessons_extractedquestion`

Model `lessons.models.ExtractedQuestion` (`backend/lessons/models.py`). It now holds only what extraction found:

| Kept | Removed |
|---|---|
| `id`, `material`, `prompt`, `question_type`, `choices`, `correct_answer`, `order`, `source_page`, `source_block_id`, `source_excerpt` | `source_type`, `content_fingerprint`, `teacher_edited`, `bloom_level`, `thinking_order`, `difficulty`, `category`, `validation_status`, `validation_issues`, `adaptive_question` |

- `lessons_questionlearningobjectlink` (model `QuestionLearningObjectLink`) is **deleted**. A question's concept is now `node` + `paired_by` on the question bank row.
- `content_fingerprint` lives only on the bank (`question_generation_question`). Your code compares `question_fingerprint(prompt)` instead.
- Typed and generated questions no longer get a row in your table. The "Manually added questions" placeholder material is gone (deleted by migration `0015`).

## 3. Your code that changed

### `backend/lessons/services/question_workflow.py`
**Still there (yours, used by your upload):** `parse_question_structure`, `validation_issues`, `normalized_question_text`, `question_fingerprint`, `duplicate_in_topic`, `detected_question_payloads`, `_sync_extracted_question_to_bank`, `synchronize_detected_questions`, `question_snapshots`, and the line/prompt helpers.

**Removed:** `classify_question`, `enriched_question_values`, `duplicate_for_topic`, `question_is_approved`, `counts_toward_minimum`, `concept_tier_counts`, `is_given_to_learners`, `label_printed_questions`, `sync_question_to_adaptive`, `delete_generated_questions_for`, `mirror_generated_questions`, `CONFIRMED_PAIRING_STATUSES`, `LABEL_FIELDS`.

**Behaviour you should know:**
- **Hand-off only.** `synchronize_detected_questions` writes `ExtractedQuestion` rows and, through `_sync_extracted_question_to_bank`, one unlabelled `review` row in the bank per printed question. It no longer labels, pairs or copies anything.
- **Repeated text is skipped.** If the topic's bank already has a question with the same text (same fingerprint), the printed question is not added to the bank again. The existing one is kept.
- **Dropped questions take their bank copy along.** When a re-read no longer finds a printed question, deleting it now deletes its bank copy too, even if the teacher edited or paired it. The delete-PDF dialog warns the teacher about this.
- **Your same-question check compares text.** `duplicate_in_topic(material, fingerprint)` compares `question_fingerprint(prompt)` because the column is gone.
- **Smaller snapshot.** `question_snapshots` (stored in `generated_json["questions"]`) now has only the extraction fields: no labels, links or validation fields.

### Deleted files
- `backend/lessons/services/question_linker.py`: the SBERT question-to-concept matcher and its `QUESTION_PAIR_*` thresholds. Pairing is now done by checking the answer key against each concept's text (`question_generation/services/pairing.py`). The `QUESTION_PAIR_*` lines were removed from `.env.example`.
- `backend/lessons/management/commands/repair_question_workflow.py`.

### `backend/lessons/views.py`
The question endpoints moved to `question_generation` (teacher/admin only). Removed from your view set:

| Removed (yours) | Replacement |
|---|---|
| `create_topic_question` POST `…/outline-nodes/<id>/questions/` | POST `/api/generation/topics/<id>/questions/` |
| `delete_topic_question` PATCH/DELETE `…/questions/<id>/` | PATCH/DELETE `/api/generation/topics/<id>/questions/<id>/` |
| `review_question_pairing` POST `…/questions/<id>/pairing/` | POST `/api/generation/topics/<id>/questions/<id>/pairing/` |
| `label_topic_questions` POST `…/label-questions/` | POST `/api/generation/topics/<id>/questions/label/` |
| `keep_question_bank` POST `…/concepts/<id>/keep-questions/` | POST `/api/generation/topics/<id>/concepts/<id>/keep-questions/` |

Also in `views.py`:
- **Topic payload.** `node_learning_resources` (the topic payload) now gets its questions from `question_generation.services.question_bank.topic_rows`. `question_pairings`, each concept's `questions` and `question_counts` keep their shape. The `question_pairing_debug` key is gone.
- **Prefetches.** They now read `questions__question_bank_rows` instead of the old pairing links.
- **Deleting a learning object** no longer calls `delete_generated_questions_for`. Generated questions go with their object (foreign key `CASCADE`). Teacher and printed questions paired to it are unpaired by a signal in `question_bank.py`.

### `backend/lessons/serializers.py`
- `QuestionSerializer` and `QuestionLearningObjectLinkSerializer` were removed.
- `ExtractedQuestionSerializer` (used for `material.questions`) has two new read-only fields: `in_bank` and `teacher_reviewed` (the bank copy was edited or paired by the teacher). The delete-PDF dialog uses them.

### Smaller ones
- `lessons/services/concept_grouper.py`: `refresh_material_learning_relationships` no longer re-pairs questions after a grouping edit.
- `lessons/services/lesson_package.py`: reads `ExtractedQuestion`.
- `web-app/src/pages/TopicDetailPage.jsx`:
  - The per-PDF question list shows only the question text, with no pairing status.
  - The delete-PDF dialog has one extra line: "N questions printed in this PDF will also be deleted, including M you edited or paired to a concept."

## 4. Your bank changes (from `MaviaDocling`) that were adjusted

Your rename to `question_generation.Question` (alias `GeneratedQuestion`), the new columns and `QuestionFile` were all kept. Changed afterwards, following the agreed table design:

- **`validation_issues` removed from the bank.** What blocks a question is worked out when it is shown (`question_bank.issues()`).
- **`extracted_question` and `question_file` now use `on_delete=CASCADE`** (were `SET_NULL`).
- **Three database rules on the bank.** The database refuses a row that breaks them, whoever writes it:
  1. `final_question_is_complete`: a `final` question needs `node`, `paired_by`, `question_format` and `thinking_order`.
  2. `source_matches_origin`:
     - `lesson_pdf` points to its extracted question.
     - `questionnaire` points to its file.
     - `generated` and `manual` point to neither.
  3. `one_question_text_per_topic`: one `content_fingerprint` per topic (`outline_node`), drafts excepted.

**For your code:** please write printed questions to the bank only through `_sync_extracted_question_to_bank`. A row written another way can break these rules and the save will fail.

## 5. What Jure needs from you

1. **Docling extraction.** `lesson_pdf_extractor.py` is still PyMuPDF and never sets `docling_label`. Your new `learning_object_builder.py` only splits at Docling headings, so a re-upload of "Lesson 1: Solid, Liquid and Gas" became **one** learning object for the whole lesson. Did the Docling reading step not get pushed?
2. **Printed question shape.** Today every printed question arrives open-ended with no choices and no answer, so the teacher must complete each one. Please send for each item:
   - `question_type` (`true_false` / `multiple_choice`),
   - `choices` as a list of texts with no letters,
   - `correct_answer` equal to one of the choices word for word (`"True"`/`"False"` for true/false),
   - `""` when the PDF has no answer.
3. **Questionnaire files** (only if your reader should also read them). On the sample "States of Matter Questionnaire.pdf" it found 6 of 35 items, all without choices or answers. It also read the answer-key table at the end as lesson content. It would need to:
   - run on a non-lesson file without creating learning objects,
   - read .docx,
   - read an end-of-file answer key.
   - Your reader is already used for questionnaires: the "Upload question PDF" button in the Questions step (`question_generation/services/questionnaire.py`) runs `extract_lesson_pdf_blocks` → `classify_blocks` → `detected_question_payloads` on the file. No learning objects are created. Whatever your reader returns gets better there automatically. One small thing: prompts keep their printed number ("2. Which state of matter…"), so please strip it in `parse_question_structure`.
4. **Learning objects for the learning path:**
   - Store each object's enclosing heading (e.g. "Matter" above "Solid", "Liquid", "Gas").
   - Group same-name concepts across PDFs (topic 2 had two "Everyday Examples").
   - Fix the label cut at a parenthesis ("Stamen (Male Part").
5. **When you merge `updated-jure` into your work,** please keep the removals above. Don't bring back `question_linker.py`, the pairing table, the removed endpoints or the removed `ExtractedQuestion` columns. The question pipeline no longer reads them.

## 6. Older differences you will see in a merge (not part of this change)

`updated-jure` removed some things before your branch was cut, and your branch still has them:
- the old test files under `backend/lessons/test_*.py`, `backend/course/test*.py`, `backend/mobile_course_package/tests.py` and `web-app/src/**/*.test.js` (cleanup on 2026-10-20),
- the course-level learning path screen (`CoursePathPage.jsx`, `learning-path/course*`), deferred to the 100% version,
- `course/question_formatting.py`.

Keep the deletions when merging.
