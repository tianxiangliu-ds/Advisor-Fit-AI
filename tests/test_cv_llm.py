"""LLM 结构化 CV 抽取测试。"""

from advisor_fit.ingest.cv_llm import (
    LlmEducation,
    LlmProject,
    LlmPublication,
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


def test_llm_cv_extracts_structured_experience():
    class RichLLM:
        def generate(self, *, schema, instructions, payload):
            return StudentProfileOutput(
                name="张三",
                education=[LlmEducation(degree="硕士", institution="武汉大学", major="信息管理")],
                projects=[LlmProject(name="政策文本抽取", description="基于大模型")],
                publications=[LlmPublication(title="某论文", venue="某期刊", year="2025")],
            )

    profile = build_student_profile_llm("张三\n武汉大学硕士", RichLLM())
    assert len(profile.education) == 1
    assert profile.education[0].institution == "武汉大学"
    assert profile.projects[0].name == "政策文本抽取"
    assert profile.publications[0].title == "某论文"
    assert ("project", "政策文本抽取：基于大模型") in {
        (fact.field, fact.value) for fact in profile.facts
    }
    assert ("publication", "某论文（某期刊，2025）") in {
        (fact.field, fact.value) for fact in profile.facts
    }
