"""data/guidelines, data/protocols 아래 모든 PDF를 청킹해서 data/chunks/ 아래 저장."""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chunking.section_chunker import chunk_and_save

ROOT = Path(__file__).resolve().parent.parent


def run(source_dir: Path, output_dir: Path):
    pdf_paths = sorted(source_dir.glob("*.pdf"))
    if not pdf_paths:
        print(f"no PDFs found in {source_dir}", file=sys.stderr)
        return

    for pdf_path in pdf_paths:
        doc_id = pdf_path.stem
        out_path = chunk_and_save(pdf_path, doc_id, output_dir)
        print(f"{pdf_path.name} -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    run(ROOT / "data" / "guidelines", ROOT / "data" / "chunks" / "guidelines")
    run(ROOT / "data" / "protocols", ROOT / "data" / "chunks" / "protocols")
