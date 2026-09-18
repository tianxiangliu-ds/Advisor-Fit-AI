# 导师双选 AI 工具 v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 10 个有效开发日内交付一个本地运行的 Streamlit MVP：用户上传一份 CV、输入一位导师官方主页 URL，经人工确认学生事实与作者身份后，获得带证据的导师画像、可解释匹配分析和事实受控的中文邮件草稿。

**Architecture:** 使用固定、可重跑的 Python pipeline：`ingest → parse → resolve → enrich → claim → match → draft → validate → export`。Streamlit 只负责交互，领域逻辑放在纯 Python 模块；SQLite 保存来源、证据、声明和运行状态；LLM 只消费已经确认的证据与事实，不能决定作者身份或补全未知字段。

**Tech Stack:** Python 3.12、Streamlit、Pydantic v2、SQLite（标准库 `sqlite3`）、httpx、BeautifulSoup、trafilatura、pypdf、OpenAlex API、OpenAI Python SDK（通过 provider 接口隔离）、pytest、pytest-asyncio、respx、ruff。

**Spec:** `C:/Users/97854/Documents/Codex/2026-09-18/files-pasted-by-the-user-ai/outputs/导师双选AI工具-MVP立项与技术可行性分析.md`

## Global Constraints

- v0.1 只支持“一份 CV + 一位导师官方主页 URL”，不支持姓名搜索和批量导师处理。
- 不连接邮箱、不提供发送按钮、不自动跟进。
- 不抓取 Google Scholar、CNKI、百度学术。
- 姓名只能召回候选，不能确认作者；身份不确定时必须进入人工选择或停止。
- 任何导师相关结论必须至少引用一个存在的 evidence ID。
- 任何学生经历必须来自 `FACT` 且 `user_confirmed=true` 的 student fact。
- `UNKNOWN` 不得被 LLM 改写成肯定或否定事实。
- 原始 CV 默认仅在本地处理；发送给 LLM 前删除姓名、邮箱、电话、地址和证件信息。
- 真实 CV 不进入 Git；测试只使用匿名 fixtures。
- 数据源失败必须可降级，不能因为一个 API 不可用而伪造完整报告。
- 每个任务遵循红—绿—重构测试循环，并在单独提交中结束。

---

## 1. 交付范围和成功标准

### 1.1 用户能完成的完整流程

1. 启动本地应用。
2. 上传 PDF CV；系统本地提取文本并生成学生事实候选。
3. 用户逐项确认、修改或删除事实。
4. 输入一位导师的学校官方主页 URL。
5. 系统检查 URL、抓取页面、记录来源与时间，抽取姓名、单位、职称、邮箱、公开方向和招生表述。
6. 系统用 OpenAlex 召回作者候选，展示机构、近年论文和匹配证据。
7. 用户确认候选作者；若无法确认，可以停止并导出“证据不足”报告。
8. 系统拉取近 5 年论文，去重后生成 declared/observed 两层导师画像。
9. 系统输出强匹配点、弱匹配点、明显缺口、招生信号和证据充分度。
10. 系统生成中文邮件草稿并展示逐句事实来源。
11. 用户导出 Markdown/JSON，或删除本次运行数据。

### 1.2 发布闸门

| 闸门 | 通过条件 | 不通过时的决定 |
|---|---|---|
| G1 身份安全 | 30–50 位金标准导师上，自动确认的论文归属 precision ≥95%；其余进入人工确认 | 禁用自动确认，全部候选人工选择 |
| G2 证据覆盖 | 导师声明和邮件导师事实的 evidence coverage = 100% | 阻止报告/邮件进入“可导出”状态 |
| G3 学生事实 | 测试集中的虚构学生经历 = 0 | 阻止邮件生成，修复 fact allowlist |
| G4 降级能力 | 官网/API/LLM 任一失败时输出明确状态，不生成伪完整结果 | 不发布 |
| G5 隐私 | fixture 外无 CV 入库/日志；删除操作移除原文和运行数据 | 不发布 |
| G6 可用性 | 5 名目标用户中至少 4 名能在 15 分钟内完成单导师报告 | 精简确认步骤与界面文案 |

---

## 2. 代码目录与职责

项目根目录建议命名为 `advisor-fit-mvp/`：

```text
advisor-fit-mvp/
├─ app.py                         # Streamlit 入口，仅做页面编排
├─ pyproject.toml                 # 运行和开发依赖、pytest/ruff 配置
├─ .env.example                   # 非秘密配置示例
├─ .gitignore                     # 忽略 .env、data、真实上传和导出
├─ README.md                      # 安装、启动、隐私和已知限制
├─ src/advisor_fit/
│  ├─ config.py                   # 环境变量和本地路径
│  ├─ models/
│  │  ├─ common.py               # FactStatus、Confidence、SourceRecord
│  │  ├─ evidence.py             # Evidence、Claim
│  │  ├─ student.py              # StudentFact、StudentProfile
│  │  ├─ professor.py            # AuthorCandidate、ProfessorProfile
│  │  └─ match.py                # MatchDimension、MatchReport、Draft
│  ├─ storage/
│  │  ├─ schema.sql              # SQLite 表结构
│  │  └─ repository.py           # 来源、证据、运行、导出、删除
│  ├─ ingest/
│  │  ├─ cv.py                   # PDF 取文、PII 脱敏、事实候选
│  │  ├─ webpage.py              # robots、URL 安全检查、页面取文
│  │  └─ official_profile.py     # 官方身份锚点候选与用户确认
│  ├─ providers/
│  │  ├─ academic.py             # AcademicProvider Protocol
│  │  └─ openalex.py             # OpenAlex 作者/论文查询与缓存
│  ├─ resolution/
│  │  └─ author.py               # 候选特征、门控和人工确认状态
│  ├─ analysis/
│  │  ├─ professor_profile.py    # 官方声明与论文证据组装
│  │  ├─ topics.py               # 时间窗口、主题输入和趋势条件
│  │  └─ matching.py             # 规则、语义相似和分档结论
│  ├─ llm/
│  │  ├─ provider.py             # StructuredLLM Protocol 与 OpenAI 实现
│  │  ├─ claims.py               # 主题/匹配解释的受控生成
│  │  └─ drafting.py             # 中文邮件草稿生成
│  ├─ validation/
│  │  ├─ claims.py               # evidence ID、UNKNOWN 和日期验证
│  │  └─ draft.py                # 邮件逐句 evidence/fact 覆盖验证
│  ├─ pipeline.py                 # 固定阶段编排，不包含 UI 状态
│  └─ export/
│     └─ report.py               # Markdown/JSON 导出
└─ tests/
   ├─ fixtures/
   │  ├─ cv_text.txt             # 匿名 CV 文本
   │  ├─ professor_page.html     # 匿名官方页
   │  ├─ robots_allow.txt
   │  ├─ robots_deny.txt
   │  ├─ openalex_candidates.json
   │  └─ openalex_works.json
   ├─ test_models.py
   ├─ test_repository.py
   ├─ test_cv_ingest.py
   ├─ test_web_ingest.py
   ├─ test_openalex.py
   ├─ test_author_resolution.py
   ├─ test_professor_profile.py
   ├─ test_topics.py
   ├─ test_matching.py
   ├─ test_claim_validation.py
   ├─ test_draft_validation.py
   └─ test_e2e_pipeline.py
```

---

## 3. 核心接口冻结

以下接口应在 Task 1 冻结，后续任务不能自行改名：

```python
from pathlib import Path
from typing import Protocol

def extract_pdf_text(path: Path) -> "ParsedDocument":
    raise NotImplementedError

def redact_pii(text: str) -> "RedactedText":
    raise NotImplementedError

def build_student_profile(document: "ParsedDocument") -> "StudentProfile":
    raise NotImplementedError

async def fetch_official_page(url: str, user_agent: str) -> "FetchedPage":
    raise NotImplementedError

def parse_official_profile(page: "FetchedPage") -> "OfficialPageFacts":
    raise NotImplementedError

class AcademicProvider(Protocol):
    async def search_authors(self, query: "AuthorQuery") -> list["AuthorCandidate"]:
        raise NotImplementedError

    async def list_recent_works(self, author_id: str, since_year: int) -> list["Work"]:
        raise NotImplementedError

def resolve_author(
    anchor: "OfficialIdentityAnchor",
    candidates: list["AuthorCandidate"],
) -> "ResolutionResult":
    raise NotImplementedError

def assemble_professor_profile(
    anchor: "OfficialIdentityAnchor",
    works: list["Work"],
    evidences: list["Evidence"],
) -> "ProfessorProfile":
    raise NotImplementedError

def build_match_report(
    student: "StudentProfile",
    professor: "ProfessorProfile",
) -> "MatchReport":
    raise NotImplementedError

class StructuredLLM(Protocol):
    def generate(self, *, schema: type["BaseModel"], instructions: str, payload: dict) -> "BaseModel":
        raise NotImplementedError

def validate_claims(claims: list["Claim"], evidences: dict[str, "Evidence"]) -> "ValidationResult":
    raise NotImplementedError

def validate_draft(draft: "Draft", student: "StudentProfile", evidences: dict[str, "Evidence"]) -> "ValidationResult":
    raise NotImplementedError
```

---

### Task 1: 工程骨架、配置和领域模型

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `src/advisor_fit/config.py`
- Create: `src/advisor_fit/models/common.py`
- Create: `src/advisor_fit/models/evidence.py`
- Create: `src/advisor_fit/models/student.py`
- Create: `src/advisor_fit/models/professor.py`
- Create: `src/advisor_fit/models/match.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: 本计划第 3 节列出的 Pydantic 模型和枚举。
- Consumes: 无。

- [ ] **Step 1: 初始化 Git 仓库和 Python 3.12 项目**

Run:

```powershell
mkdir advisor-fit-mvp
cd advisor-fit-mvp
git init
uv init --python 3.12
uv add streamlit pydantic pydantic-settings httpx beautifulsoup4 trafilatura pypdf tenacity openai
uv add --dev pytest pytest-asyncio respx ruff
```

Expected: `uv run python --version` 输出 `Python 3.12.x`。

- [ ] **Step 2: 写模型约束的失败测试**

```python
import pytest
from pydantic import ValidationError
from advisor_fit.models.common import FactStatus
from advisor_fit.models.student import StudentFact, StudentProfile

def test_inferred_fact_cannot_be_confirmed_for_drafting():
    with pytest.raises(ValidationError):
        StudentFact(
            id="fact_1",
            field="experience",
            value="做过大模型训练",
            status=FactStatus.INFERRED,
            source_id="cv_1",
            user_confirmed=True,
        )

def test_profile_returns_only_draftable_facts():
    profile = StudentProfile(
        student_id="s1",
        facts=[StudentFact(
            id="fact_1", field="skill", value="Python",
            status=FactStatus.FACT, source_id="cv_1", user_confirmed=True,
        )],
    )
    assert [f.id for f in profile.draftable_facts()] == ["fact_1"]
```

- [ ] **Step 3: 运行测试并确认红灯**

Run: `uv run pytest tests/test_models.py -v`

Expected: FAIL，原因是模型模块不存在。

- [ ] **Step 4: 实现最小模型和枚举**

关键规则：

```python
class FactStatus(str, Enum):
    FACT = "FACT"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"

class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class StudentFact(BaseModel):
    id: str
    field: str
    value: str | float | int | None
    status: FactStatus
    source_id: str | None
    user_confirmed: bool = False

    @model_validator(mode="after")
    def prevent_confirmed_inference(self):
        if self.status != FactStatus.FACT and self.user_confirmed:
            raise ValueError("only FACT can be confirmed for drafting")
        return self
```

`Claim` 必须包含非空 `evidence_ids`，除非 `status=UNKNOWN`；`DraftSentence` 必须显式声明 `sentence_type` 为 `PROFESSOR_FACT / STUDENT_FACT / GENERIC`。

- [ ] **Step 5: 运行模型测试和静态检查**

Run:

```powershell
uv run pytest tests/test_models.py -v
uv run ruff check src tests
```

Expected: 全部 PASS，无 ruff 错误。

- [ ] **Step 6: 提交**

```powershell
git add pyproject.toml .env.example .gitignore src tests/test_models.py
git commit -m "feat: define evidence-first domain models"
```

---

### Task 2: SQLite 证据账本和运行生命周期

**Files:**
- Create: `src/advisor_fit/storage/schema.sql`
- Create: `src/advisor_fit/storage/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `SourceRecord`、`Evidence`、`Claim`。
- Produces: `Repository.create_run()`、`save_source()`、`save_evidence()`、`save_claim()`、`load_run()`、`delete_run()`。

- [ ] **Step 1: 写来源、证据和删除级联的失败测试**

```python
def test_delete_run_removes_sources_evidence_and_claims(tmp_path):
    repo = Repository(tmp_path / "test.db")
    run_id = repo.create_run()
    repo.save_source(run_id, source_record())
    repo.save_evidence(run_id, evidence_record())
    repo.save_claim(run_id, supported_claim())

    repo.delete_run(run_id)

    assert repo.load_run(run_id) is None
    assert repo.count_rows_for_run(run_id) == 0
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_repository.py -v`

Expected: FAIL，`Repository` 未定义。

- [ ] **Step 3: 建立明确表结构**

`schema.sql` 创建以下表并启用外键：

```sql
PRAGMA foreign_keys = ON;
CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_artifacts_run_kind ON artifacts(run_id, kind);
```

在 MVP 中用 `kind=source/evidence/claim/student_profile/professor_profile/match/draft` 区分对象，避免过早设计七套关系表。

- [ ] **Step 4: 实现 repository 并确保事务原子性**

每个写入方法使用 `with self._connect() as conn:`；`delete_run` 只接受数据库中存在的精确 UUID，不接受路径或通配符。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/test_repository.py -v`

Expected: PASS，并验证删除后总行数为 0。

- [ ] **Step 6: 提交**

```powershell
git add src/advisor_fit/storage tests/test_repository.py
git commit -m "feat: add local evidence ledger"
```

---

### Task 3: CV 本地解析、PII 脱敏和学生确认

**Files:**
- Create: `src/advisor_fit/ingest/cv.py`
- Create: `tests/fixtures/cv_text.txt`
- Test: `tests/test_cv_ingest.py`

**Interfaces:**
- Consumes: 本地 PDF 路径。
- Produces: `ParsedDocument(text, page_count, warnings)`、`RedactedText(text, redactions)`、`StudentProfile`。

- [ ] **Step 1: 写 PII 脱敏和空 PDF 失败测试**

```python
def test_redact_pii_removes_direct_identifiers():
    result = redact_pii("张三 zhang@example.com 13800138000 北京市海淀区")
    assert "张三" not in result.text
    assert "zhang@example.com" not in result.text
    assert "13800138000" not in result.text
    assert set(result.redactions) >= {"name", "email", "phone", "address"}

def test_empty_pdf_returns_blocking_warning(fake_empty_pdf):
    parsed = extract_pdf_text(fake_empty_pdf)
    assert parsed.text == ""
    assert "NO_EXTRACTABLE_TEXT" in parsed.warnings
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_cv_ingest.py -v`

Expected: FAIL，解析函数不存在。

- [ ] **Step 3: 实现解析与脱敏**

实现规则：

- 使用 `pypdf.PdfReader`，逐页提取并保留 `page_number`；
- 不在日志中记录全文；
- 邮箱、手机号和地址通过正则识别；姓名由 UI 输入的可选姓名字符串做精确替换；
- `build_student_profile` 只产生 `user_confirmed=False` 的候选事实；
- GPA、排名、成果数字无法确定时保持缺失，不从描述推断。

- [ ] **Step 4: 增加“未确认事实不可用于草稿”的集成测试**

```python
def test_parsed_facts_are_not_draftable_until_user_confirms():
    profile = build_student_profile(parsed_fixture())
    assert profile.draftable_facts() == []
```

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_cv_ingest.py tests/test_models.py -v`

Expected: PASS。

```powershell
git add src/advisor_fit/ingest tests/fixtures/cv_text.txt tests/test_cv_ingest.py
git commit -m "feat: parse and redact student CV locally"
```

---

### Task 4: 官方网页安全获取与来源记录

**Files:**
- Create: `src/advisor_fit/ingest/webpage.py`
- Create: `src/advisor_fit/ingest/official_profile.py`
- Create: `tests/fixtures/professor_page.html`
- Create: `tests/fixtures/robots_allow.txt`
- Create: `tests/fixtures/robots_deny.txt`
- Test: `tests/test_web_ingest.py`

**Interfaces:**
- Consumes: `url: str`、固定 User-Agent。
- Produces: `FetchedPage(final_url, status_code, title, text, retrieved_at, content_hash, robots_allowed)` 和待用户确认的 `OfficialPageFacts(identity, declared_interests, recruiting_statements, team_roster)`。

- [ ] **Step 1: 写 SSRF、robots 和重定向测试**

```python
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data",
    "file:///etc/passwd",
])
def test_private_or_non_http_urls_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_http_url(url)

@pytest.mark.asyncio
async def test_robots_denial_prevents_page_fetch(respx_mock):
    respx_mock.get("https://u.edu/robots.txt").respond(200, text="User-agent: *\nDisallow: /faculty/")
    with pytest.raises(RobotsDeniedError):
        await fetch_official_page("https://u.edu/faculty/wang", "AdvisorFitBot/0.1 contact@example.com")
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_web_ingest.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现 URL 与抓取边界**

- 只允许 `http`/`https`；解析 DNS 后拒绝 loopback、private、link-local、multicast IP；
- 最多 5 次重定向，每次重新验证目标；
- 连接超时 5 秒、读取超时 15 秒、响应体上限 5 MB；
- 先读 `/robots.txt`，拒绝时不获取目标页；
- 使用 `trafilatura.extract`，失败后降级到 BeautifulSoup 正文；
- 保存短证据片段、标题、最终 URL、时间和 SHA-256，不镜像页面资源。
- `parse_official_profile` 从标题、`mailto:`、正文和页面中的 ORCID/DBLP/OpenAlex 链接生成姓名、单位、邮箱与外部 ID 候选；从“研究方向/Research Interests”和“招生/Join Us”邻近段落生成带原文片段的候选事实；所有字段初始为 `user_confirmed=false`，身份字段在 UI 确认后才组成 `OfficialIdentityAnchor` 并进入 Author Resolution。

- [ ] **Step 4: 验证允许、拒绝和超时三条路径**

Run: `uv run pytest tests/test_web_ingest.py -v`

Expected: PASS；拒绝路径没有第二个目标页请求。

- [ ] **Step 5: 提交**

```powershell
git add src/advisor_fit/ingest/webpage.py tests/fixtures tests/test_web_ingest.py
git commit -m "feat: fetch official pages with robots and SSRF guards"
```

---

### Task 5: OpenAlex Provider、缓存和论文归一化

**Files:**
- Create: `src/advisor_fit/providers/academic.py`
- Create: `src/advisor_fit/providers/openalex.py`
- Create: `tests/fixtures/openalex_candidates.json`
- Create: `tests/fixtures/openalex_works.json`
- Test: `tests/test_openalex.py`

**Interfaces:**
- Consumes: `AuthorQuery(name, institution, topics)`、author ID、年份。
- Produces: `list[AuthorCandidate]`、`list[Work]`。

- [ ] **Step 1: 写 API 映射、日期筛选和重试测试**

```python
@pytest.mark.asyncio
async def test_search_authors_maps_affiliations_and_ids(openalex_mock):
    candidates = await provider.search_authors(AuthorQuery(name="王伟", institution="武汉大学"))
    assert candidates[0].external_ids.openalex == "A123"
    assert "武汉大学" in candidates[0].affiliations

@pytest.mark.asyncio
async def test_429_retries_then_succeeds(respx_mock):
    route = respx_mock.get(url_pattern).mock(side_effect=[
        httpx.Response(429), httpx.Response(200, json=fixture_json())
    ])
    result = await provider.search_authors(query())
    assert route.call_count == 2
    assert result
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_openalex.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现最小 provider**

- 使用官方 `/authors?search=` 召回，最多保留 5 个候选；
- 取 `id/display_name/ids/affiliations/last_known_institutions/works_count`；
- 使用 `/works?filter=author.id:{author_id},from_publication_date:{since_year}-01-01` 获取近 5 年；
- 只请求需要字段，`per_page=100`；
- 429 使用 `Retry-After` 或 1/2/4 秒指数退避，最多 3 次；
- 缓存键由 provider、URL 和查询参数的规范 JSON 组成；默认缓存 7 天；
- DOI 小写规范化；无 DOI 时用 `normalized_title + year` 去重。

- [ ] **Step 4: 添加“API 失败不返回虚构空成功”的测试**

```python
@pytest.mark.asyncio
async def test_provider_failure_returns_explicit_error():
    with pytest.raises(AcademicProviderUnavailable):
        await failing_provider.search_authors(query())
```

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_openalex.py -v`

Expected: PASS。

```powershell
git add src/advisor_fit/providers tests/fixtures/openalex_*.json tests/test_openalex.py
git commit -m "feat: add cached OpenAlex academic provider"
```

---

### Task 6: Author Resolution 与人工确认门控

**Files:**
- Create: `src/advisor_fit/resolution/author.py`
- Create: `tests/fixtures/author_resolution_cases.json`
- Test: `tests/test_author_resolution.py`

**Interfaces:**
- Consumes: `OfficialIdentityAnchor` 和 `list[AuthorCandidate]`。
- Produces: `ResolutionResult(status, ranked_candidates, selected_author_id, reasons, conflicts)`。

- [ ] **Step 1: 先建立 15–20 个匿名化金标准案例**

每条 fixture 必须包含：官方姓名、英文别名、单位、邮箱域、官方论文 DOI/题名、候选列表、正确候选或 `none`。至少包含：同名同学科、同名不同学校、导师调动、拼音变体、无强证据五类。

- [ ] **Step 2: 写“姓名相同不能自动确认”和“强锚点确认”测试**

```python
def test_same_name_only_requires_manual_review():
    result = resolve_author(anchor(name="王伟"), [candidate(name="王伟")])
    assert result.status == "REVIEW_REQUIRED"
    assert result.selected_author_id is None

def test_exact_official_doi_overlap_can_confirm_without_conflict():
    result = resolve_author(
        anchor(name="王伟", official_dois={"10.1/a"}),
        [candidate(id="A1", name="王伟", dois={"10.1/a"}, affiliation="武汉大学")],
    )
    assert result.status == "CONFIRMED"
    assert result.selected_author_id == "A1"
```

- [ ] **Step 3: 实现特征与硬门控**

强证据：官方 ORCID/DBLP/OpenAlex 链接、官方 DOI/题名重合、完整公开邮箱一致。中证据：当前/历史机构一致、两个以上稳定合作者重合。弱证据：主题和城市。重大冲突：同时期明确属于其他机构或完全不同学科。

自动 `CONFIRMED` 只允许：一个强证据且无冲突。两个中证据只能排第一，状态仍为 `REVIEW_REQUIRED`。所有用户选择记录为 `confirmed_by=user`。

- [ ] **Step 4: 对金标准计算 precision 和 coverage**

```python
def evaluate_resolution(cases) -> dict[str, float]:
    results = [resolve_author(case.anchor, case.candidates) for case in cases]
    auto = [pair for pair in zip(cases, results) if pair[1].status == "CONFIRMED"]
    correct = sum(result.selected_author_id == case.expected_author_id for case, result in auto)
    confirmable = sum(case.expected_author_id is not None for case in cases)
    return {
        "auto_precision": correct / len(auto) if auto else 1.0,
        "auto_coverage": len(auto) / confirmable if confirmable else 1.0,
    }
```

测试断言 `auto_precision >= 0.95`；coverage 不设发布下限，避免为覆盖率牺牲精度。

- [ ] **Step 5: 运行并提交**

Run: `uv run pytest tests/test_author_resolution.py -v`

Expected: PASS，并打印自动确认 precision/coverage。

```powershell
git add src/advisor_fit/resolution tests/fixtures/author_resolution_cases.json tests/test_author_resolution.py
git commit -m "feat: gate author identity with auditable evidence"
```

---

### Task 7: 导师画像和时间敏感主题趋势

**Files:**
- Create: `src/advisor_fit/analysis/professor_profile.py`
- Create: `src/advisor_fit/analysis/topics.py`
- Test: `tests/test_professor_profile.py`
- Test: `tests/test_topics.py`

**Interfaces:**
- Consumes: 已确认 `OfficialIdentityAnchor`、近 5 年 `Work`、Evidence。
- Produces: `ProfessorProfile`，区分 `declared_interests` 与 `observed_recent_topics`。

- [ ] **Step 1: 写趋势最低证据要求测试**

```python
def test_single_paper_never_becomes_research_shift():
    trend = infer_trend([work(year=2026, topics=["Agentic RAG"])], current_year=2026)
    assert trend.status == "INSUFFICIENT_EVIDENCE"

def test_trend_requires_three_works_across_two_years():
    works = [work(2026, ["RAG"]), work(2025, ["RAG"]), work(2025, ["RAG"])]
    trend = infer_trend(works, current_year=2026)
    assert trend.status in {"EMERGING", "SUSTAINED"}
    assert len(trend.evidence_ids) == 3
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_professor_profile.py tests/test_topics.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现确定性时间窗口**

- 0–2 年权重 0.60、3 年前 0.25、4–5 年前 0.15；
- 少于 3 篇或只覆盖 1 个年份时为 `INSUFFICIENT_EVIDENCE`；
- 每个 observed topic 保存支持论文的 evidence IDs；
- 招生状态只接受有日期的明确声明；无声明为 `UNKNOWN`；
- 团队只保存 `public_roster_count`，不产生 `actual_team_size` 数字。

- [ ] **Step 4: 组装画像并验证所有非 UNKNOWN 字段有来源**

```python
def test_non_unknown_professor_fields_have_evidence(profile):
    for field in profile.iter_asserted_fields():
        assert field.evidence_ids
```

- [ ] **Step 5: 运行并提交**

Run: `uv run pytest tests/test_professor_profile.py tests/test_topics.py -v`

Expected: PASS。

```powershell
git add src/advisor_fit/analysis tests/test_professor_profile.py tests/test_topics.py
git commit -m "feat: build time-aware professor profiles"
```

---

### Task 8: 可解释匹配引擎

**Files:**
- Create: `src/advisor_fit/analysis/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: 已确认 `StudentProfile`、`ProfessorProfile`。
- Produces: `MatchReport`，包含维度、证据、缺口、机会信号和四档建议，不含百分比。

- [ ] **Step 1: 写输出分档和禁止百分比测试**

```python
def test_match_report_separates_fit_from_recruiting():
    report = build_match_report(strong_student(), professor(recruiting="UNKNOWN"))
    assert report.research_fit == "STRONG"
    assert report.opportunity_signal == "UNKNOWN"

def test_public_report_has_no_percentage_score():
    report = build_match_report(strong_student(), matching_professor())
    assert not hasattr(report, "match_percentage")
    assert report.recommendation in {
        "WORTH_CONTACTING", "LEARN_MORE", "LOW_PRIORITY", "INSUFFICIENT_EVIDENCE"
    }
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_matching.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现规则优先的分维度匹配**

- 硬门控：作者身份未确认、学生事实未确认、导师证据不足 → `INSUFFICIENT_EVIDENCE`；
- 主题匹配：学生明确兴趣与 observed topic 的词法/embedding 相似；
- 项目方法匹配：只比较确认项目事实和论文/主题中的方法词；
- 技能匹配：输出“已有”“邻近”“缺失”，不把相似技能写成已掌握；
- 招生信号完全独立；
- `WORTH_CONTACTING` 需要至少一个强经历连接和中等以上证据覆盖；
- 输出每一项的 `student_fact_ids` 和 `professor_evidence_ids`。

首版先用可解释关键词和同义词表；本地 embedding 作为可选增强，不把模型下载作为 MVP 阻塞项。

- [ ] **Step 4: 写反例测试**

```python
def test_keyword_overlap_without_project_evidence_is_not_strong():
    report = build_match_report(student_interest_only("LLM"), professor_topic("LLM"))
    assert report.research_fit != "STRONG"
    assert report.recommendation != "WORTH_CONTACTING"
```

- [ ] **Step 5: 运行并提交**

Run: `uv run pytest tests/test_matching.py -v`

Expected: PASS。

```powershell
git add src/advisor_fit/analysis/matching.py tests/test_matching.py
git commit -m "feat: add explainable multidimensional matching"
```

---

### Task 9: 受控 LLM 生成和 Claim Validator

**Files:**
- Create: `src/advisor_fit/llm/provider.py`
- Create: `src/advisor_fit/llm/claims.py`
- Create: `src/advisor_fit/validation/claims.py`
- Test: `tests/test_claim_validation.py`

**Interfaces:**
- Consumes: evidence allowlist、确认事实、Pydantic 输出 schema。
- Produces: `list[Claim]` 和 `ValidationResult`。

- [ ] **Step 1: 写不存在 evidence ID、日期矛盾和 UNKNOWN 越权测试**

```python
def test_claim_with_unknown_evidence_id_is_rejected():
    result = validate_claims(
        [Claim(text="导师转向大模型", status="SUPPORTED", evidence_ids=["missing"])],
        evidences={},
    )
    assert not result.ok
    assert result.errors[0].code == "UNKNOWN_EVIDENCE_ID"

def test_unknown_recruiting_cannot_become_open():
    result = validate_claims([recruiting_claim("正在招生", ["ev_no_recruiting"])], evidence_map())
    assert not result.ok
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_claim_validation.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现单一 StructuredLLM adapter**

配置项固定为：

```text
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=
LLM_STORE=false
```

`StructuredLLM.generate()` 只返回 Pydantic schema；payload 中只传短证据文本、证据 ID、已脱敏学生事实。v0.1 只保证一套经过测试的 provider 配置；其他 OpenAI-compatible 服务属于部署者自测范围。

- [ ] **Step 4: 实现 deterministic validator**

逐条检查：evidence ID 存在；claim subject 一致；发布日期在陈述窗口内；`UNKNOWN` 不能支持肯定句；论文 author resolution 必须为 `CONFIRMED`；任何失败返回错误码并移除该 claim。

- [ ] **Step 5: 用 fake LLM 测试恶意/幻觉输出**

```python
class FakeLLM:
    def generate(self, **_):
        return ClaimsOutput(claims=[
            Claim(text="导师目前招收 6 名博士", status="SUPPORTED", evidence_ids=["ev_profile"])
        ])

def test_hallucinated_team_size_is_removed():
    claims = generate_and_validate_claims(FakeLLM(), safe_payload())
    assert claims == []
```

- [ ] **Step 6: 运行并提交**

Run: `uv run pytest tests/test_claim_validation.py -v`

Expected: PASS。

```powershell
git add src/advisor_fit/llm src/advisor_fit/validation/claims.py tests/test_claim_validation.py
git commit -m "feat: constrain llm claims to known evidence"
```

---

### Task 10: 邮件草稿与逐句事实验证

**Files:**
- Create: `src/advisor_fit/llm/drafting.py`
- Create: `src/advisor_fit/validation/draft.py`
- Test: `tests/test_draft_validation.py`

**Interfaces:**
- Consumes: `MatchReport`、draftable student facts、validated professor claims。
- Produces: `Draft(subject, sentences, warnings)` 与逐句 provenance。

- [ ] **Step 1: 写学生事实、导师事实和禁用措辞测试**

```python
def test_student_sentence_requires_confirmed_fact_id():
    draft = Draft(sentences=[DraftSentence(
        text="我曾发表三篇顶会论文。",
        sentence_type="STUDENT_FACT",
        fact_ids=["missing"],
        evidence_ids=[],
    )])
    result = validate_draft(draft, student_profile(), evidence_map())
    assert not result.ok

@pytest.mark.parametrize("phrase", ["拜读了您的论文", "久仰大名", "深受震撼"])
def test_unconfirmed_flattery_is_rejected(phrase):
    result = validate_draft(draft_with(phrase), student_profile(), evidence_map())
    assert not result.ok
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_draft_validation.py -v`

Expected: FAIL。

- [ ] **Step 3: 实现邮件 schema 和生成约束**

- 中文正文目标 180–350 字；
- 结构：身份/目的 1–2 句、最强经历连接 2–3 句、导师真实研究连接 1–2 句、明确询问与附件说明、收尾；
- 不写“已阅读某论文”，除非用户额外勾选 `paper_read_confirmed`；
- 每个 `PROFESSOR_FACT` 句必须带 evidence IDs；每个 `STUDENT_FACT` 句必须带 fact IDs；
- 验证失败时删除句子并返回 warning，不自动用另一条无来源句子替换。

- [ ] **Step 4: 写 100% coverage 测试**

```python
def test_valid_draft_has_full_provenance_coverage():
    result = validate_draft(valid_draft(), confirmed_student(), evidence_map())
    assert result.ok
    assert result.professor_fact_coverage == 1.0
    assert result.student_fact_coverage == 1.0
```

- [ ] **Step 5: 运行并提交**

Run: `uv run pytest tests/test_draft_validation.py -v`

Expected: PASS。

```powershell
git add src/advisor_fit/llm/drafting.py src/advisor_fit/validation/draft.py tests/test_draft_validation.py
git commit -m "feat: generate provenance-locked email drafts"
```

---

### Task 11: Streamlit 主流程、导出和删除

**Files:**
- Create: `app.py`
- Create: `src/advisor_fit/pipeline.py`
- Create: `src/advisor_fit/export/report.py`
- Create: `tests/test_e2e_pipeline.py`

**Interfaces:**
- Consumes: Tasks 1–10 的稳定接口。
- Produces: `run_pipeline(cv_path, professor_url, academic_provider, llm, repository) -> PipelineResult`、可运行 UI、Markdown/JSON 导出和删除功能。

- [ ] **Step 1: 写不调用真实网络/LLM 的端到端失败测试**

```python
def test_fixture_pipeline_exports_grounded_report(tmp_path, fake_provider, fake_llm):
    result = run_pipeline(
        cv_path=fixture_cv_pdf(),
        professor_url="https://u.edu/faculty/demo",
        academic_provider=fake_provider,
        llm=fake_llm,
        repository=Repository(tmp_path / "app.db"),
    )
    assert result.match_report.recommendation
    assert result.claim_validation.ok
    assert result.draft_validation.ok
    assert "证据" in export_markdown(result)
```

- [ ] **Step 2: 运行并确认红灯**

Run: `uv run pytest tests/test_e2e_pipeline.py -v`

Expected: FAIL，pipeline 尚未编排。

- [ ] **Step 3: 实现五步 Streamlit wizard**

页面状态固定为：

1. `输入`：CV、可选姓名、官方 URL；
2. `确认学生事实`：逐项编辑、勾选，未确认不能继续；
3. `确认导师身份`：显示最多 5 个候选、机构、近年论文、支持/冲突证据；
4. `报告`：导师画像、匹配分维度、UNKNOWN、证据卡片；
5. `邮件与导出`：逐句来源、Markdown/JSON 下载、删除本次数据。

UI 不显示百分比，不显示“发送”。所有阻塞错误使用明确动作：重试、粘贴页面文本、人工选候选、停止并导出不足报告。

- [ ] **Step 4: 实现导出**

Markdown 必须包含：运行时间、来源列表、身份决策、导师画像、匹配维度、缺口、招生状态、邮件草稿、warnings。JSON 使用 Pydantic `model_dump(mode="json")`，不得含原始 CV 文本或 API Key。

- [ ] **Step 5: 实现删除动作并测试**

点击删除后调用 `Repository.delete_run(run_id)`，同时删除该运行的临时上传文件；路径必须由 run ID 到已知上传目录的映射产生，不能接受用户传入删除路径。

- [ ] **Step 6: 运行全套测试并本地启动**

Run:

```powershell
uv run pytest -q
uv run ruff check src tests app.py
uv run streamlit run app.py
```

Expected: pytest 0 failures；ruff 0 errors；浏览器可完成 fixture 主流程。

- [ ] **Step 7: 提交**

```powershell
git add app.py src/advisor_fit/export tests/test_e2e_pipeline.py
git commit -m "feat: deliver local evidence-first MVP workflow"
```

---

### Task 12: 金标准评测、隐私检查和发布文档

**Files:**
- Create: `evals/gold_author_resolution.jsonl`
- Create: `evals/gold_claims.jsonl`
- Create: `scripts/run_evals.py`
- Create: `README.md`
- Modify: `.gitignore`
- Test: `tests/test_privacy_invariants.py`

**Interfaces:**
- Consumes: 完整 pipeline。
- Produces: 可复现评测报告和本地发布说明。

- [ ] **Step 1: 扩展到 30–50 位匿名金标准导师**

样本至少覆盖 5 所中国高校、5 类常见中文姓名、计算机/AI/数学/信息管理至少三个学科分组。公开来源 URL 可保留；个人邮箱和学生材料必须删除或散列。

- [ ] **Step 2: 写隐私失败测试**

```python
def test_export_and_logs_do_not_contain_direct_pii(caplog, pipeline_result):
    exported = export_json(pipeline_result)
    combined = exported + caplog.text
    assert "13800138000" not in combined
    assert "student@example.com" not in combined
    assert "LLM_API_KEY" not in combined
```

- [ ] **Step 3: 实现评测脚本**

输出：author auto precision、auto coverage、manual-review rate、claim evidence coverage、unsupported claim count、draft professor/student fact coverage、各失败类型数量。任何 G1–G4 闸门不通过时进程退出码为 1。

- [ ] **Step 4: 写 README**

README 包含：产品边界、安装、配置、启动、测试、删除数据、支持/不支持的数据源、隐私模型、`UNKNOWN` 定义、已知限制、禁止批量套磁声明。

- [ ] **Step 5: 运行发布验证**

Run:

```powershell
uv run pytest -q
uv run ruff check src tests scripts app.py
uv run python scripts/run_evals.py
git status --short
```

Expected: pytest 0 failures；ruff 0 errors；评测脚本退出码 0；Git 不出现 `.env`、`data/`、真实 PDF 或 API Key。

- [ ] **Step 6: 提交**

```powershell
git add evals scripts README.md .gitignore tests/test_privacy_invariants.py
git commit -m "docs: add reproducible evaluation and privacy release checks"
```

---

## 4. 需求追踪矩阵

| v0.1 需求 | 实施任务 | 验证证据 |
|---|---|---|
| CV 本地解析和学生确认 | Task 1、3、11 | `test_cv_ingest.py`、wizard Step 2 |
| PII 脱敏和原始 CV 不外泄 | Task 3、12 | `test_privacy_invariants.py` |
| 官方 URL、robots、SSRF 防护 | Task 4 | `test_web_ingest.py` |
| 官方身份与研究/招生候选事实 | Task 4、7 | `test_professor_profile.py` |
| OpenAlex 作者和近 5 年论文 | Task 5 | `test_openalex.py` |
| 作者消歧与人工确认 | Task 6、11 | 金标准 precision、wizard Step 3 |
| declared/observed 分层画像 | Task 7 | `test_professor_profile.py`、`test_topics.py` |
| 分维度匹配、无百分比 | Task 8 | `test_matching.py` |
| claim-level evidence | Task 2、9 | `test_claim_validation.py` |
| 学生/导师事实锁定邮件 | Task 10 | `test_draft_validation.py` |
| Markdown/JSON 导出 | Task 11 | `test_e2e_pipeline.py` |
| 一键删除运行数据 | Task 2、11 | repository 删除级联测试 |
| 数据源/LLM 失败降级 | Task 4、5、9、11 | 各失败路径测试和端到端 fixture |
| 可复现评测与发布文档 | Task 12 | `scripts/run_evals.py` 退出码 0 |

---

## 5. 10 个开发日排期

| 日期槽 | 主任务 | 当日必须可演示的成果 | 决策点 |
|---|---|---|---|
| Day 1 | Task 1–2 | 模型约束和本地证据账本测试通过 | schema 不再随意改名 |
| Day 2 | Task 3 | 匿名 CV 可解析、脱敏、确认 | 复杂版式失败则允许手工编辑，不做 OCR |
| Day 3 | Task 4 | 官方页允许/拒绝/超时均有明确结果 | 动态页先用粘贴文本 fallback |
| Day 4 | Task 5 | OpenAlex 候选和近 5 年论文可从 fixture/真实 API 返回 | 只实现一个学术 provider |
| Day 5 | Task 6 | 15–20 个消歧案例可评测 | precision 不足则全部人工确认 |
| Day 6 | Task 7 | declared/observed 画像与 UNKNOWN 正确 | 单篇论文不产生“转向” |
| Day 7 | Task 8 | 强/弱/缺口/机会信号分列 | 不添加总百分比 |
| Day 8 | Task 9–10 | 幻觉 claim 被拦截，邮件逐句有来源 | 验证失败就删句，不让模型自圆其说 |
| Day 9 | Task 11 | Streamlit 端到端 fixture 流程、导出和删除 | 不做账号/云部署 |
| Day 10 | Task 12 | 全测、金标准评测、README、5 人试用包 | 任何发布闸门失败则延期 |

### 预留 Day 11–14 时才考虑

只在 G1–G6 已通过后，从以下三项选一项：

1. Semantic Scholar 作为摘要增强 provider；
2. DBLP 作为 CS 作者 profile 交叉核验；
3. 扫描 PDF 的显式 OCR fallback。

不在缓冲期加入姓名搜索、批量导师、自动发信、云登录或向量数据库。

---

## 6. 实施方式

### 5.1 每个任务的工作循环

1. 只读取当前任务和它依赖的接口。
2. 先写失败测试并记录失败原因。
3. 写满足测试的最小实现。
4. 运行当前任务测试，再运行受影响的既有测试。
5. 做一次需求审查：是否引入未授权功能、无来源字段或隐私泄漏。
6. 独立提交；提交信息使用计划中的文本。
7. 在任务看板记录：完成、测试命令、结果、已知限制。

### 5.2 人工评审点

- Task 3 后：用户确认 student profile 表单是否可理解；
- Task 6 后：人工检查 15–20 个消歧案例，决定是否允许任何自动确认；
- Task 8 后：检查“值得联系”是否与用户直觉一致，重点看理由而非排序；
- Task 10 后：逐句检查 20 封 fixture 邮件，寻找模板腔和无来源陈述；
- Task 12 后：由 5 名真实用户试用，不记录其真实 CV。

### 5.3 成本控制

- 页面和 API 结果缓存 7 天；相同 DOI/author ID 不重复请求；
- 只有学生与作者身份确认后才调用 LLM；
- 主题总结和邮件各最多一次正常调用、一次显式重试；
- 单次运行设 token/费用上限；达到上限输出已有结构化报告，不继续生成邮件；
- 评测默认使用 fake LLM，只有小型人工回归集调用真实模型。

### 5.4 推荐的首批测试样本

- 5 位姓名唯一、官方页信息完整的导师；
- 5 位中文常见重名导师；
- 3 位近年换学校/学院的导师；
- 3 位官网方向明显陈旧但近年论文变化的导师；
- 2 位近 5 年论文过少的导师；
- 2 位明确发布招生信息的导师；
- 2 位只有无日期招生表述的导师；
- 2 份文字 PDF CV、1 份双栏 CV、1 份扫描 PDF、1 份信息极少的 CV。

样本可以重叠，总导师数先达到 20，再扩展到发布闸门要求的 30–50。

---

## 7. MVP 完成定义

只有同时满足以下条件，v0.1 才算完成：

- 用户能在本地完成一次真实但不发送邮件的端到端流程；
- 作者身份无法确认时系统会停止，而不是继续猜；
- 导师画像区分官网声明和论文观察；
- 招生与团队信息允许并清晰显示 `UNKNOWN`；
- 匹配结果没有伪精确百分比；
- 邮件中的导师事实和学生事实分别达到 100% provenance coverage；
- 原始 CV 不出现在导出、日志或 Git；
- 删除功能经测试能清除该运行的数据库记录和临时文件；
- 全套自动测试、ruff 和金标准评测通过；
- README 能让另一名学生在 15 分钟内安装并跑通匿名示例。
