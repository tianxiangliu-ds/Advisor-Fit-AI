# 导师双选 AI 工具 v0.1

证据驱动的单导师理解与匹配助手。用户上传一份 CV，人工确认学生事实，再手动录入已经核实的导师资料与论文摘要；系统生成可追溯的导师画像、匹配分析和中文联系邮件草稿。

> 原始自动抓取 MVP 保存在 Git 标签 `v0`。当前工作区为 v0.1 人工证据输入版。

## 为什么改为人工证据输入

中国高校导师主页结构差异大，中文姓名在学术数据库中的消歧也不稳定。v0.1 先解决最核心的问题：不依赖官网抓取、OpenAlex 或 LLM API，也能让一个真实案例完整跑通。

## v0.1 流程

1. 上传 PDF 简历，在本机提取文字。
2. 编辑、增加、删除并确认可用于匹配的学生事实。
3. 填写导师姓名、学校和可选的官方主页、公开研究方向。
4. 手动录入 1–10 篇已核实论文的标题、摘要、链接、年份和关键词。
5. 确认导师身份与每篇论文归属。
6. 生成导师画像、可解释匹配、证据来源和邮件草稿。
7. 下载 Markdown/JSON，或一键删除本次数据库记录与上传 PDF。

## 产品边界

**做**：本地 CV 解析、学生事实人工审核、手动导师资料、手动论文证据、可选万方检索候选（中文/英文/学位论文）、分维度匹配、无 LLM 模板邮件、可选 LLM 润色、Markdown/JSON 导出、完整删除。

**暂不做**：官网自动解析、OpenAlex/Google Scholar/知网自动抓取、Word/Excel 导入、论文 PDF 解析、扫描件 OCR、批量导师、自动发送或跟进。

## 安装与启动

项目使用 Python 虚拟环境。已有 `.venv` 时，可以直接运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

如果需要重新安装依赖，可先安装 `uv`，然后运行：

```powershell
uv sync
uv run streamlit run app.py
```

默认地址通常为 `http://localhost:8501`。

LLM 配置是可选的，支持多供应商：复制 `.env.example` 为 `.env`，设置 `LLM_PROVIDER`（`deepseek` 默认 / `openai` / `anthropic` / `ollama`）与 `LLM_API_KEY`。配置后，简历结构化抽取与邮件/声明生成走 LLM；未配置时自动降级为「规则抽取 + 事实锁定模板」，核心流程仍可完整运行。`LLM_BASE_URL`、`LLM_MODEL` 留空则使用所选供应商的默认地址与模型。

简历解析：默认用 `pypdf` 提取文本；若额外安装了 `docling`（可选增强），会自动用它把 PDF 转成 Markdown，对复杂版式/表格/中文简历效果更好，失败时仍回退到 `pypdf`。

## 可选在线检索（万方）

在 `.env` 配置 `WANFANG_APP_KEY`（万方数据开放平台申请）后，「③ 导师资料与已核实论文」会出现「🔎 一键研究（Agent）」。检索结果只是候选，必须由用户逐条勾选确认归属后才会进入报告。

数据源可选，实测均可用：

| 数据源 | collection | 说明 |
|---|---|---|
| 万方 · 中文（期刊 + 会议） | `OpenPeriodical`、`OpenConference` | 默认，用中文名检索；建议同时填学校，按 `Creator:X AND OrganizationForSearch:Y` 减少同名歧义 |
| 万方 · 英文（英文期刊） | `OpenPeriodicalEng` | 只收录英文文献，**必须用导师英文名**；机构字段常为空，学校过滤基本无效 |
| 万方 · 学位论文 | `OpenThesis` | 收录的是学位论文作者（学生），**不提供「导师」字段**，按导师姓名检索会返回同名学生的论文，慎用 |

结果按 `PublishYear` 倒序。万方未开放的 collection（`OpenNstr`、`OpenStandard`、`OpenCstad`、`OpenClaw` 等）当前返回 0 条，未接入。

检索走自研轻量 Harness：LLM 通过 `run_loop` 决策调用哪个工具，无 LLM 时降级为直接检索。检索失败只提示错误，不影响手动录入流程。

## 第一次测试建议

准备一份可提取文字的测试 CV，以及一位导师的以下资料：

- 姓名和学校；
- 一条可选的官方主页链接；
- 至少一篇确认属于该导师的论文；
- 论文标题、摘要和 `http/https` 来源链接；
- 可选的关键词，例如 `知识图谱；数字人文；文化遗产`。

只勾选真实且愿意用于报告或邮件的学生事实。论文和导师信息也必须由用户人工核实。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts app.py
.\.venv\Scripts\python.exe scripts\run_evals.py
```

## 数据与隐私

- 原始 CV 仅写入本机 `uploads/`，不进入 Git 或导出报告。
- 结构化数据存入本机 `data/app.db`。
- “删除本次数据”会删除数据库记录和对应上传 PDF。
- API Key 只从 `.env` 或环境变量读取。
- 报告不会自动发送；邮件必须由用户人工确认。

## 证据语义

系统严格区分 `FACT / INFERRED / UNKNOWN`。`UNKNOWN` 表示没有足够证据，不代表肯定或否定。例如，未提供招生声明时只显示招生机会未知，不会推断导师招生或不招生。

主题趋势只有在同一主题至少有 3 篇已确认论文、并跨至少 2 个年份时才会输出；单篇论文不构成研究方向转变。

## Git 版本

```powershell
git show v0       # 查看原始 MVP 基线
git diff v0..HEAD # 查看从 v0 到当前版本的变化（提交 v0.1 后）
```

> v0.1 工作区已移除自动抓取子系统（官网抓取、OpenAlex、作者消歧及其测试与依赖）。如需参照旧实现，请回看 Git 标签 `v0`。

完整立项与早期技术分析保留在 `outputs/`。其中部分内容描述原始自动抓取方案，应以本 README 的 v0.1 边界为准。
