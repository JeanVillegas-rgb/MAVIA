import json
from pathlib import Path

from lessons.services.lesson_pdf_extractor import extract_lesson_pdf_blocks


TRAINING_FOLDER = Path(__file__).resolve().parent
PDF_FOLDER = TRAINING_FOLDER / "pdf_samples"
OUTPUT_FOLDER = TRAINING_FOLDER / "labeling_tasks"


def prepare_dataset(pdf_path, output_path):
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)

    blocks, _, _= extract_lesson_pdf_blocks(pdf_path)
    tasks = []

    for index, block in enumerate(blocks):
        previous_text = blocks[index - 1]["text"] if index > 0 else ""
        next_text = (
            blocks[index + 1]["text"]
            if index + 1 < len(blocks)
            else ""
        )

        context = (
            f"Previous block:\n{previous_text}\n\n"
            f"Next block:\n{next_text}"
        )

        tasks.append({
            "data": {
                "text": block["text"],
                "context": context,
                "document_id": str(pdf_path.resolve()),
                "page": block["page"],
                "block_id": block["block_id"],
                "docling_label": block["docling_label"],
            }
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(tasks, file, ensure_ascii=False, indent=2)

    return tasks


def main():
    pdf_files = sorted(
        path for path in PDF_FOLDER.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )

    print(f"Found {len(pdf_files)} PDF files.")

    total_tasks = 0
    failed_files = []

    for pdf_path in pdf_files:
        output_path = OUTPUT_FOLDER / f"{pdf_path.stem}.json"
        print(f"\nProcessing: {pdf_path.name}")

        try:
            tasks = prepare_dataset(pdf_path, output_path)
        except Exception as error:
            failed_files.append(pdf_path.name)
            print(f"FAILED: {type(error).__name__}: {error}")
            continue

        total_tasks += len(tasks)
        print(f"Saved {len(tasks)} tasks → {output_path.name}")

    print(f"\nFinished: {len(pdf_files) - len(failed_files)} PDFs processed.")
    print(f"Total tasks saved this run: {total_tasks}")
    print(f"Output folder: {OUTPUT_FOLDER}")

    if failed_files:
        print("\nPDFs that failed:")
        for filename in failed_files:
            print(f"- {filename}")


if __name__ == "__main__":
    main()