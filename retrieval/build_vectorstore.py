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


def _is_definition(breadcrumb: str) -> bool:
    # ICH E6(R2)는 청크의 41%(65/159)가 "1 GLOSSARY > ..." 아래 용어 정의다. 이
    # 정의 청크들이 "audit trail", "validated" 같은 흔한 단어를 하나씩 담고 있어서
    # 실제 요구사항 조항(예: 5.5 Trial Management)과 retrieval 순위를 놓고 경쟁하며
    # top-k에서 밀어내는 걸 eval로 실제 확인했다 (target이 top-30 중 14위로 밀림).
    # 정의 자체는 "검토 포인트"가 아니라서 대조용 retrieval 후보에서는 제외한다.
    top_level = breadcrumb.split(">")[0].strip().upper()
    return top_level.endswith("GLOSSARY")


def _to_metadata(chunk: dict) -> dict:
    # Chroma 메타데이터는 str/int/float/bool만 허용 — None은 넣을 수 없다.
    breadcrumb = chunk["breadcrumb"] or ""
    return {
        "doc_id": chunk["doc_id"],
        "source_org": _source_org(chunk["doc_id"]),
        "section_number": chunk["section_number"] or "",
        "section_title": chunk["section_title"] or "",
        "breadcrumb": breadcrumb,
        "page_start": chunk["page_start"] or 0,
        "page_end": chunk["page_end"] or 0,
        "is_definition": _is_definition(breadcrumb),
    }


def _contextualize(chunk: dict) -> str:
    # Contextual retrieval: 임베딩에는 breadcrumb(섹션 계층 경로)를 본문 앞에
    # 붙여서 같이 넣는다. eval로 실제 확인한 문제 — SPONSOR 산하의 비슷한 조항들
    # (5.0/5.1/5.15/5.18/5.5)이 본문만 봐서는 서로 잘 구분이 안 됐는데, 정작
    # "이게 5.5 Trial Management, Data Handling 조항이다"라는 제목 정보는
    # 임베딩에 전혀 안 들어가고 있었다. Chroma에 저장하는 documents(citation에
    # 쓰이는 원문)는 그대로 두고, 임베딩 계산에만 이 컨텍스트를 붙인다.
    breadcrumb = chunk["breadcrumb"] or ""
    if not breadcrumb or breadcrumb == "(문서 서두)":
        return chunk["text"]
    return f"{breadcrumb}\n\n{chunk['text']}"


def build_collection(client: chromadb.ClientAPI, name: str, corpus_dir: Path) -> int:
    chunks = _load_chunks(corpus_dir)
    if not chunks:
        print(f"경고: {corpus_dir}에 청크가 없음 — {name} 컬렉션을 건너뜀", file=sys.stderr)
        return 0

    client.delete_collection(name) if name in {c.name for c in client.list_collections()} else None
    collection = client.create_collection(name)

    ids = [f"{c['doc_id']}::{i}" for i, c in enumerate(chunks)]
    texts = [c["text"] for c in chunks]
    contextualized_texts = [_contextualize(c) for c in chunks]
    metadatas = [_to_metadata(c) for c in chunks]

    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        end = start + EMBED_BATCH_SIZE
        embeddings = embed_passages(contextualized_texts[start:end])
        collection.add(
            ids=ids[start:end],
            embeddings=embeddings,
            documents=texts[start:end],
            metadatas=metadatas[start:end],
        )
        print(f"  {name}: {min(end, len(chunks))}/{len(chunks)}", file=sys.stderr)

    return len(chunks)


def main():
    parser = argparse.ArgumentParser(description="가이드라인/프로토콜 청크를 Chroma에 적재")
    parser.add_argument("--vectorstore-dir", default=str(VECTORSTORE_DIR))
    parser.add_argument(
        "--only",
        choices=["guideline", "protocol"],
        help="지정하면 해당 컬렉션만 다시 빌드한다 (다른 쪽은 그대로 둠)",
    )
    args = parser.parse_args()

    Path(args.vectorstore_dir).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=args.vectorstore_dir)

    n_guidelines = n_protocols = None
    if args.only in (None, "guideline"):
        n_guidelines = build_collection(client, "guideline", CHUNKS_DIR / "guidelines")
    if args.only in (None, "protocol"):
        n_protocols = build_collection(client, "protocol", CHUNKS_DIR / "protocols")

    print(
        f"완료: guideline={n_guidelines}건, protocol={n_protocols}건 -> {args.vectorstore_dir}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
