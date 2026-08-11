"""
data/chunks/{guidelines,protocols}/*.json 를 읽어 Chroma에 적재한다.

두 코퍼스를 컬렉션 자체를 분리해서 저장한다 (같은 컬렉션에 넣고 메타데이터
필터로 나누지 않음). CLAUDE.md §2의 핵심 설계: 프로토콜은 "검토 대상",
가이드라인은 "대조 기준"이라는 역할이 다른 두 코퍼스이고, retrieval 시점에
"프로토콜 섹션 → 관련 가이드라인만 검색"처럼 항상 한쪽에서 다른 쪽을 찾는
방향으로만 쓰인다. 컬렉션을 분리해두면 이 방향성이 스키마 레벨에서 강제된다.
"""

import argparse
import json
import sys
from pathlib import Path

import chromadb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from retrieval.embeddings import embed_passages

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "data" / "chunks"
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"

EMBED_BATCH_SIZE = 32


def _source_org(doc_id: str) -> str:
    prefix = doc_id.split("_")[0]
    if prefix in ("ICH", "FDA", "MFDS"):
        return prefix
    if prefix.startswith("NCT"):
        return "ClinicalTrials.gov"
    return "unknown"


def _load_chunks(corpus_dir: Path) -> list[dict]:
    chunks = []
    for path in sorted(corpus_dir.glob("*.json")):
        doc_chunks = json.loads(path.read_text(encoding="utf-8"))
        chunks.extend(doc_chunks)
    return chunks


def _to_metadata(chunk: dict) -> dict:
    # Chroma 메타데이터는 str/int/float/bool만 허용 — None은 넣을 수 없다.
    return {
        "doc_id": chunk["doc_id"],
        "source_org": _source_org(chunk["doc_id"]),
        "section_number": chunk["section_number"] or "",
        "section_title": chunk["section_title"] or "",
        "breadcrumb": chunk["breadcrumb"] or "",
        "page_start": chunk["page_start"] or 0,
        "page_end": chunk["page_end"] or 0,
    }


def build_collection(client: chromadb.ClientAPI, name: str, corpus_dir: Path) -> int:
    chunks = _load_chunks(corpus_dir)
    if not chunks:
        print(f"경고: {corpus_dir}에 청크가 없음 — {name} 컬렉션을 건너뜀", file=sys.stderr)
        return 0

    client.delete_collection(name) if name in {c.name for c in client.list_collections()} else None
    collection = client.create_collection(name)

    ids = [f"{c['doc_id']}::{i}" for i, c in enumerate(chunks)]
    texts = [c["text"] for c in chunks]
    metadatas = [_to_metadata(c) for c in chunks]

    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        end = start + EMBED_BATCH_SIZE
        batch_texts = texts[start:end]
        embeddings = embed_passages(batch_texts)
        collection.add(
            ids=ids[start:end],
            embeddings=embeddings,
            documents=batch_texts,
            metadatas=metadatas[start:end],
        )
        print(f"  {name}: {min(end, len(chunks))}/{len(chunks)}", file=sys.stderr)

    return len(chunks)


def main():
    parser = argparse.ArgumentParser(description="가이드라인/프로토콜 청크를 Chroma에 적재")
    parser.add_argument("--vectorstore-dir", default=str(VECTORSTORE_DIR))
    args = parser.parse_args()

    Path(args.vectorstore_dir).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=args.vectorstore_dir)

    n_guidelines = build_collection(client, "guideline", CHUNKS_DIR / "guidelines")
    n_protocols = build_collection(client, "protocol", CHUNKS_DIR / "protocols")

    print(
        f"완료: guideline={n_guidelines}건, protocol={n_protocols}건 -> {args.vectorstore_dir}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
