# Advisor Fit UI Implementation Plan

> **For agentic workers:** Implement this plan task-by-task with tests first. The approved visual reference is `design-proposal/advisor-fit-concept.html` and its seven screenshots.

**Goal:** Apply the approved ink-sidebar/warm-paper visual system to the working Streamlit app while preserving its evidence confirmation and export workflow.

**Architecture:** Keep the existing Streamlit backend and single entry point. Add a focused visual theme module and page navigation state helper. Render one workflow screen at a time; copy widget values to durable session state before changing screens, since Streamlit prunes widgets that are not rendered.

**Tech Stack:** Python 3.12+, Streamlit, pytest/AppTest, built-in CSS. No new runtime dependency.

**Spec:** `design-proposal/advisor-fit-concept.html` (approved by the user).

## Global constraints

- Never infer identity or paper ownership from the visual treatment; retain human confirmation gates.
- Keep CV parsing local and email draft manual; no automatic send.
- Historic runs and exports remain available. Visual sample data never enters production.
- Preserve unrelated workspace changes and original `UI_design/` references.

---

### Task 1: Navigation and durable form state

**Files:** Create `src/advisor_fit/ui_state.py`; modify `app.py`; test `tests/test_ui_state.py`.

- [x] Write tests proving page changes preserve professor fields and confirmed paper selections, and reset clears the current professor's saved fields.
- [x] Run tests and confirm expected failures from missing interface.
- [x] Implement `remember_widgets(state, keys)` and `restore_widgets(state, keys)` around a separate `ui_saved_widgets` dictionary, plus page routing in `app.py`.
- [x] Re-run focused tests and AppTest navigation tests.

### Task 2: Shared theme and page shell

**Files:** Create `src/advisor_fit/ui_theme.py`; modify `app.py`; test `tests/test_ui_app.py`.

- [x] Write AppTest checks for the seven navigable screens and default landing route.
- [x] Run and observe route-test failures.
- [x] Implement the approved color/type/sidebar/hero/page-heading CSS and Streamlit sidebar navigation; show one active screen at a time.
- [x] Verify routes and desktop/browser rendering.

### Task 3: Evidence workflow pages

**Files:** Modify `app.py`; test `tests/test_ui_app.py`.

- [x] Add tests exercising existing CV/fact, professor, and candidate-confirmation gates after screen switching.
- [x] Implement student review, professor profile, and paper list + right evidence inspector with existing widgets and actions.
- [x] Confirm no unverified paper enters the report; run focused tests.

### Task 4: Brief, draft, archive, export

**Files:** Modify `app.py`; test `tests/test_ui_app.py`.

- [x] Test report/email/history navigation with a saved run; preserve download and full-clear actions.
- [x] Implement research-brief report, editable paper-like email view, and archive comparison using real result fields, not hardcoded sample data.
- [x] Run focused and full tests, lint, and a real browser smoke review across all screens.
