"""LLM 结构化 CV 抽取测试。"""

from advisor_fit.ingest.cv_llm import (
    LlmStudentFact,
    StudentProfileOutput,
    build_student_profile_llm,
)


class FakeCVLLM:
    def __init__(self, facts):
        self._facts = facts

    def generate(self, *, schema, instructions, payload):
        return StudentProfileOutput(facts=self._facts)


def test_llm_cv_extracts_candidate_facts():
    llm = FakeCVLLM(
        [
            LlmStudentFact(field="skill", value="Python"),
            LlmStudentFact(field="degree", value="硕士"),
        ]
    )
    profile = build_student_profile_llm("简历：Python 硕士", llm)
    assert [(f.field, f.value) for f in profile.facts] == [
        ("skill", "Python"),
        ("degree", "硕士"),
    ]
    assert all(not f.user_confirmed for f in profile.facts)
    assert profile.draftable_facts() == []


def test_llm_cv_empty_text_skips_llm():
    called: list[bool] = []

    class Spy(FakeCVLLM):
        def generate(self, **kwargs):
            called.append(True)
            return StudentProfileOutput(facts=[])

    profile = build_student_profile_llm("   ", Spy([]))
    assert profile.facts == []
    assert called == []


def test_llm_cv_drops_invalid_fields_and_duplicates():
    llm = FakeCVLLM(
        [
            LlmStudentFact(field="skill", value="Python"),
            LlmStudentFact(field="skill", value="Python"),  # 重复
            LlmStudentFact(field="hobby", value="篮球"),  # 非法字段
            LlmStudentFact(field="skill", value=""),  # 空值
        ]
    )
    profile = build_student_profile_llm("x", llm)
    assert [(f.field, f.value) for f in profile.facts] == [("skill", "Python")]


def test_llm_cv_extracts_name():
    class NameLLM:
        def generate(self, *, schema, instructions, payload):
            return StudentProfileOutput(name="张三", facts=[])

    profile = build_student_profile_llm("张三\nPython 硕士", NameLLM())
    assert profile.name == "张三"
