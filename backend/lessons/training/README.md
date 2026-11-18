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

## Decision-margin review

New uploads store `predicted_category`, `decision_scores`, and the gap between
the largest two scores as `decision_margin`. These are LinearSVC scores, not
probabilities. The model and its predicted categories are unchanged.

`BLOCK_CLASSIFIER_REVIEW_MARGIN` optionally flags margins below the cutoff for
teacher review. It defaults to blank/off: choose a cutoff on held-out labelled
validation data and check review workload and missed errors before enabling it.
Do not tune it on the final test set or interpret the cutoff as a percentage.
Scores/flags are not retroactively computed for existing uploads.

The PDF block review panel also exposes excluded and assessment blocks without
a cutoff. Corrections are allowed only before confirmation/finalization, retain
the original prediction, and record the reviewer and prior category. They do not
rerun extraction, replace all objects, delete questions, or change the model.
Restore heading context before restoring following text. Existing source-linked
objects/questions must be reviewed through their own controls before a category
change; a same-category review simply acknowledges the current prediction.
