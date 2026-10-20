# PDF processing feature

This package is the application boundary for course-outline and lesson-PDF workflows.

- `serializers.py` validates transport input only.
- `use_cases.py` coordinates models and domain processing services without importing DRF.
- `lessons/views.py` remains the HTTP adapter and formats responses.
- `lessons/services/outline_parser.py` and `content_generator.py` contain reusable processing algorithms.

Learning-object chunk balancing is deterministic. Physical PDF line wraps are reconstructed first.
`LEARNING_OBJECT_MAX_WORDS` is the preferred card size, while
`LEARNING_OBJECT_HARD_MAX_WORDS` is the ceiling (by default, four-thirds of the preferred size).
Coherent objects below the ceiling stay whole; longer objects are globally balanced and split only
between complete authored units so a tiny final card is not stranded. Adjacent undersized objects in
the same section merge only when TF-IDF cosine similarity or a shared relationship structure supports
the merge. Configure the remaining limits with `LEARNING_OBJECT_MIN_WORDS` and
`LEARNING_OBJECT_MERGE_COSINE_THRESHOLD`.

Dependency direction: `views -> feature serializers/use cases -> models and processing services`.
