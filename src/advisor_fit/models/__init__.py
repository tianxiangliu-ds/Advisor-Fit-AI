"""领域模型：FACT/INFERRED/UNKNOWN 三态事实、证据、声明、画像、匹配与草稿。"""

from advisor_fit.models.common import (
    Confidence,
    FactStatus,
    SourceRecord,
    SourceTier,
)
from advisor_fit.models.evidence import Claim, ClaimStatus, Evidence
from advisor_fit.models.match import (
    Draft,
    DraftSentence,
    FitLevel,
    MatchDimension,
    MatchReport,
    Recommendation,
    SentenceType,
)
from advisor_fit.models.professor import (
    AssertedField,
    ExternalIds,
    FactValue,
    ObservedTopic,
    OfficialIdentityAnchor,
    ProfessorProfile,
    RecentPublication,
    Recruiting,
    RecruitingStatus,
)
from advisor_fit.models.student import StudentFact, StudentProfile

__all__ = [
    "AssertedField",
    "Claim",
    "ClaimStatus",
    "Confidence",
    "Draft",
    "DraftSentence",
    "Evidence",
    "ExternalIds",
    "FactStatus",
    "FactValue",
    "FitLevel",
    "MatchDimension",
    "MatchReport",
    "ObservedTopic",
    "OfficialIdentityAnchor",
    "ProfessorProfile",
    "RecentPublication",
    "Recommendation",
    "Recruiting",
    "RecruitingStatus",
    "SentenceType",
    "SourceRecord",
    "SourceTier",
    "StudentFact",
    "StudentProfile",
]
