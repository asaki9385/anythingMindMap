import os
import re
import fitz


def get_toc(pdf_path: str) -> list:
    """Read PDF table of contents."""
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    doc.close()
    return toc


def detect_chapters_by_content(pdf_path: str) -> list:
    """
    Detect chapter boundaries by scanning text content.
    Looks for patterns like: 第X章, 第X节, Chapter X, etc.
    Returns TOC-style list: [[level, title, page_num], ...]
    """
    doc = fitz.open(pdf_path)
    chapters = []

    # Common chapter patterns (Chinese + English)
    patterns = [
        r'^第[一二三四五六七八九十百千\d]+[章篇编部]',
        r'^第\s*\d+\s*[章节篇]',
        r'^Chapter\s+\d+',
        r'^CHAPTER\s+\d+',
        r'^\d+\.\s+\S',  # "1. Title" style
    ]

    for page_num in range(doc.page_count):
        page = doc[page_num]
        text = page.get_text("text")

        for line in text.split('\n'):
            line = line.strip()
            if not line or len(line) > 50:
                continue

            for pattern in patterns:
                if re.match(pattern, line):
                    # Avoid duplicates on same page
                    if not chapters or chapters[-1][2] != page_num + 1:
                        chapters.append([1, line, page_num + 1])
                    break

    doc.close()
    return chapters


def sanitize_filename(name: str) -> str:
    """Remove invalid characters from filename."""
    invalid_chars = '<>:"/\\|?*'
    for ch in invalid_chars:
        name = name.replace(ch, '_')
    return name.strip()


def split_pdf_by_toc(pdf_path: str, output_dir: str, toc: list = None, max_size_mb: int = 200) -> list:
    """
    Split PDF into chapter-level files based on TOC.

    Args:
        pdf_path: Path to source PDF
        output_dir: Directory for output files
        toc: Table of contents list (if None, reads from PDF)
        max_size_mb: Max file size in MB (default 200)

    Returns:
        List of output file paths
    """
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    if toc is None:
        toc = doc.get_toc()

    if not toc:
        raise ValueError("PDF has no TOC (table of contents)")

    output_files = []

    for i in range(len(toc)):
        level, title, start_page = toc[i]
        # end page = next chapter start - 1, or last page
        if i + 1 < len(toc):
            end_page = toc[i + 1][2] - 1
        else:
            end_page = doc.page_count

        # skip if start == end (empty chapter)
        if start_page > end_page:
            continue

        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)

        filename = sanitize_filename(title) + ".pdf"
        filepath = os.path.join(output_dir, filename)
        new_doc.save(filepath)
        new_doc.close()

        # check size
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        if size_mb > max_size_mb:
            print(f"WARNING: {filename} is {size_mb:.1f}MB (>{max_size_mb}MB)")

        output_files.append(filepath)
        print(f"  {filename}  ({start_page}-{end_page}, {size_mb:.1f}MB)")

    doc.close()
    return output_files


def split_pdf(pdf_path: str, output_dir: str = None) -> list:
    """Main entry: split PDF by TOC or content detection into chapter files."""
    if output_dir is None:
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        output_dir = os.path.join(os.path.dirname(pdf_path), base + "_chapters")

    print(f"Reading: {pdf_path}")
    toc = get_toc(pdf_path)

    if not toc:
        print("No TOC found, detecting chapters by content...")
        toc = detect_chapters_by_content(pdf_path)

    if not toc:
        print("No chapters detected. Splitting by fixed page ranges.")
        toc = _split_by_page_ranges(pdf_path)

    print(f"Found {len(toc)} chapters\n")

    files = split_pdf_by_toc(pdf_path, output_dir, toc)
    print(f"\nDone: {len(files)} files -> {output_dir}")
    return files


def _split_by_page_ranges(pdf_path: str, pages_per_chunk: int = 20) -> list:
    """Fallback: split PDF by fixed page ranges."""
    doc = fitz.open(pdf_path)
    toc = []
    for i in range(0, doc.page_count, pages_per_chunk):
        toc.append([1, f"Part_{i // pages_per_chunk + 1}", i + 1])
    doc.close()
    return toc
