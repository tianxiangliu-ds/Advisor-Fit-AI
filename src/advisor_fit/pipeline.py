"""固定阶段 pipeline：ingest → parse → resolve → enrich → claim → match → draft → validate。

不含 UI 状态；领域逻辑为纯 Python，Streamlit 只负责交互。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel

from advisor_fit.analysis.matching import build_match_report
from advisor_fit.analysis.professor_profile import assemble_professor_profile
from advisor_fit.config import settings
from advisor_fit.ingest.cv import build_student_profile, extract_pdf_text
from advisor_fit.ingest.official_profile import parse_official_profile
from advisor_fit.ingest.webpage import FetchedPage, fetch_official_page
from advisor_fit.llm.claims import generate_and_validate_claims
from advisor_fit.llm.drafting import generate_draft
from advisor_fit.models.common import SourceRecord
from advisor_fit.models.evidence import Claim, Evidence
from advisor_fit.models.match import Draft, MatchReport
from advisor_fit.models.professor import AuthorCandidate, ProfessorProfile
from advisor_fit.models.student import StudentFact, StudentProfile
from advisor_fit.providers.academic import AcademicProvider, AuthorQuery
from advisor_fit.resolution.author import ResolutionResult, resolve_author
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
    evidences: list[Evidence] = []
    sources: list[SourceRecord] = []
    warnings: list[str] = []


def run_pipeline(
    cv_path,
    professor_url: str,
    academic_provider: AcademicProvider,
    llm,
    repository: Repository,
    *,
    fetch_page=None,
    confirmed_fact_ids: list[str] | None = None,
    selected_author_id: str | None = None,
) -> PipelineResult:
    return asyncio.run(
        _run_pipeline(
            cv_path,
            professor_url,
            academic_provider,
            llm,
            repository,
            fetch_page=fetch_page,
            confirmed_fact_ids=confirmed_fact_ids,
            selected_author_id=selected_author_id,
        )
    )


async def _run_pipeline(
    cv_path,
    professor_url: str,
    academic_provider: AcademicProvider,
    llm,
    repository: Repository,
    *,
    fetch_page=None,
    confirmed_fact_ids: list[str] | None = None,
    selected_author_id: str | None = None,
) -> PipelineResult:
    run_id = repository.create_run()
    warnings: list[str] = []
    fetch = fetch_page or fetch_official_page
    confirmed = set(confirmed_fact_ids or [])

    # 1. ingest + parse：本地 CV
    parsed = extract_pdf_text(cv_path)
    student = build_student_profile(parsed)
    if parsed.warnings:
        warnings.extend(parsed.warnings)
    student = _apply_confirmation(student, confirmed)

    # 2. 官方页
    try:
        page: FetchedPage = await fetch(professor_url, settings.advisor_fit_user_agent)
    except Exception as exc:  # noqa: BLE001 - 抓取失败降级
        return _abstain(run_id, student, warnings + [f"官方页获取失败：{exc}"], repository)

    official_source = SourceRecord(
        id=f"src_{run_id[:8]}_official",
        source_type="official_page",
        url=professor_url,
        final_url=page.final_url,
        status_code=page.status_code,
        title=page.title,
        text=page.text,
        retrieved_at=page.retrieved_at,
        content_hash=page.content_hash,
        robots_allowed=page.robots_allowed,
    )
    repository.save_source(run_id, official_source)

    facts = parse_official_profile(page)
    if not facts.name:
        return _abstain(run_id, student, warnings + ["官方页未识别到姓名"], repository)

    anchor = facts.to_anchor()
    official_ev = Evidence(
        id="ev_official_profile",
        source_type="official_page",
        source_url=professor_url,
        title=page.title,
        retrieved_at=page.retrieved_at,
        evidence_text=(page.text or "")[:500],
        author_resolution_status="CONFIRMED",
        source_tier=1,
        content_hash=page.content_hash,
    )

    # 3. resolve：作者候选
    try:
        candidates: list[AuthorCandidate] = await academic_provider.search_authors(
            AuthorQuery(name=anchor.name, institution=anchor.institution)
        )
    except Exception as exc:  # noqa: BLE001 - 学术 API 失败降级
        candidates = []
        warnings.append(f"学术 API 失败：{exc}")

    resolution = resolve_author(anchor, candidates)
    if selected_author_id and resolution.status != "CONFIRMED":
        resolution = ResolutionResult(
            status="CONFIRMED",
            ranked_candidates=resolution.ranked_candidates,
            selected_author_id=selected_author_id,
            reasons=resolution.reasons,
            conflicts=resolution.conflicts,
            confirmed_by="user",
        )

    if resolution.status != "CONFIRMED" or not resolution.selected_author_id:
        repository.save_evidence(run_id, official_ev)
        return _abstain(
            run_id,
            student,
            warnings + ["作者身份无法确认，已停止以避免误归因"],
            repository,
        )

    # 4. enrich：近 5 年论文
    since_year = datetime.now(UTC).year - 5
    try:
        works = await academic_provider.list_recent_works(
            resolution.selected_author_id, since_year
        )
    except Exception as exc:  # noqa: BLE001
        works = []
        warnings.append(f"论文获取失败：{exc}")

    evidences: list[Evidence] = [official_ev]
    for work in works:
        evidences.append(
            Evidence(
                id=f"ev_{work.id}",
                source_type="openalex",
                canonical_id=work.doi,
                title=work.title,
                published_date=str(work.year) if work.year else None,
                evidence_text=work.title,
                author_resolution_status="CONFIRMED",
                source_tier=2,
            )
        )
    for evidence in evidences:
        repository.save_evidence(run_id, evidence)

    # 5. assemble professor
    professor = assemble_professor_profile(
        anchor, works, evidences, current_year=datetime.now(UTC).year
    )

    # 6. match
    match_report = build_match_report(student, professor)

    # 7. claims（受控生成）
    claim_payload = {"evidences": {ev.id: ev for ev in evidences}}
    claims = generate_and_validate_claims(llm, claim_payload)
    claim_validation = validate_claims(claims, {ev.id: ev for ev in evidences})

    # 8. draft（受控生成 + 逐句校验）
    draft = generate_draft(llm, student, professor, match_report)
    draft_validation = validate_draft(draft, student, {ev.id: ev for ev in evidences})
    if not draft_validation.ok:
        draft = _sanitize_draft(draft, draft_validation)

    # 9. persist
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
        resolution=resolution,
        claims=claims,
        claim_validation=claim_validation,
        draft=draft,
        draft_validation=draft_validation,
        evidences=evidences,
        sources=[official_source],
        warnings=warnings,
    )


def _apply_confirmation(profile: StudentProfile, confirmed: set[str]) -> StudentProfile:
    facts: list[StudentFact] = []
    for fact in profile.facts:
        facts.append(
            fact.model_copy(update={"user_confirmed": fact.id in confirmed})
        )
    return profile.model_copy(update={"facts": facts})


def _sanitize_draft(draft: Draft, validation: DraftValidationResult) -> Draft:
    invalid = {e.sentence_index for e in validation.errors if e.sentence_index is not None}
    sentences = [s for i, s in enumerate(draft.sentences) if i not in invalid]
    warnings = list(draft.warnings) + [f"移除 {len(invalid)} 句无来源句子"]
    return Draft(subject=draft.subject, sentences=sentences, warnings=warnings)


def _abstain(
    run_id: str,
    student: StudentProfile,
    warnings: list[str],
    repository: Repository,
) -> PipelineResult:
    repository.set_run_status(run_id, "ABSTAINED")
    empty_professor = ProfessorProfile(professor_id="unresolved", identity_confirmed=False)
    match_report = MatchReport(recommendation="INSUFFICIENT_EVIDENCE")
    return PipelineResult(
        run_id=run_id,
        student=student,
        professor=empty_professor,
        match_report=match_report,
        resolution=ResolutionResult(status="REVIEW_REQUIRED"),
        warnings=warnings,
    )
