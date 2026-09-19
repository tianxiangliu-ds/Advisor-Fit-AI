"""CV 本地解析、PII 脱敏与学生事实候选。

规则：
- 使用 pypdf 逐页提取文本；无文本时返回阻断性 warning（NO_EXTRACTABLE_TEXT）；
- 邮箱/手机号用正则精确脱敏；地址用中文行政区划关键词做尽力脱敏；
- 姓名优先由 UI 传入的可选姓名做精确替换；未传入时用姓氏启发式做尽力脱敏；
- build_student_profile 只产出 user_confirmed=False 的候选事实，确认前不得进入草稿。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pypdf import PdfReader

from advisor_fit.models.common import FactStatus
from advisor_fit.models.student import StudentFact, StudentProfile


class ParsedDocument(BaseModel):
    text: str = ""
    page_count: int = 0
    warnings: list[str] = []


class RedactedText(BaseModel):
    text: str
    redactions: set[str] = set()


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ADDRESS_RE = re.compile(
    r"[一-龥]{2,}(?:省|自治区|特别行政区|市|县|区|旗|路|街|道|镇|乡|村|号|楼|大厦|单元|室)"
)

# 常见中文姓氏（用于无显式姓名时的尽力脱敏）
_SURNAMES = "".join(
    """赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻
柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕
郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴
谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田
樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑
裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰
秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖
卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀浦
尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄
阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后
荆红游竺权逯盖益桓公""".split()
)
_NAME_RE = re.compile(r"(?<![一-龥])[" + _SURNAMES + r"][一-龥]{1,2}")
_NAME_LINE_RE = re.compile(r"^[一-龥·]{2,4}$")


def extract_name_rule(text: str) -> str | None:
    """无 LLM 时的姓名启发式：取前几行中形如「2–4 个汉字」的首个候选。"""
    for line in text.splitlines()[:8]:
        line = line.strip()
        if _NAME_LINE_RE.match(line) and "·" not in line:
            return line
    return None


def extract_pdf_text(path: Path | str) -> ParsedDocument:
    path = Path(path)
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - 读取失败必须降级为可读警告
        return ParsedDocument(text="", page_count=0, warnings=[f"PDF_READ_FAILED: {exc}"])

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - 单页失败不阻断其余页
            parts.append("")
    text = "\n".join(parts).strip()

    warnings: list[str] = []
    if not text:
        warnings.append("NO_EXTRACTABLE_TEXT")
    return ParsedDocument(text=text, page_count=len(reader.pages), warnings=warnings)


def extract_pdf_markdown(path: Path | str) -> str:
    """用 docling 将 PDF 转为 Markdown；docling 不可用时降级为 pypdf 纯文本。"""
    path = Path(path)
    try:
        from docling.document_converter import DocumentConverter
    except Exception:  # noqa: BLE001 - docling 为可选增强，缺失时走 pypdf
        return extract_pdf_text(path).text
    try:
        result = DocumentConverter().convert(str(path))
        markdown = result.document.export_to_markdown()
        if markdown and markdown.strip():
            return markdown.strip()
    except Exception:  # noqa: BLE001 - docling 解析失败时降级
        pass
    return extract_pdf_text(path).text


def redact_pii(text: str, name: str | None = None) -> RedactedText:
    redactions: set[str] = set()
    out = text

    out, n = _EMAIL_RE.subn("[邮箱已脱敏]", out)
    if n:
        redactions.add("email")

    out, n = _PHONE_RE.subn("[电话已脱敏]", out)
    if n:
        redactions.add("phone")

    if name:
        if name in out:
            out = out.replace(name, "[姓名已脱敏]")
            redactions.add("name")
    else:
        out, n = _NAME_RE.subn("[姓名已脱敏]", out)
        if n:
            redactions.add("name")

    out, n = _ADDRESS_RE.subn("[地址已脱敏]", out)
    if n:
        redactions.add("address")

    return RedactedText(text=out, redactions=redactions)


_SKILL_TOKENS = [
    "Python",
    "PyTorch",
    "TensorFlow",
    "Keras",
    "scikit-learn",
    "NumPy",
    "Pandas",
    "Java",
    "C++",
    "Rust",
    "Go",
    "JavaScript",
    "TypeScript",
    "SQL",
    "Spark",
    "Hadoop",
    "Docker",
    "Kubernetes",
    "Linux",
    "Git",
    "MySQL",
    "Redis",
    "LangChain",
    "RAG",
    "检索增强生成",
    "机器学习",
    "深度学习",
    "自然语言处理",
    "计算机视觉",
    "信息检索",
    "BM25",
]


def build_student_profile(document: ParsedDocument) -> StudentProfile:
    """从 CV 文本抽取候选事实；全部 user_confirmed=False，确认前不可用于草稿。"""
    text = document.text
    facts: list[StudentFact] = []
    i = 0

    for token in _SKILL_TOKENS:
        if token.casefold() in text.casefold():
            facts.append(
                StudentFact(
                    id=f"fact_{i}",
                    field="skill",
                    value=token,
                    status=FactStatus.FACT,
                    source_id="cv",
                    user_confirmed=False,
                )
            )
            i += 1

    for degree in ("博士", "硕士", "本科", "学士"):
        if degree in text:
            facts.append(
                StudentFact(
                    id=f"fact_{i}",
                    field="degree",
                    value=degree,
                    status=FactStatus.FACT,
                    source_id="cv",
                    user_confirmed=False,
                )
            )
            i += 1

    for institution in re.findall(r"[一-龥A-Za-z]{1,}(?:大学|学院|研究所)", text)[:3]:
        facts.append(
            StudentFact(
                id=f"fact_{i}",
                field="institution",
                value=institution,
                status=FactStatus.FACT,
                source_id="cv",
                user_confirmed=False,
            )
        )
        i += 1

    for line in text.splitlines():
        if any(key in line for key in ("兴趣", "研究方向", "希望")):
            value = line.strip().strip("：: ").strip()
            if value:
                facts.append(
                    StudentFact(
                        id=f"fact_{i}",
                        field="interest",
                        value=value,
                        status=FactStatus.FACT,
                        source_id="cv",
                        user_confirmed=False,
                    )
                )
                i += 1

    return StudentProfile(student_id="student_1", name=extract_name_rule(text), facts=facts)


def apply_fact_edits(
    profile: StudentProfile, rows: list[dict[str, Any]]
) -> StudentProfile:
    """把 UI 编辑后的行转换为新的事实清单；空行等同于删除。"""
    facts: list[StudentFact] = []
    for index, row in enumerate(rows):
        field = str(row.get("field") or "").strip()
        value = str(row.get("value") or "").strip()
        if not field or not value:
            continue
        facts.append(
            StudentFact(
                id=f"fact_edit_{index}",
                field=field,
                value=value,
                status=FactStatus.FACT,
                source_id="cv_or_user_edit",
                user_confirmed=bool(row.get("confirmed", False)),
            )
        )
    return profile.model_copy(update={"facts": facts})


def delete_uploaded_cv(upload_dir: Path | str, run_id: str) -> bool:
    """仅删除与精确 UUID run_id 对应的上传 PDF。"""
    try:
        uuid.UUID(run_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("run_id must be a valid UUID") from exc

    path = Path(upload_dir) / f"{run_id}.pdf"
    if not path.exists():
        return False
    path.unlink()
    return True
