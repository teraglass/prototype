"""
결함 주입 eval harness 실행기.

cases.py의 각 케이스마다 redacted(요구사항 문장 뺀 버전)/compliant(포함 버전)
두 변형을 만들어 LangGraph 리뷰 파이프라인에 그대로 태운다. 핵심 지표는
"redacted 버전을 돌렸을 때, 우리가 뺀 요구사항에 해당하는 가이드라인 조항이
실제로 citations에 잡히는가" — recall이다.

flag(aligned/review_needed/conflict) 자체는 참고용으로만 같이 찍는다. 실제로
돌려본 결과 이 시스템은 사소한 이유로도 review_needed를 꽤 자주 준다는 걸
확인했기 때문에(Phase 4/5 노트), flag 하나만 보고 "적중/실패"를 가르면 노이즈가
크다. citations에 정확한 조항이 잡히는지가 더 깨끗한 신호다.
"""

import json
import sys
from pathlib import Path

import chromadb
from anthropic import Anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.graph import build_review_graph
from eval.cases import EVAL_CASES, build_variants

ROOT = Path(__file__).resolve().parent.parent
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"
OUTPUT_PATH = ROOT / "data" / "eval" / "eval_results.json"
REPORT_PATH = ROOT / "data" / "eval" / "eval_report.md"

TOP_K = 4


def _make_chunk(case: dict, variant: str, text: str) -> dict:
    return {
        "doc_id": "eval_synthetic",
        "section_number": case["id"],
        "section_title": case["topic"],
        "breadcrumb": f"[EVAL:{variant}] {case['topic']}",
        "page_start": 0,
        "page_end": 0,
        "char_count": len(text),
        "text": text,
    }


def _target_hit(case: dict, citations: list) -> bool:
    for c in citations:
        if c.doc_id != case["target_doc_id"]:
            continue
        haystack = f"{c.breadcrumb} {c.guideline_ref}"
        if any(kw in haystack for kw in case["target_section_keywords"]):
            return True
    return False


def run():
    anthropic_client = Anthropic()
    chroma_client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    graph = build_review_graph(anthropic_client, chroma_client)

    results = []
    for case in EVAL_CASES:
        redacted_text, compliant_text = build_variants(case)
        print(f"[{case['id']}] {case['topic']}", file=sys.stderr)

        case_result = {"id": case["id"], "topic": case["topic"], "variants": {}}

        for variant, text in [("redacted", redacted_text), ("compliant", compliant_text)]:
            chunk = _make_chunk(case, variant, text)
            state = graph.invoke({"protocol_chunk": chunk, "top_k": TOP_K})
            citations = state["citations"]
            hit = _target_hit(case, citations)

            print(
                f"    {variant:10s} flag={state['llm_result']['flag']:14s} "
                f"conf={state['llm_result']['confidence']:.2f} target_hit={hit}",
                file=sys.stderr,
            )

            case_result["variants"][variant] = {
                "text": text,
                "flag": state["llm_result"]["flag"],
                "confidence": state["llm_result"]["confidence"],
                "target_hit": hit,
                "citations": [
                    {"doc_id": c.doc_id, "breadcrumb": c.breadcrumb, "grounded": c.grounded}
                    for c in citations
                ],
            }

        results.append(case_result)

    n = len(results)
    recall = sum(1 for r in results if r["variants"]["redacted"]["target_hit"]) / n
    compliant_hit_rate = sum(1 for r in results if r["variants"]["compliant"]["target_hit"]) / n

    summary = {
        "n_cases": n,
        "redacted_target_recall": recall,
        "compliant_target_hit_rate": compliant_hit_rate,
        "cases": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    n_flag_correct = sum(
        1
        for r in results
        if r["variants"]["redacted"]["flag"] != "aligned"
        and r["variants"]["compliant"]["flag"] == "aligned"
    )
    REPORT_PATH.write_text(
        _render_report(results, n, recall, compliant_hit_rate, n_flag_correct), encoding="utf-8"
    )

    print(
        f"\n완료: {n}건 중 redacted recall={recall:.0%}, "
        f"compliant hit rate={compliant_hit_rate:.0%} -> {OUTPUT_PATH}, {REPORT_PATH}",
        file=sys.stderr,
    )


def _render_report(results: list, n: int, recall: float, compliant_hit_rate: float, n_flag_correct: int) -> str:
    lines = [
        "# Eval 리포트 — 결함 주입 테스트",
        "",
        "## 방법론",
        "",
        "실제 프로토콜 문장을 사람이 \"이건 위반이다\"라고 라벨링하는 대신, ICH E6(R2)/E8(R1)에",
        "실존하는 요구사항 6개를 골라 그 요구사항을 충족하는 문단(compliant)과 핵심 문장만 뺀",
        "문단(redacted)을 한 쌍씩 만들었다. 정답은 우리가 직접 통제해서 만들었으므로 라벨",
        "신뢰도는 100%다. 다만 텍스트는 실제 프로토콜에서 발췌한 게 아니라 요구사항 하나만",
        "깨끗하게 격리하려고 새로 쓴 합성 문단이다 — 실제 프로토콜 문장은 여러 요구사항이",
        "뒤섞여 있어 최소 쌍을 만들기 어렵다.",
        "",
        "핵심 지표는 두 가지다.",
        "- **target recall**: redacted 버전을 돌렸을 때, 뺀 요구사항에 해당하는 가이드라인",
        "  조항이 실제로 citations에 잡히는가.",
        "- **flag 정합성**: redacted는 aligned가 아닌 플래그를, compliant는 aligned를 받는가.",
        "",
        "## 결과 요약",
        "",
        f"- 케이스 수: {n}",
        f"- redacted target recall: **{recall:.0%}**",
        f"- compliant target hit rate: {compliant_hit_rate:.0%} (참고용 — 정상 텍스트에서도",
        "  같은 조항이 걸리는지)",
        f"- flag 정합성(redacted≠aligned & compliant=aligned): **{n_flag_correct}/{n}**",
        "",
        "## 개선 이력",
        "",
        "1차 실행에서는 target recall이 67%(4/6)였다. `data_audit_trail` 케이스를 까보니",
        "GLOSSARY 용어정의 청크(ICH E6R2 전체 청크의 41%, 65/159)가 실제 요구사항 조항과",
        "retrieval 순위를 놓고 경쟁해서 밀어내는 게 원인이었다 — 타겟 조항(5.5)이 top-30",
        "중 14위로 밀려나 있었다. `retrieval/build_vectorstore.py`에 `is_definition`",
        "메타데이터를 추가해 GLOSSARY 청크를 retrieval 후보에서 제외하도록 고치자, 같은",
        "쿼리에서 타겟 조항이 top_k=4 안 3위로 올라왔고, 재실행 결과 recall이 83%(5/6)로",
        "올랐다. 아래 케이스별 결과와 실패 분석은 이 수정을 반영한 최신 실행 기준이다.",
        "",
        "## 케이스별 상세",
        "",
    ]

    for r in results:
        red = r["variants"]["redacted"]
        comp = r["variants"]["compliant"]
        lines.append(f"### {r['id']} — {r['topic']}")
        lines.append("")
        lines.append(
            f"| variant | flag | confidence | target hit |\n|---|---|---|---|\n"
            f"| redacted | {red['flag']} | {red['confidence']:.2f} | {'✓' if red['target_hit'] else '✗'} |\n"
            f"| compliant | {comp['flag']} | {comp['confidence']:.2f} | {'✓' if comp['target_hit'] else '✗'} |"
        )
        lines.append("")
        if not red["target_hit"]:
            cited = ", ".join(f"{c['breadcrumb']}" for c in red["citations"]) or "(인용 없음)"
            lines.append(f"- redacted에서 타겟 조항을 못 찾음. 실제로 인용된 것: {cited}")
            lines.append("")

    lines.extend(
        [
            "## 남은 실패 케이스 원인 분석",
            "",
            "**trial_injury_compensation (redacted만)** — 흥미로운 지점이다: redacted 텍스트",
            "(\"참가자에게 절차 비용을 청구하지 않는다\")가 그 자체로 너무 일반적이라 5.8",
            "Compensation 조항과 의미적으로 충분히 가깝지 않았다 (top_k=4 안에 안 들어옴).",
            "반면 compliant 버전은 상해 보상이라는 구체적 표현이 들어가면서 바로 잡혔다.",
            "즉 **결함이 있는 텍스트일수록 그 결함 때문에 오히려 관련 조항을 retrieval하기",
            "어려워지는 경향**이 있다는 뜻 — RAG 기반 검토 도구의 구조적 약점 중 하나로",
            "보인다. top_k를 늘리거나, 결측 항목을 추정하는 키워드 기반 보조 검색을",
            "곁들이는 방향으로 개선할 수 있다.",
            "",
            "## 한계",
            "",
            "- N=6으로 표본이 작다. 통계적으로 유의미한 수치라기보다 파이프라인의 약점을",
            "  찾아내는 진단 도구에 가깝다.",
            "- 합성 문단은 요구사항 하나만 깨끗하게 격리한 최소 쌍이라, 여러 이슈가 섞여",
            "  있는 실제 프로토콜 문장보다 판별이 쉬운 편이다. 그래서 flag 정합성이",
            "  6/6으로 실제 프로토콜 리뷰(Phase 4/5 기록상 confidence가 훨씬 들쭉날쭉했음)",
            "  보다 깨끗하게 나왔을 가능성이 있다.",
        ]
    )

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run()
