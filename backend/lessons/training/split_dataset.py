import json
from collections import Counter
from pathlib import Path


TRAINING_FOLDER = Path(__file__).resolve().parent
DATASET_PATH = TRAINING_FOLDER / "labeled_data" / "blocks_labeled_v6_reviewed.json"
OUTPUT_FOLDER = TRAINING_FOLDER / "splits"

VALID_LABELS = {"lesson_content", "assessment", "other"}

# For research dataset splitting only, not teacher-upload classification.
DOCUMENT_FAMILIES = {
    "Lesson-1_Solid-Liquid-and-Gas-1.pdf": "states_of_matter",
    "Solid.pdf": "states_of_matter",
    "Solid_Liquid_and_Gas.pdf": "states_of_matter",
    "Solid-Liquid-Gas-2.pdf": "states_of_matter",
    "States_of_Matter_Accessible_Guide.pdf": "states_of_matter",
    "Lesson-4_Reproduction-in-Non-Flowering-Plants.pdf": "plant_reproduction",
    "Reproduction-Among-Flowering-Plants-1.pdf": "plant_reproduction",
    "Grade8_English_Q1W2_Handout.pdf": "english",
    "Lesson_Grouping-Materials-Based-on-Properties (1).pdf": "material_properties",
    "Lesson_Mixtures-and-Their-Characteristics (2).pdf": "mixtures",
    "teachersguide.pdf": "acid_rain_teacher_guide",
    "assessment_source_uploaded.pdf": "uploaded_assessment",
    "Grade_8_Science_Cells_Lesson.pdf": "cells_lesson",
    "Grade_8_Science_Mixtures_Lesson.pdf": "mixtures_lesson",
    "Forces and Motion.pdf": "forces_motion",
    "Light and Sound.pdf": "light_sound",
    "The Solar System.pdf": "solar_system",
}

FAMILY_TO_SPLIT = {
    "states_of_matter": "train",
    "plant_reproduction": "train",
    "english": "train",
    "material_properties": "validation",
    "mixtures": "test",
    "acid_rain_teacher_guide": "train",
    "uploaded_assessment": "test",
    "cells_lesson": "train",
    "mixtures_lesson": "train",
    "forces_motion": "train",
    "light_sound": "validation",
    "solar_system": "test",
}
SECTION_LABELS = {"title", "section_header", "heading"}


def get_label(task):
    labels = []

    for annotation in task.get("annotations", []):
        if annotation.get("was_cancelled", False):
            continue

        for result in annotation.get("result", []):
            if result.get("from_name") == "category":
                labels.extend(result.get("value", {}).get("choices", []))

    if len(labels) != 1 or labels[0] not in VALID_LABELS:
        raise ValueError(
            f"Task {task.get('id')} must have exactly one valid label. "
            f"Found: {labels}"
        )

    return labels[0]

def enrich_tasks(tasks):
    active_section = ""

    for index, task in enumerate(tasks):
        data = task["data"]
        current_label = data.get("docling_label", "")

        if current_label in SECTION_LABELS:
            active_section = data.get("text", "")

        previous_data = tasks[index - 1]["data"] if index > 0 else {}
        next_data = (
            tasks[index + 1]["data"]
            if index + 1 < len(tasks)
            else {}
        )

        previous_label = previous_data.get("docling_label", "")
        next_label = next_data.get("docling_label", "")
        previous_text = previous_data.get("text", "")
        next_text = next_data.get("text", "")

        data["active_section"] = active_section
        data["previous_docling_label"] = previous_label
        data["next_docling_label"] = next_label

        data["context"] = (
            f"Active section:\n{active_section}\n\n"
            f"Current Docling label:\n{current_label}\n\n"
            f"Previous Docling label:\n{previous_label}\n"
            f"Previous block:\n{previous_text}\n\n"
            f"Next Docling label:\n{next_label}\n"
            f"Next block:\n{next_text}"
        )
    
def get_family(document_id):
    filename = Path(document_id).name

    if filename not in DOCUMENT_FAMILIES:
        raise ValueError(
            f"Review the document family for this dataset file: {filename}"
        )

    return DOCUMENT_FAMILIES[filename]


def load_dataset():
    with DATASET_PATH.open("r", encoding="utf-8") as file:
        tasks = json.load(file)

    if not tasks:
        raise ValueError("The dataset is empty.")

    documents = {}
    seen_ids = set()

    for task in tasks:
        task_id = task["id"]

        if task_id in seen_ids:
            raise ValueError(f"Duplicate task ID: {task_id}")

        seen_ids.add(task_id)
        get_label(task)

        document_id = task["data"]["document_id"]
        get_family(document_id)
        documents.setdefault(document_id, []).append(task)

    # The exported context includes real neighbouring blocks, including blocks
    # omitted from the labelled subset. Do not rebuild it across missing blocks.
    for tasks in documents.values():
        tasks.sort(key=lambda task: task["data"]["block_id"])
        for task in tasks:
            if not isinstance(task["data"].get("context"), str):
                raise ValueError(f"Task {task['id']} is missing its exported context.")

    return documents


def summarize_families(documents):
    family_counts = {}

    for document_id, tasks in documents.items():
        family = get_family(document_id)
        counts = family_counts.setdefault(family, Counter())

        for task in tasks:
            counts[get_label(task)] += 1

    print("\nCATEGORY COUNTS BY FAMILY")

    for family, counts in sorted(family_counts.items()):
        print(f"\n{family}: {sum(counts.values())} blocks")

        for label in sorted(VALID_LABELS):
            print(f"  {label}: {counts[label]}")


def save_splits(documents):
    splits = {
        "train": [],
        "validation": [],
        "test": [],
    }
    split_families = {name: set() for name in splits}

    for document_id, tasks in documents.items():
        family = get_family(document_id)
        split_name = FAMILY_TO_SPLIT[family]

        splits[split_name].extend(tasks)
        split_families[split_name].add(family)

    # Validate everything before writing any split files.
    seen_task_ids = set()
    seen_families = set()

    for split_name, tasks in splits.items():
        counts = Counter(get_label(task) for task in tasks)
        missing_labels = VALID_LABELS - set(counts)

        if missing_labels:
            raise ValueError(
                f"{split_name} is missing categories: {missing_labels}"
            )

        task_ids = {task["id"] for task in tasks}

        if seen_task_ids & task_ids:
            raise ValueError("Tasks overlap between splits.")

        if seen_families & split_families[split_name]:
            raise ValueError("Document families overlap between splits.")

        seen_task_ids.update(task_ids)
        seen_families.update(split_families[split_name])

    expected_total = sum(len(tasks) for tasks in documents.values())

    if sum(len(tasks) for tasks in splits.values()) != expected_total:
        raise ValueError("Some tasks were lost or duplicated during splitting.")

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    print("\nSAVING SPLITS")

    for split_name, tasks in splits.items():
        output_path = OUTPUT_FOLDER / f"{split_name}.json"

        with output_path.open("w", encoding="utf-8") as file:
            json.dump(tasks, file, ensure_ascii=False, indent=2)

        print(f"Saved {split_name}: {len(tasks)} blocks")

    print(f"\nOutput folder: {OUTPUT_FOLDER}")


def main():
    documents = load_dataset()

    print(f"Documents: {len(documents)}")

    for document_id, tasks in documents.items():
        print(f"{Path(document_id).name}: {len(tasks)} blocks")

    total_blocks = sum(len(tasks) for tasks in documents.values())
    print(f"\nTotal blocks: {total_blocks}")

    summarize_families(documents)
    save_splits(documents)


if __name__ == "__main__":
    main()
