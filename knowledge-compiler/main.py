import sys
from parser.pdf_splitter import split_pdf


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <pdf_path> [output_dir]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    split_pdf(pdf_path, output_dir)


if __name__ == "__main__":
    main()
