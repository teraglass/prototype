"""벡터스토어에 질의해서 retrieval 품질을 눈으로 확인하기 위한 스크립트."""

import argparse
import sys
from pathlib import Path

import chromadb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from retrieval.embeddings import embed_query

ROOT = Path(__file__).resolve().parent.parent
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"


def query(collection_name: str, text: str, top_k: int, vectorstore_dir: str):
    client = chromadb.PersistentClient(path=vectorstore_dir)
    collection = client.get_collection(collection_name)

    result = collection.query(
        query_embeddings=[embed_query(text)],
        n_results=top_k,
    )

    print(f'질의: "{text}" (collection={collection_name})\n')
    for i, (doc, meta, dist) in enumerate(
        zip(result["documents"][0], result["metadatas"][0], result["distances"][0]), start=1
    ):
        print(f"[{i}] distance={dist:.4f}  {meta['doc_id']}  {meta['breadcrumb']}")
        print(f"    {doc[:200]}...")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", choices=["guideline", "protocol"], required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--vectorstore-dir", default=str(VECTORSTORE_DIR))
    args = parser.parse_args()

    query(args.collection, args.query, args.top_k, args.vectorstore_dir)
