"""人工证据输入版 pipeline：不访问导师官网，也不调用学术检索 API。"""

from __future__ import annotations

from pydantic import BaseModel

from advisor_fit.analysis.matching import build_match_report
from advisor_fit.analysis.professor_profile import assemble_professor_profile
from advisor_fit.ingest.manual_professor import ManualProfessorInput, build_manual_materials
from advisor_fit.llm.analysis import (
    DeepAnalysis,
    DirectionSummary,
    generate_deep_analysis,
    generate_direction_summary,
    sanitize_analysis,
)
from advisor_fit.llm.claims import generate_and_validate_claims
from advisor_fit.llm.drafting import generate_draft
from advisor_fit.models.common import SourceRecord
from advisor_fit.models.evidence import Claim, Evidence
from advisor_fit.models.match import Draft, MatchReport
from advisor_fit.models.professor import ProfessorProfile
from advisor_fit.models.resolution import ResolutionResult
from advisor_fit.models.student import StudentProfile
from advisor_fit.storage.repository import Repository
from advisor_fit.validation.claims import ValidationResult, validate_claims
from advisor_fit.validation.draft import DraftValidationResult, validate_draft


class PipelineResult(BaseModel):
    run_id: str
    student: StudentProfile
    professor: ProfessorProfile
    match_report: MatchReport
    resolution: ResolutionResult
    claims: list[Claim] = []
    claim_validation: ValidationResult = ValidationResult()
    draft: Draft = Draft()
    draft_validation: DraftValidationResult = DraftValidationResult()
    deep_analysis: DeepAnalysis = DeepAnalysis()
    direction_summary: DirectionSummary = DirectionSummary()
    evidences: list[Evidence] = []
    sources: list[SourceRecord] = []
    warnings: list[str] = []


def _confirmed_student(
    student: StudentProfile, confirmed_fact_ids: set[str], *, student_id: str
) -> StudentProfile:
    facts = [
        fact.model_copy(update={"user_confirmed": fact.id in confirmed_fact_ids})
        for fact in student.facts
    ]
    return student.model_copy(update={"student_id": student_id, "facts": facts})


def _sanitize_draft(draft: Draft, validation: DraftValidationResult) -> Draft:
    invalid = {
        error.sentence_index
        for error in validation.errors
        if error.sentence_index is not None
    }
    return Draft(
        subject=draft.subject,
        sentences=[
            sentence
            for index, sentence in enumerate(draft.sentences)
            if index not in invalid
        ],
        warnings=[*draft.warnings, f"移除 {len(invalid)} 句无来源句子"],
    )


def run_manual_pipeline(
    *,
    student: StudentProfile,
    confirmed_fact_ids: set[str],
    professor_input: ManualProfessorInput,
    llm,
    repository: Repository,
    run_id: str | None = None,
    paper_read_confirmed: bool = False,
) -> PipelineResult:
    run_id = run_id or repository.create_run()
    prefix = run_id.replace("-", "")[:10]
    student = _confirmed_student(
        student, confirmed_fact_ids, student_id=f"student_{prefix}"
    )
    materials = build_manual_materials(professor_input, id_prefix=prefix)

    professor = assemble_professor_profile(
        materials.anchor, materials.works, materials.evidences
    ).model_copy(update={"professor_id": f"professor_{prefix}"})
    match_report = build_match_report(student, professor)
    evidence_map = {evidence.id: evidence for evidence in materials.evidences}
    deep_analysis = sanitize_analysis(
        generate_deep_analysis(llm, student, professor), student, evidence_map
    )
    direction_summary = generate_direction_summary(llm, professor)
    direction_summary.evidence_ids = [
        eid for eid in direction_summary.evidence_ids if eid in evidence_map
    ]

    claims = generate_and_validate_claims(llm, {"evidences": evidence_map})
    claim_validation = validate_claims(claims, evidence_map)
    draft = generate_draft(
        llm, student, professor, match_report, paper_read_confirmed=paper_read_confirmed
    )
    draft_validation = validate_draft(draft, student, evidence_map, paper_read_confirmed)
    if not draft_validation.ok:
        draft = _sanitize_draft(draft, draft_validation)
        draft_validation = validate_draft(draft, student, evidence_map, paper_read_confirmed)

    for source in materials.sources:
        repository.save_source(run_id, source)
    for evidence in materials.evidences:
        repository.save_evidence(run_id, evidence)
    repository.save_student_profile(run_id, student)
    repository.save_professor_profile(run_id, professor)
    repository.save_match(run_id, match_report)
    repository.save_draft(run_id, draft)
    for claim in claims:
        repository.save_claim(run_id, claim)
    repository.set_run_status(run_id, "COMPLETED")

    return PipelineResult(
        run_id=run_id,
        student=student,
        professor=professor,
        match_report=match_report,
        resolution=ResolutionResult(
            status="CONFIRMED",
            selected_author_id=professor.professor_id,
            reasons=["user_confirmed_profile", "user_confirmed_papers"],
            confirmed_by="user",
        ),
        claims=claims,
        claim_validation=claim_validation,
        draft=draft,
        draft_validation=draft_validation,
        deep_analysis=deep_analysis,
        direction_summary=direction_summary,
        evidences=materials.evidences,
        sources=materials.sources,
        warnings=[],
    )
