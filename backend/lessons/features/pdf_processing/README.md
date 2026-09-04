# PDF processing feature

This package is the application boundary for course-outline and lesson-PDF workflows.

- `serializers.py` validates transport input only.
- `use_cases.py` coordinates models and domain processing services without importing DRF.
- `lessons/views.py` remains the HTTP adapter and formats responses.
- `lessons/services/outline_parser.py` and `content_generator.py` contain reusable processing algorithms.

Learning-object chunk balancing is deterministic. Physical PDF line wraps are reconstructed first,
then oversized objects split only between complete sentences. Adjacent undersized objects in the same section merge only when TF-IDF
cosine similarity or a shared relationship structure supports the merge. Configure the word and
similarity limits with `LEARNING_OBJECT_MIN_WORDS`, `LEARNING_OBJECT_MAX_WORDS`, and
`LEARNING_OBJECT_MERGE_COSINE_THRESHOLD`.

Dependency direction: `views -> feature serializers/use cases -> models and processing services`.
