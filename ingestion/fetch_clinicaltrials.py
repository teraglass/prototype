"""
ClinicalTrials.gov API v2에서 조건(condition)에 맞는 트라이얼 중
Study Protocol 문서가 첨부된 건을 골라 메타데이터 + PDF 다운로드 URL을 수집한다.

API 응답에는 filter.docs 같은 서버 사이드 문서 필터가 없다 (400 Unknown parameter로 확인됨).
그래서 pageToken으로 넘겨가며 클라이언트에서 documentSection.largeDocumentModule.largeDocs 중
hasProtocol=true 항목을 직접 걸러낸다.

query.cond는 리터럴 조건명이 아니라 파생 MeSH 브라우즈 트리로 매칭한다. 그래서
실제로는 무관한 트라이얼도 섞여 들어온다 (예: condition=epilepsy로 검색했는데 conditions
필드엔 두경부암만 있고, MeSH 조상 노드에 우연히 "Pyridoxine-dependent epilepsy"가 걸려서
매칭된 케이스를 실제로 확인함). 그래서 conditionsModule.conditions 리스트에 조건 문자열이
실제로 포함되는지 한 번 더 클라이언트에서 검증한다.

PDF 다운로드 URL은 API 응답에 없고, CDN 경로 규칙으로 조립해야 한다:
  https://cdn.clinicaltrials.gov/large-docs/{NCT번호 마지막 2자리}/{NCTID}/{filename}
"""

import argparse
import json
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

API_BASE = "https://clinicaltrials.gov/api/v2/studies"
CDN_BASE = "https://cdn.clinicaltrials.gov/large-docs"
PAGE_SIZE = 100


def protocol_pdf_url(nct_id: str, filename: str) -> str:
    last_two = nct_id[-2:]
    return f"{CDN_BASE}/{last_two}/{nct_id}/{filename}"


def extract_protocol_doc(study: dict) -> dict | None:
    large_docs = (
        study.get("documentSection", {})
        .get("largeDocumentModule", {})
        .get("largeDocs", [])
    )
    for doc in large_docs:
        if doc.get("hasProtocol"):
            return doc
    return None


def fetch_studies_with_protocol(condition: str, count: int) -> list[dict]:
    results = []
    page_token = None
    session = requests.Session()
    condition_lower = condition.lower()

    while len(results) < count:
        params = {"query.cond": condition, "pageSize": PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token

        resp = session.get(API_BASE, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for study in data.get("studies", []):
            protocol_doc = extract_protocol_doc(study)
            if protocol_doc is None:
                continue

            id_module = study["protocolSection"]["identificationModule"]
            status_module = study["protocolSection"].get("statusModule", {})
            conditions_module = study["protocolSection"].get("conditionsModule", {})
            conditions = conditions_module.get("conditions", [])

            # query.cond는 파생 MeSH 트리로 매칭돼 무관한 트라이얼도 섞여 든다.
            # 실제 conditions 리스트에 조건 문자열이 있는지 재검증한다.
            if not any(condition_lower in c.lower() for c in conditions):
                continue

            nct_id = id_module["nctId"]

            results.append(
                {
                    "nctId": nct_id,
                    "briefTitle": id_module.get("briefTitle"),
                    "officialTitle": id_module.get("officialTitle"),
                    "organization": id_module.get("organization", {}).get("fullName"),
                    "overallStatus": status_module.get("overallStatus"),
                    "conditions": conditions,
                    "protocolDoc": {
                        "filename": protocol_doc.get("filename"),
                        "label": protocol_doc.get("label"),
                        "date": protocol_doc.get("date"),
                        "sizeBytes": protocol_doc.get("size"),
                        "downloadUrl": protocol_pdf_url(
                            nct_id, protocol_doc["filename"]
                        ),
                    },
                }
            )

            if len(results) >= count:
                break

        page_token = data.get("nextPageToken")
        if not page_token:
            break

        time.sleep(0.2)  # 연속 요청 사이 예의상 지연

    return results


def main():
    parser = argparse.ArgumentParser(
        description="ClinicalTrials.gov에서 프로토콜 문서가 첨부된 트라이얼 수집"
    )
    parser.add_argument("--condition", default="epilepsy")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--output", default=None, help="결과를 저장할 JSON 파일 경로")
    args = parser.parse_args()

    results = fetch_studies_with_protocol(args.condition, args.count)

    if len(results) < args.count:
        print(
            f"경고: {args.count}건을 요청했지만 {len(results)}건만 찾음 "
            f"(condition={args.condition!r}에 프로토콜 문서가 첨부된 트라이얼이 더 없음)",
            file=sys.stderr,
        )

    output_json = json.dumps(results, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"{len(results)}건을 {args.output}에 저장함", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
