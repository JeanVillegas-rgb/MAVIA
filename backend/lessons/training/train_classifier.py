import json
import hashlib
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.svm import LinearSVC

from lessons.training.split_dataset import get_label


TRAINING_FOLDER = Path(__file__).resolve().parent
SPLITS_FOLDER = TRAINING_FOLDER / "splits"
ARTIFACT_PATH = TRAINING_FOLDER / "artifacts" / "block_classifier.joblib"
RESULTS_PATH = TRAINING_FOLDER / "artifacts" / "training_results.json"
ENCODER_NAME = "sentence-transformers/all-MiniLM-L6-v2"

LABELS = ["assessment", "lesson_content", "other"]


def load_examples(split_name):
    input_path = SPLITS_FOLDER / f"{split_name}.json"

    with input_path.open("r", encoding="utf-8") as file:
        tasks = json.load(file)

    target_texts = []
    context_texts = []
    labels = []

    for task in tasks:
        target_texts.append(task["data"]["text"])
        context_texts.append(task["data"]["context"])
        labels.append(get_label(task))

    return target_texts, context_texts, labels


def encode_examples(encoder, target_texts, context_texts):
    options = {
        "batch_size": 16,
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "show_progress_bar": True,
    }

    target_vectors = encoder.encode(target_texts, **options)
    context_vectors = encoder.encode(context_texts, **options)

    combined_vectors = np.concatenate(
        [target_vectors, context_vectors],
        axis=1,
    )

    return target_vectors, combined_vectors


def create_models():
    models = {}

    for c_value in [0.01, 0.1, 1.0]:
        models[f"Linear SVM | C={c_value}"] = LinearSVC(
            C=c_value,
            class_weight="balanced",
            max_iter=10000,
            random_state=42,
        )

        models[f"Logistic Regression | C={c_value}"] = LogisticRegression(
            C=c_value,
            class_weight="balanced",
            max_iter=10000,
            random_state=42,
        )

    return models


def macro_f1(actual, predicted):
    return f1_score(
        actual,
        predicted,
        labels=LABELS,
        average="macro",
        zero_division=0,
    )


def print_misclassified_examples(texts, actual, predicted):
    print("\nMISCLASSIFIED TEST EXAMPLES")

    error_count = 0

    for index, (text, expected, received) in enumerate(
        zip(texts, actual, predicted)
    ):
        if expected == received:
            continue

        error_count += 1
        compact_text = " ".join(text.split())

        print(
            f"\n[{error_count}] Example {index} | "
            f"expected={expected} | predicted={received}"
        )
        print(f"Text: {compact_text}")

    print(f"\nTotal misclassified examples: {error_count}")


def compare_classifiers(
    experiment_name,
    X_train,
    y_train,
    X_val,
    y_val,
):
    print(f"\nFEATURE EXPERIMENT: {experiment_name}")
    print(f"Features per example: {X_train.shape[1]}")

    results = []

    # New, independent models for each feature experiment.
    for model_name, model in create_models().items():
        model.fit(X_train, y_train)

        train_predictions = model.predict(X_train)
        val_predictions = model.predict(X_val)

        train_f1 = macro_f1(y_train, train_predictions)
        val_f1 = macro_f1(y_val, val_predictions)

        print(f"\n=== {model_name} ===")
        print(f"Training macro-F1:   {train_f1:.4f}")
        print(f"Validation macro-F1: {val_f1:.4f}")
        print(
            "Training accuracy:   "
            f"{accuracy_score(y_train, train_predictions):.4f}"
        )
        print(
            "Validation accuracy: "
            f"{accuracy_score(y_val, val_predictions):.4f}"
        )

        print("\nValidation classification report:")
        print(classification_report(
            y_val,
            val_predictions,
            labels=LABELS,
            digits=3,
            zero_division=0,
        ))

        results.append({
            "experiment": experiment_name,
            "model": model_name,
            "train_f1": train_f1,
            "val_f1": val_f1,
            "estimator": model,
        })

    return results


def evaluate_selected_model(
    model,
    X_test,
    y_test,
    test_texts,
):
    predictions = model.predict(X_test)

    print("\nTEST RESULT - SELECTED MODEL")
    print(f"Model: {model}")
    print(f"Test accuracy: {accuracy_score(y_test, predictions):.4f}")
    print(f"Test macro-F1: {macro_f1(y_test, predictions):.4f}")

    print("\nTest classification report:")
    print(classification_report(
        y_test,
        predictions,
        labels=LABELS,
        digits=3,
        zero_division=0,
    ))

    print("\nTest confusion matrix:")
    print(confusion_matrix(y_test, predictions, labels=LABELS))
    print_misclassified_examples(test_texts, y_test, predictions)
    return {
        "accuracy": accuracy_score(y_test, predictions),
        "macro_f1": macro_f1(y_test, predictions),
        "classification_report": classification_report(
            y_test, predictions, labels=LABELS, output_dict=True, zero_division=0,
        ),
        "confusion_matrix_labels": LABELS,
        "confusion_matrix": confusion_matrix(y_test, predictions, labels=LABELS).tolist(),
        "errors": [
            {"index": index, "text": text, "expected": expected, "predicted": str(predicted)}
            for index, (text, expected, predicted) in enumerate(zip(test_texts, y_test, predictions))
            if expected != predicted
        ],
    }


def save_selected_model(model, experiment_name, model_name):
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "encoder_name": ENCODER_NAME,
            "feature_mode": experiment_name,
            "labels": LABELS,
            "model": model,
            "model_name": model_name,
        },
        ARTIFACT_PATH,
    )
    print(f"\nSaved classifier artifact: {ARTIFACT_PATH}")


def main():
    train_texts, train_contexts, y_train = load_examples("train")
    val_texts, val_contexts, y_val = load_examples("validation")
    test_texts, test_contexts, y_test = load_examples("test")

    print(f"Training examples: {len(y_train)}")
    print(f"Validation examples: {len(y_val)}")
    print(f"Test examples: {len(y_test)}")

    encoder = SentenceTransformer(ENCODER_NAME)

    print("\nEncoding training examples...")
    train_target, train_combined = encode_examples(
        encoder,
        train_texts,
        train_contexts,
    )

    print("\nEncoding validation examples...")
    val_target, val_combined = encode_examples(
        encoder,
        val_texts,
        val_contexts,
    )

    print("\nEncoding test examples...")
    test_target, test_combined = encode_examples(
        encoder,
        test_texts,
        test_contexts,
    )

    experiments = [
        ("Target only", train_target, val_target),
        ("Target + context", train_combined, val_combined),
    ]

    results = []

    for experiment_name, X_train, X_val in experiments:
        results.extend(compare_classifiers(
            experiment_name,
            X_train,
            y_train,
            X_val,
            y_val,
        ))

    print("\nFINAL COMPARISON")

    for result in results:
        print(
            f"{result['experiment']} | {result['model']} | "
            f"Train F1: {result['train_f1']:.4f} | "
            f"Validation F1: {result['val_f1']:.4f}"
        )

    best_result = max(results, key=lambda result: result["val_f1"])

    print("\nSELECTED MODEL")
    print(f"Experiment: {best_result['experiment']}")
    print(f"Model: {best_result['model']}")
    print(f"Validation macro-F1: {best_result['val_f1']:.4f}")

    if best_result["experiment"] == "Target only":
        selected_test = test_target
    else:
        selected_test = test_combined

    test_result = evaluate_selected_model(
        best_result["estimator"],
        selected_test,
        y_test,
        test_texts,
    )

    save_selected_model(
        best_result["estimator"],
        best_result["experiment"],
        best_result["model"],
    )

    summary = {
        "selection_metric": "validation macro-F1",
        "encoder_name": ENCODER_NAME,
        "split_counts": {
            "train": dict(Counter(y_train)),
            "validation": dict(Counter(y_val)),
            "test": dict(Counter(y_test)),
        },
        "split_sha256": {
            name: hashlib.sha256((SPLITS_FOLDER / f"{name}.json").read_bytes()).hexdigest()
            for name in ("train", "validation", "test")
        },
        "experiments": [
            {key: value for key, value in result.items() if key != "estimator"}
            for result in results
        ],
        "selected": {
            key: value for key, value in best_result.items() if key != "estimator"
        },
        "test": test_result,
    }
    with RESULTS_PATH.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    print(f"Saved training results: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
