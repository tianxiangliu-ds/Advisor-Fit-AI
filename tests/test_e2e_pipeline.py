"""Task 11：端到端 pipeline（fixture，不触真实网络/LLM）。"""

from __future__ import annotations

from pathlib import Path

from advisor_fit.export.report import export_markdown
from advisor_fit.ingest.cv import build_student_profile, extract_pdf_text
from advisor_fit.ingest.webpage import FetchedPage
from advisor_fit.llm.claims import ClaimsOutput
from advisor_fit.llm.drafting import DraftOutput
from advisor_fit.models.evidence import Claim, ClaimStatus
from advisor_fit.models.match import DraftSentence, SentenceType
from advisor_fit.models.professor import AuthorCandidate, ExternalIds
from advisor_fit.pipeline import run_pipeline
from advisor_fit.providers.academic import Work
from advisor_fit.storage.repository import Repository


def _make_text_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = "%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n"
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(pdf.encode("latin-1"))


class _FakeProvider:
    async def search_authors(self, query):
        return [
            AuthorCandidate(
                id="A1",
                name="王伟",
                external_ids=ExternalIds(orcid="0000-0002-1234-5678"),
                affiliations=["示例大学"],
            )
        ]

    async def list_recent_works(self, author_id, since_year):
        return [
            Work(id="W1", title="RAG Survey", year=2026, topics=["检索增强生成"]),
            Work(id="W2", title="RAG Eval", year=2025, topics=["检索增强生成"]),
            Work(id="W3", title="RAG Robustness", year=2025, topics=["检索增强生成"]),
        ]


class _FakeLLM:
    def generate(self, *, schema, instructions, payload):
        if schema is ClaimsOutput:
            ev_ids = list(payload.get("evidences", {}).keys())
            claim = (
                Claim(
                    id="c1",
                    text="近三年持续研究检索增强生成",
                    claim_type="observed_research_trend",
                    status=ClaimStatus.SUPPORTED,
                    evidence_ids=ev_ids[:1],
                )
                if ev_ids
                else None
            )
            return ClaimsOutput(claims=[claim] if claim else [])
        if schema is DraftOutput:
            topics = payload.get("professor_topics", [])
            facts = payload.get("student_facts", [])
            sentences = []
            if topics and topics[0].get("evidence_ids"):
                sentences.append(
                    DraftSentence(
                        text="您的团队近期研究检索增强生成。",
                        sentence_type=SentenceType.PROFESSOR_FACT,
                        evidence_ids=topics[0]["evidence_ids"][:1],
                    )
                )
            if facts:
                sentences.append(
                    DraftSentence(
                        text="我在课程项目中实践过相关技术。",
                        sentence_type=SentenceType.STUDENT_FACT,
                        fact_ids=[facts[0]["id"]],
                    )
                )
            sentences.append(
                DraftSentence(text="请问您是否有招生名额？", sentence_type=SentenceType.GENERIC)
            )
            return DraftOutput(subject="联系邮件", sentences=sentences)
        return schema()


async def _fake_fetch(url: str, user_agent: str) -> FetchedPage:
    html = (Path(__file__).parent / "fixtures" / "professor_page.html").read_text(
        encoding="utf-8"
    )
    return FetchedPage(
        final_url=url,
        status_code=200,
        title="王伟 - 计算机学院 - 示例大学",
        html=html,
        robots_allowed=True,
    )


def test_fixture_pipeline_exports_grounded_report(tmp_path):
    cv_pdf = tmp_path / "cv.pdf"
    _make_text_pdf(cv_pdf, "Python PyTorch machine learning")

    parsed = extract_pdf_text(cv_pdf)
    profile = build_student_profile(parsed)
    confirmed = [f.id for f in profile.facts]

    repo = Repository(tmp_path / "app.db")
    result = run_pipeline(
        cv_path=cv_pdf,
        professor_url="https://u.edu/faculty/wang",
        academic_provider=_FakeProvider(),
        llm=_FakeLLM(),
        repository=repo,
        fetch_page=_fake_fetch,
        confirmed_fact_ids=confirmed,
    )

    assert result.match_report.recommendation
    assert result.claim_validation.ok
    assert result.draft_validation.ok
    assert result.professor.identity_confirmed
    assert "证据" in export_markdown(result)


def test_pipeline_abstains_when_author_unresolved(tmp_path):
    cv_pdf = tmp_path / "cv.pdf"
    _make_text_pdf(cv_pdf, "Python")

    parsed = extract_pdf_text(cv_pdf)
    profile = build_student_profile(parsed)
    confirmed = [f.id for f in profile.facts]

    class _NoEvidenceProvider:
        async def search_authors(self, query):
            return [AuthorCandidate(id="A9", name="王伟")]

        async def list_recent_works(self, author_id, since_year):
            return []

    repo = Repository(tmp_path / "app.db")
    result = run_pipeline(
        cv_path=cv_pdf,
        professor_url="https://u.edu/faculty/wang",
        academic_provider=_NoEvidenceProvider(),
        llm=_FakeLLM(),
        repository=repo,
        fetch_page=_fake_fetch,
        confirmed_fact_ids=confirmed,
    )

    assert result.match_report.recommendation == "INSUFFICIENT_EVIDENCE"
    assert "作者身份无法确认" in " ".join(result.warnings)
