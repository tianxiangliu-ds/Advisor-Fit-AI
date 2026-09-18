"""从官方页抽取身份候选事实（姓名、单位、邮箱、外部 ID、方向、招生）。

所有字段初始 user_confirmed=False；用户在 UI 确认后，由 to_anchor() 组装为
OfficialIdentityAnchor 进入作者消歧。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from pydantic import BaseModel

from advisor_fit.ingest.webpage import FetchedPage
from advisor_fit.models.professor import ExternalIds, OfficialIdentityAnchor

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ORCID_RE = re.compile(r"orcid\.org/([0-9Xx]{4}-[0-9Xx]{4}-[0-9Xx]{4}-[0-9Xx]{4})")
_DBLP_RE = re.compile(r"dblp\.org/pid/([\w/\-]+)")
_OPENALEX_RE = re.compile(r"openalex\.org/authors/(A\d+)")


class OfficialPageFacts(BaseModel):
    name: str | None = None
    aliases: list[str] = []
    institution: str | None = None
    department: str | None = None
    title: str | None = None
    email: str | None = None
    homepage: str | None = None
    external_ids: ExternalIds = ExternalIds()
    declared_interests: list[str] = []
    recruiting_statements: list[str] = []
    team_roster: list[str] = []
    user_confirmed: bool = False

    def to_anchor(self) -> OfficialIdentityAnchor:
        if not self.name:
            raise ValueError("cannot build anchor without a confirmed name")
        return OfficialIdentityAnchor(
            name=self.name,
            aliases=self.aliases,
            institution=self.institution,
            department=self.department,
            title=self.title,
            email=self.email,
            email_domain=(self.email.split("@")[1] if self.email and "@" in self.email else None),
            homepage=self.homepage,
            external_ids=self.external_ids,
            declared_interests=self.declared_interests,
        )


def _sections(soup: BeautifulSoup) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        if tag.name in ("h1", "h2", "h3"):
            current = tag.get_text(" ", strip=True)
            sections[current] = ""
        elif current:
            sections[current] += " " + tag.get_text(" ", strip=True)
    return sections


def parse_official_profile(page: FetchedPage) -> OfficialPageFacts:
    html = page.html or ""
    soup = BeautifulSoup(html, "html.parser") if html else None
    text = page.text or ""
    if not text and soup:
        body = soup.body or soup
        text = body.get_text(" ", strip=True)
    title_text = page.title or ""

    # 姓名：优先 h1，其次标题首段
    name: str | None = None
    if soup:
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            name = h1.get_text(strip=True)
    if not name and title_text:
        name = re.split(r"[-|—–]", title_text)[0].strip() or None

    # 邮箱：优先 mailto，其次正则
    email: str | None = None
    if soup:
        mailto = soup.find("a", href=re.compile(r"^mailto:", re.I))
        if mailto and mailto.get("href"):
            email = mailto["href"][len("mailto:") :].strip()
    if not email:
        match = _EMAIL_RE.search(text)
        if match:
            email = match.group(0)

    # 外部 ID
    external = ExternalIds()
    blob = html + "\n" + text
    if m := _ORCID_RE.search(blob):
        external.orcid = m.group(1)
    if m := _DBLP_RE.search(blob):
        external.dblp = m.group(1)
    if m := _OPENALEX_RE.search(blob):
        external.openalex = m.group(1)

    # 机构：正文首个“大学/研究所”词（排除 head/title）
    institution = None
    if found := re.findall(r"[一-龥A-Za-z]{1,}(?:大学|研究所|研究院)", text):
        institution = found[0]

    sections = _sections(soup) if soup else {}
    declared_interests = _topics_for_sections(sections, ("研究", "Research"))
    recruiting = _statements_for_sections(sections, ("招生", "Join", "Open", "Recruit"))

    return OfficialPageFacts(
        name=name,
        institution=institution,
        email=email,
        homepage=page.final_url,
        external_ids=external,
        declared_interests=declared_interests,
        recruiting_statements=recruiting,
    )


def _topics_for_sections(sections: dict[str, str], keywords: tuple[str, ...]) -> list[str]:
    topics: list[str] = []
    for heading, body in sections.items():
        if any(k in heading for k in keywords):
            for part in re.split(r"[、，,;；/]", body):
                part = part.strip()
                if part and len(part) <= 40:
                    topics.append(part)
    return topics


def _statements_for_sections(sections: dict[str, str], keywords: tuple[str, ...]) -> list[str]:
    statements: list[str] = []
    for heading, body in sections.items():
        if any(k in heading for k in keywords):
            body = body.strip()
            if body:
                statements.append(body)
    return statements
