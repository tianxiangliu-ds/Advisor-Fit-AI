# 导师双选 AI 工具 v0.1

证据驱动的单导师理解与匹配助手。用户上传一份 CV，人工确认学生事实，再手动录入已经核实的导师资料与论文摘要；系统生成可追溯的导师画像、匹配分析和中文联系邮件草稿。

> 原始自动抓取 MVP 保存在 Git 标签 `v0`。当前工作区为 v0.1 人工证据输入版。

## 为什么改为人工证据输入

中国高校导师主页结构差异大，中文姓名在学术数据库中的消歧也不稳定。v0.1 先解决最核心的问题：不依赖官网抓取、OpenAlex 或 LLM API，也能让一个真实案例完整跑通。

## v0.1 流程

1. 上传 PDF 简历，在本机提取文字，自动填入姓名并抽取学生事实。
2. 编辑、增加、删除并确认可用于匹配的学生事实（支持全选/全不选）。
3. 填写导师姓名、学校（可粘贴主页链接自动解析），或用「🆕 开始新导师」测试下一位。
4. 「🔎 一键研究」用 Agent 检索候选论文并逐篇消歧，结果分「匹配 / 需审核 / 疑似同名」三栏。
5. 勾选确认论文归属（支持全选/全不选，每条可点「查看原文」）；检索不到时手动补录。
6. 生成导师画像、可解释匹配、证据来源和个性化邮件草稿。
7. 下载 Markdown/JSON/Word；侧栏历史记录按「导师名 · 单位」命名，可回看或对比。

## 产品边界

**做**：本地 CV 解析与姓名自动提取、学生事实人工审核、主页解析、万方检索候选（中文/英文）+ 作者消歧与机构审核、分维度匹配、LLM 个性化邮件、历史记录命名与对比、Markdown/JSON/Word 导出、完整删除。

**暂不做**：官网自动解析（JS 动态页）、OpenAlex/Google Scholar/知网自动抓取、Word/Excel 导入、论文 PDF 解析、扫描件 OCR、批量导师、自动发送或跟进。

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

在 `.env` 配置 `WANFANG_APP_KEY`（万方数据开放平台申请）后，「③ 导师资料与已核实论文」会出现「🔎 一键研究（Agent）」与主页解析。检索结果只是候选，必须由用户逐条勾选确认归属后才会进入报告。

检索走自研轻量 Harness + 导师研究 Agent，包含四步：

1. **检索**：先确定性用「姓名 + 学校」查万方中文库（`OpenPeriodical`、`OpenConference`），若提供了英文名，LLM 可在结果不理想时切到英文库（`OpenPeriodicalEng`）重试。
2. **作者消歧**：LLM 逐篇判断候选论文是否属于该导师本人（依据机构、合作作者、研究主题）。结果分三栏：**匹配**、**机构不符需审核**（可能是导师曾任职单位，需人工确认）、**疑似同名**（已排除，可手动加回）。
3. **确认门控**：当 Agent 发现机构明显不符等歧义时，会暂停并提供结构化选项（确认继续 / 去掉学校重检索 / 按机构保留 / 全部保留手动核对）。
4. **多轮重试**：结果太少时 LLM 会去掉学校重试或切换中/英文库。

候选论文支持「全选匹配项 / 全不选」，每条可点「查看原文」跳转。无 LLM 时降级为「确定性检索 + 机构规则消歧」。检索方式可选「自动（推荐）/ 仅中文 / 仅英文」。

## 主页解析（可选）

在「导师主页链接」粘贴导师个人主页或学校官方页面，点「🔍 解析主页并自动检索」：系统抓取网页、用 LLM 提取姓名/学校/院系/职称/邮箱/研究方向，填入表格并自动触发检索。手动填写的字段作为解析失败时的保障（无 LLM 时仅用规则提取邮箱与页面标题）。

> 注意：部分高校导师页是 JS 动态渲染，静态抓取可能取不到内容；此时请手动填写。

## 导师库建库（批量采集高校导师）

从高校官网批量采集导师档案，写入本地 `data/faculty.db`，用于后续「导师指纹」建库与检索去重。

```powershell
# 安装可选依赖（渲染 JS 页面）
uv sync --group crawl
.\.venv\Scripts\python.exe -m playwright install chromium

# 静态抓取（数据源 seeds/faculty_seed.json）
.\.venv\Scripts\python.exe scripts\crawl_faculty.py

# JS 渲染抓取（高校师资列表多为 Vue/React 动态加载，需 Playwright）
.\.venv\Scripts\python.exe scripts\crawl_faculty.py --js --limit 20
```

每条记录含：姓名、学校、学院、职称、主页链接、邮箱、研究领域、研究方向、代表论文。数据源在 `seeds/faculty_seed.json`（top-10 高校 + 已收录学院）；扩充时先在其学院官网找到「师资队伍/教师名录」入口页 URL 加入即可。

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
# 消歧评测（需 LLM_API_KEY；--rule 可跑机构规则基线对比）
.\.venv\Scripts\python.exe scripts\run_disambiguation_evals.py
```

消歧评测数据在 `evals/disambiguation_golden.jsonl`：每个导师配一组「应命中/应排除」候选论文，量化消歧 precision/recall/F1。用 `scripts/fetch_golden_candidates.py` 抓取真实候选后，按你的领域知识补充标注即可扩充（当前为起始集，约 10 位导师、150 篇候选）。

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
