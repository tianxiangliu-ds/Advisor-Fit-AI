"""字段级来源记录：导师信息不再"要么全有、要么失败"，而是每个字段各自记录来历。

三态沿用项目红线：
- CONFIRMED：拿到了明确值，且能说清来自哪里；
- INFERRED：拿到了值，但只是推断（例如邮箱域名不像学校域名），需要用户核对；
- UNKNOWN：没拿到，界面显示"未知"，**不阻塞后续流程**。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class FieldStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class FieldProvenance(BaseModel):
    """一个导师字段的值 + 状态 + 来源。"""

    field: str
    label: str = ""
    value: str = ""
    status: FieldStatus = FieldStatus.UNKNOWN
    source: str = ""
    source_url: str | None = None
    note: str = ""

    @property
    def known(self) -> bool:
        return self.status != FieldStatus.UNKNOWN and bool(self.value.strip())


class FieldReport(BaseModel):
    """一次"补齐导师信息"的结果：逐字段的状态 + 缺什么 + 一句中文小结。"""

    fields: dict[str, FieldProvenance] = {}
    source_url: str | None = None
    notes: list[str] = []

    def get(self, field: str) -> FieldProvenance:
        return self.fields.get(
            field, FieldProvenance(field=field, label=field, status=FieldStatus.UNKNOWN)
        )

    def value_of(self, field: str) -> str:
        entry = self.fields.get(field)
        return entry.value if entry is not None else ""

    def known_fields(self) -> list[str]:
        return [name for name, entry in self.fields.items() if entry.known]

    def unknown_fields(self) -> list[str]:
        return [name for name, entry in self.fields.items() if not entry.known]

    def missing_required(self, required: tuple[str, ...]) -> list[str]:
        return [name for name in required if not self.get(name).known]

    def summary(self) -> str:
        total = len(self.fields)
        known = len(self.known_fields())
        line = f"已获取 {known}/{total} 个字段"
        unknown = self.unknown_fields()
        if unknown:
            labels = "、".join(self.get(name).label or name for name in unknown)
            line += f"；还缺：{labels}"
        return line
