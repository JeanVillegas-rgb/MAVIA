# Lesson block classifier

`artifacts/block_classifier.joblib` is the trained runtime classifier. It includes
the fitted LinearSVC, encoder name, feature mode, and category labels:
`assessment`, `lesson_content`, and `other`.

The runtime loads this artifact through `lessons.services.block_classifier` and
uses `sentence-transformers/all-MiniLM-L6-v2` to encode the target block and its
context. The sentence-transformer weights are downloaded separately on first
use; they are not embedded in the small classifier artifact.

The preparation, splitting, and training scripts are included for development.
Source PDFs, Label Studio annotations, split datasets, and detailed training
exports are local-only and intentionally excluded from the shared branch.
Running the app uses the supplied artifact and does not require those datasets.
Retraining requires providing the local datasets expected by the scripts.
