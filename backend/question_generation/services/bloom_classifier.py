# question_generation/services/bloom_classifier.py

import os
import re
from pathlib import Path

BLOOM_TO_DIFFICULTY = {
    "remember":    "easy",
    "understand":  "easy",
    "apply":       "medium",
    "analyze":     "medium",
    "evaluate":    "hard",
    "create":      "hard",
}

# Bloom's taxonomy describes the KIND of thinking a question demands, not how
# hard it is — a different axis from BLOOM_TO_DIFFICULTY above. The question
# generation pipeline targets this one directly; BLOOM_TO_DIFFICULTY stays in
# use for the adaptive engine's difficulty-based remediation.
#
#   remember / understand / apply  -> LOT (lower order thinking)
#   analyze / evaluate             -> HOT (higher order thinking)
#   create                         -> excluded (None)
#
# `create` maps to None rather than being omitted: the trained model really can
# predict it (it is one of its six classes), so it has to stay a recognised
# level for _normalize_level. None marks it as having no assessable thinking
# order, and the pipeline drops those questions — MAVIA only supports MCQ/TF,
# which cannot assess a "produce something new" task.
BLOOM_TO_THINKING_ORDER = {
    "remember":    "LOT",
    "understand":  "LOT",
    "apply":       "LOT",
    "analyze":     "HOT",
    "evaluate":    "HOT",
    "create":      None,
}

BLOOM_TO_CATEGORY = {
    "remember":    "Facts and Information",
    "understand":  "Meaning",
    "apply":       "Skills",
    "analyze":     "Skills",
    "evaluate":    "Outcome",
    "create":      "Outcome",
}

BT_LABELS = {
    "bt1": "remember",
    "bt2": "understand",
    "bt3": "apply",
    "bt4": "analyze",
    "bt5": "evaluate",
    "bt6": "create",
}

DEFAULT_MODEL_DIR = os.path.join(
    Path(__file__).resolve().parent.parent,
    "classifier", "trained_model"
)
MODEL_DIR = os.getenv("BLOOM_MODEL_DIR", DEFAULT_MODEL_DIR)


class BloomClassifier:
    """
    Classifies question text into Bloom's taxonomy levels.
    Primary: fine-tuned RoBERTa. Fallback: SVM pipeline (if transformers
    or torch are unavailable, or the model folder is missing).
    """

    def __init__(self, backend: str = "auto"):
        self.backend = None

        if backend in ("auto", "roberta"):
            try:
                self._load_roberta()
                self.backend = "roberta"
            except Exception as e:
                if backend == "roberta":
                    raise
                print(f"[BloomClassifier] RoBERTa unavailable ({e}), falling back to SVM")

        if self.backend is None:
            try:
                self._load_svm()
                self.backend = "svm"
            except Exception as e:
                if backend == "svm":
                    raise
                print(f"[BloomClassifier] SVM unavailable ({e}), using rule fallback")
                self.backend = "rules"

        print(f"[BloomClassifier] Using backend: {self.backend}")

    # ── RoBERTa ──
    def _load_roberta(self):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        roberta_path = os.path.join(MODEL_DIR, "roberta_blooms_final")
        self.tokenizer = AutoTokenizer.from_pretrained(roberta_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(roberta_path)
        self.model.eval()
        self.torch = torch

    def _classify_roberta(self, question_text: str) -> str:
        inputs = self.tokenizer(
            question_text,
            return_tensors="pt",
            truncation=True,
            padding=True,
            max_length=128,
        )
        with self.torch.no_grad():
            logits = self.model(**inputs).logits
        pred_id = int(logits.argmax())
        return self.model.config.id2label[pred_id]

    # ── SVM fallback ──
    def _load_svm(self):
        import joblib
        from nltk.stem import WordNetLemmatizer

        svm_path = os.path.join(MODEL_DIR, "bloom_svm_pipeline.joblib")
        self.svm_pipeline = joblib.load(svm_path)
        self.lemmatizer = WordNetLemmatizer()

    def _preprocess_for_svm(self, text: str) -> str:
        # must match the preprocessing used during training
        from nltk.tokenize import word_tokenize
        text = text.lower().strip()
        text = re.sub(r"[^a-zA-Z0-9\s?]", "", text)
        tokens = word_tokenize(text)
        tokens = [self.lemmatizer.lemmatize(t) for t in tokens]
        return " ".join(tokens)

    def _classify_svm(self, question_text: str) -> str:
        cleaned = self._preprocess_for_svm(question_text)
        try:
            return self.svm_pipeline.predict([cleaned])[0]
        except Exception as e:
            print(f"[BloomClassifier] SVM prediction failed ({e}), using rule fallback")
            self.backend = "rules"
            return self._classify_rules(question_text)

    def _classify_rules(self, question_text: str) -> str:
        text = question_text.lower().strip()
        cue_groups = (
            ("create", (
                "create", "design", "construct", "develop", "compose",
                "formulate", "make", "plan", "propose", "invent",
            )),
            ("evaluate", (
                "evaluate", "judge", "justify", "defend", "critique",
                "recommend", "which is best", "which is better",
                "do you agree", "why or why not",
            )),
            ("analyze", (
                "analyze", "compare", "contrast", "differentiate",
                "distinguish", "classify", "categorize", "examine",
                "relationship", "cause",
            )),
            ("apply", (
                "apply", "use", "solve", "demonstrate", "show how",
                "calculate", "choose", "select", "what should",
                "in this situation",
            )),
            ("understand", (
                "explain", "describe", "summarize", "interpret",
                "give an example", "why", "how does", "what happens",
            )),
            ("remember", (
                "define", "identify", "list", "name", "state", "what is",
                "who is", "when", "where", "true or false",
            )),
        )
        for level, cues in cue_groups:
            if any(cue in text for cue in cues):
                return level
        return "understand"

    def _normalize_level(self, raw_level: str) -> str:
        level = str(raw_level or "").strip().lower()
        level = BT_LABELS.get(level, level)
        if level not in BLOOM_TO_DIFFICULTY:
            return "understand"
        return level

    # ── Public API ──
    def classify(self, question_text: str) -> dict:
        if self.backend == "roberta":
            bloom_level = self._classify_roberta(question_text)
        elif self.backend == "svm":
            bloom_level = self._classify_svm(question_text)
        else:
            bloom_level = self._classify_rules(question_text)

        bloom_level = self._normalize_level(bloom_level)

        # thinking_order is None for "create" — callers must treat that as
        # "exclude this question", not as a missing value to backfill.
        return {
            "bloom_level": bloom_level,
            "difficulty": BLOOM_TO_DIFFICULTY[bloom_level],
            "thinking_order": BLOOM_TO_THINKING_ORDER[bloom_level],
            "category": BLOOM_TO_CATEGORY[bloom_level],
        }

    def classify_batch(self, questions: list[str]) -> list[dict]:
        return [self.classify(q) for q in questions]
