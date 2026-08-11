"""
임베딩 래퍼 — intfloat/multilingual-e5-large (로컬, API 키 불필요).

가이드라인 코퍼스(영어: ICH/FDA, 한글: MFDS)와 프로토콜 코퍼스(영어) 사이에
교차언어 검색이 필요해서 다국어 임베딩 모델을 골랐다.

E5 계열 모델은 "query: " / "passage: " 접두어를 붙이지 않으면 성능이 떨어진다
(비대칭 검색 태스크 기준 — HuggingFace 모델 카드에서 확인). 그래서 인덱싱할 때는
passage:, 검색할 때는 query: 를 강제한다.
"""

from sentence_transformers import SentenceTransformer

MODEL_NAME = "intfloat/multilingual-e5-large"

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    prefixed = [f"passage: {t}" for t in texts]
    return model.encode(prefixed, normalize_embeddings=True).tolist()


def embed_query(text: str) -> list[float]:
    model = _get_model()
    return model.encode(f"query: {text}", normalize_embeddings=True).tolist()
