"""
Eval harness의 골드셋 정의.

라벨을 사람이 매기는 대신 결함 주입(fault injection)으로 만든다 — 임상 프로토콜을
"이게 진짜 GCP 위반인지" 판단할 도메인 전문가가 없는 상태에서 사람이 라벨링하면
신뢰도가 낮다. 대신 ICH E6(R2)/E8(R1)에 실제로 존재하는 요구사항 6개를 골라
(이미 data/chunks/guidelines/*.json에서 실제로 확인한 조항들), 그 요구사항을
충족하는 짧은 프로토콜 문단과 그 핵심 문장만 뺀 버전을 한 쌍씩 만든다.

정답은 우리가 직접 통제해서 만들었으므로 라벨 자체의 신뢰도는 100%다. 다만
텍스트 자체는 실제 프로토콜에서 가져온 게 아니라 이 요구사항 하나만 깨끗하게
격리하려고 새로 쓴 합성 문단이다 — 실제 프로토콜 문장은 여러 요구사항이 뒤섞여
있어서 "이 문장 하나만 뺀 최소 쌍(minimal pair)"을 만들기 어렵기 때문. 이 트레이드
오프는 eval 리포트에도 명시한다.
"""

EVAL_CASES = [
    {
        "id": "irb_approval_of_consent",
        "topic": "동의서는 사용 전 IRB/IEC 승인을 받아야 한다",
        "target_doc_id": "ICH_E6R2_GCP",
        "target_section_keywords": ["4.4", "Communication with IRB"],
        "common_text": (
            "Participants will be approached for informed consent prior to any "
            "study procedures. The study team will review the purpose, risks, "
            "and benefits of participation with each participant and answer "
            "any questions before obtaining a signature."
        ),
        "compliant_addition": (
            "The informed consent form, along with any subsequent amendments, "
            "will be submitted to the site's Institutional Review Board (IRB) "
            "for review, and no participant will be consented using a version "
            "that has not received prior IRB approval."
        ),
    },
    {
        "id": "sample_size_justification",
        "topic": "표본수 산출 근거(power analysis)를 명시해야 한다",
        "target_doc_id": "ICH_E6R2_GCP",
        "target_section_keywords": ["6.9", "Statistics"],
        "common_text": "The study will enroll a total of 120 participants across all sites.",
        "compliant_addition": (
            "This sample size was determined using a power analysis assuming a "
            "two-sided alpha of 0.05 and 80% power to detect a between-group "
            "difference of 0.5 standard deviations, based on effect sizes "
            "observed in prior trials of this intervention."
        ),
    },
    {
        "id": "withdrawal_criteria",
        "topic": "치료/시험절차 중단을 위한 명확한 기준이 있어야 한다",
        "target_doc_id": "ICH_E8R1_General_Considerations",
        "target_section_keywords": ["6.2.2", "Withdrawal Criteria"],
        "common_text": "Participants may discontinue study participation at any time.",
        "compliant_addition": (
            "In addition, the investigator will discontinue study treatment for "
            "any participant who experiences a Grade 3 or higher adverse event "
            "that is possibly, probably, or definitely related to the study "
            "drug, or who becomes pregnant during the treatment period; these "
            "criteria are provided to the site in writing prior to study "
            "initiation."
        ),
    },
    {
        "id": "sae_reporting_timeline",
        "topic": "중대한 이상반응(SAE)은 즉시 스폰서에 보고해야 한다",
        "target_doc_id": "ICH_E6R2_GCP",
        "target_section_keywords": ["4.11", "Safety Reporting"],
        "common_text": (
            "Adverse events will be recorded at each study visit using a "
            "structured case report form."
        ),
        "compliant_addition": (
            "Serious adverse events (SAEs) will be reported to the sponsor "
            "within 24 hours of the site becoming aware of the event, followed "
            "by a detailed written report within 5 business days; the sponsor "
            "will in turn notify the IRB and applicable regulatory authorities "
            "per local requirements."
        ),
    },
    {
        "id": "data_audit_trail",
        "topic": "전자데이터 시스템은 밸리데이션·감사추적·접근통제가 있어야 한다",
        "target_doc_id": "ICH_E6R2_GCP",
        "target_section_keywords": ["5.5", "Data Handling"],
        "common_text": (
            "Study data will be collected and stored using a web-based "
            "electronic data capture (EDC) system."
        ),
        "compliant_addition": (
            "The EDC system has been validated prior to use, maintains a full "
            "audit trail of all data entries and changes with no deletion of "
            "previously entered data, and restricts data-modification access "
            "to a documented list of authorized study personnel."
        ),
    },
    {
        "id": "trial_injury_compensation",
        "topic": "시험 관련 상해 발생 시 치료비 보상 정책이 있어야 한다",
        "target_doc_id": "ICH_E6R2_GCP",
        "target_section_keywords": ["5.8", "Compensation"],
        "common_text": (
            "Participants will not be charged for any study-related procedures "
            "performed as part of this protocol."
        ),
        "compliant_addition": (
            "In the event a participant experiences an injury that is directly "
            "related to study participation, the sponsor will cover the cost "
            "of medically necessary treatment for that injury in accordance "
            "with applicable regulatory requirements."
        ),
    },
]


def build_variants(case: dict) -> tuple[str, str]:
    """(redacted_text, compliant_text) 튜플을 반환한다."""
    redacted = case["common_text"]
    compliant = case["common_text"] + " " + case["compliant_addition"]
    return redacted, compliant
