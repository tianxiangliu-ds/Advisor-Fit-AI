"""测试共享 fixture 与假实现（不触真实网络/LLM）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from advisor_fit.ingest.webpage import FetchedPage
from advisor_fit.llm.claims import ClaimsOutput
from advisor_fit.llm.drafting import DraftOutput
from advisor_fit.models.evidence import Claim, ClaimStatus
from advisor_fit.models.match import DraftSentence, SentenceType
from advisor_fit.models.professor import AuthorCandidate, ExternalIds
from advisor_fit.providers.academic import Work

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def cv_text() -> str:
    return (FIXTURES_DIR / "cv_text.txt").read_text(encoding="utf-8")


def make_text_pdf(path: Path, text: str) -> None:
    """生成含 ASCII 文本的最小合法 PDF（Helvetica，仅支持 ASCII）。"""
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


class FakeProvider:
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


class FakeLLM:
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


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_fetch(fixtures_dir):
    async def _fetch(url: str, user_agent: str) -> FetchedPage:
        html = (fixtures_dir / "professor_page.html").read_text(encoding="utf-8")
        return FetchedPage(
            final_url=url,
            status_code=200,
            title="王伟 - 计算机学院 - 示例大学",
            html=html,
            robots_allowed=True,
        )

    return _fetch


@pytest.fixture
def make_cv_pdf(tmp_path):
    def _make(text: str = "Python PyTorch machine learning") -> Path:
        path = tmp_path / "cv.pdf"
        make_text_pdf(path, text)
        return path

    return _make
