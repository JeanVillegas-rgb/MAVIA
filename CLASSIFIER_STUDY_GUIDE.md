# MAVIA Bloom's Classifier — Study Guide

A section-by-section walkthrough of the training notebook, with the reasoning
behind each decision. Use this to prepare for defense questions.

---

## The One-Sentence Summary

We train a model that reads a quiz question and labels its **Bloom's Taxonomy
level** (remember, understand, apply, analyze, evaluate, create), because the
LLM that generates questions cannot reliably judge their difficulty on its own.
The classifier is the independent, authoritative judge of difficulty.

---

## Section 1 — Loading the Dataset

**What happens:** Load a CSV of 8,767 questions, each labeled BT1–BT6.

**The data:** Two columns — `Questions` (text) and `Category` (BT1–BT6).
Class distribution is imbalanced: BT1 (remember) has 2,582 questions, BT5
(evaluate) only 783.

**Key facts to remember:**
- 8,767 total questions before cleaning
- 6 classes (the 6 Bloom's levels)
- Labels come as codes BT1–BT6, not words

**Likely question — "Why this dataset?"**
It's a public, pre-labeled Bloom's taxonomy dataset. Published studies in this
area typically use 600–1,800 questions; the largest combined study used 4,179.
At ~8,700 questions, ours is roughly double the biggest published aggregate,
which is more than enough to fine-tune a transformer.

---

## Section 2 — Data Preprocessing

Four steps happen here, each with a reason.

### 2a. Label Mapping (BT1–BT6 → words)
```
BT1→remember, BT2→understand, BT3→apply, BT4→analyze, BT5→evaluate, BT6→create
```
**Why:** Human-readable labels make everything downstream (mappings to
difficulty, confusion matrices, debugging) clearer. Purely cosmetic but
important for interpretation.

### 2b. Deduplication — **the most important preprocessing step**
Removes 71 duplicate questions (8,767 → 8,696).

**Why this matters (know this cold):** The dataset aggregates questions from
multiple sources, so the same question can appear more than once. If a duplicate
lands in *both* the training set and the test set, the model effectively "sees
the answer" during training and gets it free at test time — this **inflates the
accuracy** and makes results look better than they really are. Removing
duplicates *before* splitting guarantees the test set is truly unseen.

**If asked "how did you prevent data leakage?"** → This, plus the pipeline
design in Section 3.

### 2c. Text Cleaning (for the classical models only)
Lowercase, strip punctuation (keep `?`), tokenize, lemmatize.

**Why lemmatize:** Reduces words to their base form ("running"→"run",
"studies"→"study") so the TF-IDF model treats them as the same feature. Shrinks
the vocabulary and helps the classical models generalize.

**Important nuance:** This cleaned text is used ONLY by the classical models
(SVM etc.). The transformers use the RAW text (explained in Section 4).

### 2d. Train-Test Split (80/20, stratified)
6,956 training / 1,740 testing.

**Why 80/20:** The standard split in the Bloom's classification literature.
Enough data to train on, enough held out to evaluate reliably.

**Why stratified:** `stratify=y` forces the train and test sets to have the
**same class proportions** as the full dataset. Without it, random chance could
put most of the rare "evaluate" questions in training and leave the test set
barely able to measure that class. Stratification makes the test set a fair
miniature of the whole.

**Why one shared split for all models:** Every model (classical and transformer)
is evaluated on the *exact same* held-out test set, so the comparison is fair.

---

## Section 3 — Baseline Models (Classical ML)

Trains three traditional models as baselines: **SVM, Logistic Regression,
Naive Bayes**.

### The Pipeline (leak-free design)
TF-IDF vectorizer + classifier are bundled in one `Pipeline` object.

**Why bundle them (know this):** During cross-validation, the data is split into
folds. If you fit TF-IDF on ALL the data first, the vocabulary "learns" from the
validation folds too — another form of leakage. Putting the vectorizer inside
the pipeline means **each fold fits its own vectorizer** on only its training
portion. This gives honest CV scores.

### TF-IDF (how classical models "read" text)
Converts text into numbers based on word frequency. Settings:
- `max_features=5000` — top 5,000 words/phrases only (limits noise)
- `ngram_range=(1,2)` — single words AND two-word phrases (so "compare and"
  is a feature, not just "compare")
- `sublinear_tf=True` — dampens the effect of a word repeated many times
- `min_df=2` — ignore words appearing in only one question (typos, noise)

### GridSearchCV (finding the best SVM settings)
Tests 10 combinations (5 `C` values × 2 kernels) with 5-fold CV = 50 fits.
Winner: `C=1.0, kernel='rbf'`.

**Why:** Instead of guessing hyperparameters, we systematically test and let
cross-validated F1 pick the best. Defensible and reproducible.

### Class Imbalance Handling
`class_weight='balanced'`.

**Why not just make all classes equal size (know this — common question):**
Two options were rejected:
- **Undersampling** (cut every class down to the smallest) would throw away
  ~46% of the data. Transformers need data; this would hurt more than help.
- **Oversampling/SMOTE** risks the model memorizing duplicated examples.

Instead, `class_weight='balanced'` tells the model to **penalize mistakes on
rare classes more heavily** — mathematically similar to oversampling but without
duplicating or discarding any data. **Proof it works:** the two SMALLEST classes
(create, evaluate) have the HIGHEST F1 scores. If imbalance were hurting us,
it would be the opposite.

---

## Section 4 — Transformer Fine-Tuning (the main models)

Fine-tunes two transformers: **DistilBERT** and **RoBERTa**.

### Why transformers beat classical models
Classical models (TF-IDF) only see **word frequency** — "does the word 'compare'
appear?" Transformers understand **context and meaning** — they know
"compare X and Y" (analyze) differs from "X compares favorably" (a statement),
even though both contain "compare." Since Bloom's levels are about *what kind of
thinking* a question demands, meaning matters more than keywords. This is why
the transformers win.

### Why RAW text for transformers (not the cleaned version)
Transformers have their own subword tokenizer and were pretrained on natural
text. Over-cleaning (lemmatizing, stripping punctuation) removes information they
were built to use. So transformers get the original questions; only the classical
models get the lemmatized text.

### Key training settings
- `max_length=128` — questions padded/truncated to 128 tokens
- **Validation split + early stopping** — 10% of training data is held out to
  watch for overfitting; training stops when validation F1 stops improving
  (`patience=3`). **Why:** transformers can overfit small datasets; early
  stopping prevents memorizing the training data.
- `learning_rate=2e-5` — the standard fine-tuning rate for these models

### Why DistilBERT AND RoBERTa
- **DistilBERT** — smaller, faster, proven on this exact task (BloomBERT)
- **RoBERTa** — more robust, higher accuracy, still runs on CPU for deployment

Training both gives a comparison and justifies picking the winner with evidence.

---

## Section 5 — Test and Validation

### The Results (memorize the ranking and the story)
```
Model                 Test Acc    Test F1
Naive Bayes           0.707       0.703
Logistic Regression   0.744       0.743
SVM                   0.775       0.775
DistilBERT            0.806       0.806
RoBERTa               0.813       0.812   ← best
```
**The story:** accuracy climbs steadily from simplest to most sophisticated —
Naive Bayes → LogReg → SVM → DistilBERT → RoBERTa. This progression justifies
choosing a transformer: each jump in model sophistication buys real accuracy.

**Context:** RoBERTa's 81.3% matches published fine-tuned RoBERTa results (~83%)
on this task, so the number is credible and in line with the literature.

### The Two-Level Evaluation — **the most important part for MAVIA**

**6-class (Bloom's level): ~81%.** This is the raw classifier accuracy.

**3-class (difficulty): ~89–90%.** This is what actually matters.

**Why the difficulty number is higher (know this cold):** MAVIA doesn't use the
6 Bloom's levels directly — it maps them into 3 difficulty tiers:
```
remember + understand → easy
apply + analyze       → medium
evaluate (+ create)   → hard
```
The classifier's mistakes are almost all between *adjacent* levels that map to
the *same* tier — e.g., confusing "remember" with "understand" (both → easy).
Those errors don't matter to the system, because both map to the same difficulty.
So at the level MAVIA actually consumes, accuracy is ~90%.

**The honest framing for the panel:** "At the 6-class Bloom's level we achieve
~81%, consistent with published transformer results. But our system consumes a
3-tier difficulty label, and at that operational level accuracy is ~90%, because
residual confusion occurs between adjacent Bloom's levels that collapse into the
same difficulty tier." — This is stronger than claiming a suspicious 95% on 6
classes.

### Confusion Matrix — what to point at
The biggest confusions are remember↔understand and understand↔analyze. These are
genuinely fuzzy boundaries even for human labelers ("What is X?" vs "Describe X").
The model isn't broken; the categories overlap. And crucially, most of these
confusions stay within the same difficulty tier.

---

## Section 6 — Export Models

Two models are saved:

1. **RoBERTa** (`roberta_blooms_final.zip`) — the primary classifier used in the
   Django backend.
2. **SVM pipeline** (`bloom_svm_pipeline.joblib`) — a lightweight fallback if a
   machine can't run the transformer (no torch/GPU). One file with TF-IDF + SVM
   bundled together.

**Why a fallback:** Robustness. If RoBERTa can't load (missing dependencies, weak
hardware), the system degrades to the SVM instead of crashing.

**Note on the SVM export:** It's refit on the FULL dataset before saving. The
evaluation scores came from the 80/20 split (honest measurement), but the
*deployed* copy uses all available data for the best possible real-world model.
This is standard practice — measure on a split, deploy on everything.

---

## The Five Questions You WILL Be Asked (and short answers)

**1. Why do you need a classifier if the LLM already generates by difficulty?**
Because the LLM is unreliable at judging difficulty — in our system it matched
the intended level only 40–67% of the time. The classifier is the independent,
consistent authority; the LLM just generates raw text.

**2. How did you prevent your accuracy from being inflated?**
Deduplication before splitting (removes leaked copies), stratified split (fair
test set), and TF-IDF inside the CV pipeline (no vocabulary leakage).

**3. How did you handle class imbalance?**
Stratified splitting + `class_weight='balanced'`, not resampling. Verified it
worked: the smallest classes are our best-performing ones, and macro F1 (0.83)
is close to weighted F1 (0.81), showing no bias toward large classes.

**4. Why RoBERTa over the others?**
Highest test F1 (0.81) in a fair comparison against 4 other models, and it
understands meaning rather than just word frequency — critical when Bloom's
levels differ by cognitive intent, not keywords.

**5. Your accuracy is only 81% — isn't that low?**
At the 6-class level it matches published results. But our system uses a 3-tier
difficulty label, where accuracy is ~90%, because the classifier's errors occur
between adjacent Bloom's levels that map to the same difficulty tier.

---

## One-Line Glossary

- **Bloom's Taxonomy** — 6-level framework for cognitive complexity of a task
- **TF-IDF** — turns text into numbers by word importance/frequency
- **Lemmatization** — reduce words to base form (running → run)
- **Stratified split** — keep class proportions equal in train/test
- **Data leakage** — test info sneaking into training, inflating scores
- **Cross-validation** — train/test on multiple data folds for a robust estimate
- **GridSearchCV** — systematic hyperparameter search
- **class_weight='balanced'** — penalize rare-class errors more, to fight imbalance
- **Fine-tuning** — adapting a pretrained model to your specific task
- **Early stopping** — halt training when validation stops improving (anti-overfit)
- **F1 score** — balance of precision and recall; better than accuracy for imbalance
- **Macro F1** — average F1 across classes, treating each class equally
