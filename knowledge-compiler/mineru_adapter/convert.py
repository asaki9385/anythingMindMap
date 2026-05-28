import os
import sys
import glob
from client import upload_and_process_all, download_markdowns


def convert_all(pdf_dir: str, output_dir: str):
    """Batch convert all PDFs in a directory to markdown."""
    pdf_files = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}")
        return

    print(f"Found {len(pdf_files)} PDF files\n")

    # Batch upload and process
    results = upload_and_process_all(pdf_files)

    # Download markdown
    print("\nDownloading markdown files...")
    md_files = download_markdowns(results, output_dir)

    print(f"\nDone: {len(md_files)} markdown files -> {output_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert.py <pdf_dir> [output_dir]")
        sys.exit(1)

    pdf_dir = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(pdf_dir, "_markdown")

    convert_all(pdf_dir, output_dir)
