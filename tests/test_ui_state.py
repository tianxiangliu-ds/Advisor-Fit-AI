"""Navigation must not silently discard user-confirmed evidence."""

from advisor_fit.ui_state import forget_widgets, remember_widgets, restore_widgets


def test_professor_fields_survive_widget_pruning():
    state = {"prof_name": "陈老师", "prof_institution": "某大学"}
    keys = ("prof_name", "prof_institution")
    remember_widgets(state, keys)
    del state["prof_name"]
    del state["prof_institution"]

    restore_widgets(state, keys)

    assert state["prof_name"] == "陈老师"
    assert state["prof_institution"] == "某大学"


def test_new_professor_forgets_saved_identity_but_not_student_name():
    state = {"prof_name": "旧导师", "student_name": "张同学"}
    remember_widgets(state, ("prof_name", "student_name"))

    forget_widgets(state, ("prof_name",))
    del state["prof_name"]
    restore_widgets(state, ("prof_name", "student_name"))

    assert "prof_name" not in state
    assert state["student_name"] == "张同学"


def test_remember_updates_durable_value_after_user_edit():
    state = {"prof_name": "旧导师"}
    remember_widgets(state, ("prof_name",))
    state["prof_name"] = "新导师"
    remember_widgets(state, ("prof_name",))

    assert state["ui_saved_widgets"]["prof_name"] == "新导师"
