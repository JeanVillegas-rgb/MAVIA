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

BLOOM_TO_CATEGORY = {
    "remember":    "Facts and Information",
    "understand":  "Meaning",
    "apply":       "Skills",
    "analyze":     "Skills",
    "evaluate":    "Outcome",
    "create":      "Outcome",
}

MODEL_DIR = os.path.join(
    Path(__file__).resolve().parent.parent,
    "classifier", "trained_model"
)


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
            self._load_svm()
            self.backend = "svm"

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
        return self.svm_pipeline.predict([cleaned])[0]

    # ── Public API ──
    def classify(self, question_text: str) -> dict:
        if self.backend == "roberta":
            bloom_level = self._classify_roberta(question_text)
        else:
            bloom_level = self._classify_svm(question_text)

        return {
            "bloom_level": bloom_level,
            "difficulty": BLOOM_TO_DIFFICULTY[bloom_level],
            "category": BLOOM_TO_CATEGORY[bloom_level],
        }

    def classify_batch(self, questions: list[str]) -> list[dict]:
        return [self.classify(q) for q in questions]