from time import sleep

from ai_interviewer_worker.jobs.document_ingestion import process_document


def main() -> None:
    sample = process_document("demo-document")
    for item in sample:
        print(f"[worker] {item.document_id}: {item.status} ({item.progress_percent}%)")
        sleep(0.05)


if __name__ == "__main__":
    main()
