# 择研 · Advisor Fit 设计规范（design.md）

> **这是本项目唯一的视觉规范来源。**
> 任何新页面、新组件、新样式，动手之前**必须先读本文件**，并严格使用这里定义的色值、字号、间距与组件规则。
> 如果确有必要新增色值或尺寸，**先更新本文件，再写代码**——不允许在页面里出现"只用一次"的临时样式。

规范来源：`src/advisor_fit/ui_theme.py`（已实现的 CSS）+ `.streamlit/config.toml`（Streamlit 主题映射）。
视觉基线参考：`design-proposal/advisor-fit-concept.html`（已批准的"墨色 / 暖纸"方案）。

---

## 0. 设计语言总述

**墨色 + 暖纸**：浅色暖纸作为页面底，深墨色承担侧边栏与"聚焦面板"，紫色作为唯一强调色，金色只用于序号等极小面积点缀。

三条底层原则：

1. **层级靠边框和表面色，不靠阴影。** 卡片一律 1px 边框 + 略亮的表面色；全站默认 `box-shadow: none`。
2. **标题用衬线、正文用无衬线。** 衬线只出现在标题与品牌字，绝不用于正文。
3. **克制用色。** 紫色只用于"当前/可交互/需要被看见"的元素；金色只用于序号。其余一律走中性灰阶。

---

## 1. 配色

### 1.1 核心调色板

| 语义 | CSS 变量 | 色值 | 用在哪 |
|---|---|---|---|
| **主色**（强调） | `--purple` | `#7569af` | 页面眉标（`.page-eyebrow`）、Tab 选中、输入框聚焦边框 |
| 主色 · 深（交互态） | — | `#65558f` | 次级按钮 hover 文字 |
| 主色 · 聚焦环 | — | `#7569af22`（13% 透明） | 输入框 focus 的 `box-shadow` |
| 主色 · 浅底 | — | `#eeeaf5` | 流程条"当前步"底色 |
| 主色 · 正文态 | — | `#605483` | 流程条"当前步"文字 |
| **辅助色**（点缀） | `--gold` | `#b48a52` | 序号数字（`.landing-path b`） |
| 辅助色 · 浅 | — | `#d6c390` | 首屏标题里的强调文字（`em`） |
| **墨色**（深色面） | `--ink` | `#1d2028` | 侧边栏底、primary 按钮底 |
| 深色面 · 首屏 | — | `#202129` | `.landing-hero` |
| 深色面 · 导师横幅 | — | `#252630` | `.profile-band` |
| 深色面 · 侧栏说明卡 | — | `#282932` | `.side-note` |
| 深色面 · 交互态 | — | `#34323d` | 侧栏按钮 hover / 当前页 |
| **背景色**（页面） | `--paper` | `#f5f2eb` | `body` / 主区底 |
| **表面色**（卡片） | `--surface` | `#fffcf7` | 卡片、面板底 |
| 表面 · 高亮 | — | `#fffcf8` | 普通按钮、流程条、`.landing-path` |
| 表面 · 柔和 | — | `#fffdf9` | expander / 上传器 / `st.container(border=True)` |
| 表面 · 判定条 | — | `#f5f0e9` | `.brief-verdict` |
| 判定条 · 顶边 | — | `#bc9a68` | `.brief-verdict` 的 `border-top: 2px` |
| 报告脚标 | — | `#9c8c78` | `.folio` 文字 |
| 报告脚标 · 底线 | — | `#ded3c3` | `.folio` 的下边框 |
| **分隔线** | `--line` | `#dedbd3` | 全站边框与分隔 |
| 分隔线 · 深色面 | — | `#35343c` | 侧边栏右边框 |
| 分隔线 · 说明卡 | — | `#45434f` | `.side-note` 边框 |
| 分隔线 · 交互态 | — | `#49434f` | 侧栏按钮 hover 边框 |
| 分隔线 · 按钮 hover | — | `#a999cb` | 次级按钮 hover 边框 |

### 1.2 文字色

| 语义 | 色值 | 用在哪 |
|---|---|---|
| **标题** | `#282a33` | `h1` / `h2` / `h3` |
| **正文（全局）** | `#292b31` | `body` |
| 正文段落 | `#52545b` | `p` / `li` / markdown 段落 |
| 次级说明 | `--muted` `#777975` | `.page-intro` |
| 图注 | `#81817e` | `st.caption` |
| 分区标签 | `#847f78` | `.section-note` |
| 按钮文字 | `#44464d` | 次级按钮 |
| 侧栏正文 | `#dad9df` | 侧栏所有文字 |
| 侧栏次级 | `#abaab4` | 侧栏 markdown 段落 |
| 侧栏按钮 | `#d5d2df` | 侧栏按钮文字 |
| 侧栏标签 | `#8e8c9d` | `.side-label` |
| 侧栏品牌 | `#f3f0ed` | `.studio-brand` |
| 侧栏品牌副标 | `#a9a3b8` | `.studio-brand small` |
| 侧栏品牌符号 | `#c1acdb` | `.brand-mark` |
| 侧栏说明卡正文 | `#c4c0ca` | `.side-note` |
| 侧栏说明卡强调 | `#decaa5` | `.side-note b` |
| 深色面标题 | `#f7f4ee` | `.landing-hero h1` |
| 深色面副标 | `#c9b4e7` | `.landing-hero .label` |
| 深色面正文 | `#c5c1c8` | `.landing-hero p` |
| 深色面点缀 | `#f3e7d5` | `.landing-hero .orb` |
| 横幅标题 | `#faf3ec` | `.profile-band h2` |
| 横幅副标 | `#c7b3e2` | `.profile-band .label` |
| 横幅说明 | `#bdb9c3` | `.profile-band p` |
| 判定条文字 | `#36333b` / `#67635d` | `.brief-verdict strong` / `span` |

### 1.3 状态色

| 语义 | 色值 | 用在哪 |
|---|---|---|
| 已完成 | `#54806f` | 流程条 `.done`；轨迹步骤成功 `.trace-step .ok` |
| 进行中 | `#605483` | 流程条 `.current` |
| 未开始 | `#a2a09b` | 流程条默认；轨迹里被跳过的步骤 `.trace-step .skipped` |
| **出错 / 降级** | `#a8543f` | 轨迹里失败或触发资源上限的步骤 `.trace-step .error`、降级条 `.trace-degraded` 的顶边 |
| 聚焦轮廓 | `#b9a8de` | 侧栏导航 `:focus-visible`（`outline: 2px`，`outline-offset: 1px`） |

> 语义提示（成功/警告/错误）沿用 Streamlit 原生 `st.success / st.warning / st.error`，只统一圆角为 **5px**，不自定义颜色。

### 1.4 插画元素专用色（不得用作 UI 颜色）

以下色值只出现在首屏 / 横幅的**装饰图形**（描边圆、光晕、球体）中，属于插画，**不得**用于按钮、卡片、文字或状态：

| 用途 | 色值 |
|---|---|
| 首屏装饰描边 | `#80768a70` |
| 首屏光晕（双层） | `#ffffff05`、`#c4a8850a` |
| 球体渐变 | `#77718b` → `#353542` |
| 球体描边 / 投影 | `#777083` / `#15151d` |
| 横幅装饰描边 / 光晕 | `#887d92` / `#ffffff06` |

> 移动端（`max-width: 900px`）球体降为 `opacity: .25`，靠右 2%。

### 1.5 Streamlit 主题必须与本文档一致

`.streamlit/config.toml` 是对 CSS 之外的控件（复选框、单选、滑块、下拉）生效的兜底，**不得出现第二套主色**：

```toml
[theme]
primaryColor = "#7569af"            # --purple，与主色一致
backgroundColor = "#f5f2eb"         # --paper
secondaryBackgroundColor = "#fffcf7" # --surface
textColor = "#292b31"               # 正文色
font = "sans serif"
```

---

## 2. 字体

### 2.1 字体族

| 用途 | 字体栈 |
|---|---|
| **标题**（h1/h2/h3、品牌、判定条强调） | `"Noto Serif SC", "Songti SC", SimSun, Georgia, serif` |
| **正文 / 控件** | 系统无衬线（Streamlit 默认 `sans serif`） |
| **英文小标签**（眉标、分区标签、侧栏标签、品牌副标） | `Arial, sans-serif` |
| **数字序号** | `Georgia, serif` |

> 基准：`1rem = 16px`（浏览器/Streamlit 默认）。

### 2.2 标题

| 层级 | 字号 | 字重 | 行高 | 字距 | 备注 |
|---|---|---|---|---|---|
| `h1`（页面主标题） | `2.1rem` ≈ 33.6px | 400 | 1.4 | `.02em` | 用 `st.title()` |
| `h2` | `1.42rem` ≈ 22.7px | 400 | 继承 | — | `margin-top: 1.4rem` |
| `h3` | `1.12rem` ≈ 17.9px | 400 | 继承 | — | |
| 首屏大标题（`.landing-hero h1`） | `clamp(2.6rem, 4vw, 4.4rem)` | 400 | 1.3 | — | 仅首屏，`margin: 25px 0 20px` |
| 导师横幅标题（`.profile-band h2`） | `2rem` ≈ 32px | 400 | 继承 | — | `margin: 10px 0 6px` |
| 品牌字（`.studio-brand`） | `1.45rem` | 600 | 继承 | `.09em` | 衬线 |

> 标题字重固定 **400**（衬线体在浅色纸底上不需要加粗）；全部用 `!important` 覆盖 Streamlit 默认值。

### 2.3 正文与小字

| 用途 | 字号 | 字重 | 行高 | 字距 |
|---|---|---|---|---|
| 正文（`p` / `li`） | 1rem ≈ 16px | 400 | **1.78** | — |
| 页面导语（`.page-intro`） | `.86rem` ≈ 13.8px | 400 | 继承 | — |
| 图注（`st.caption`） | `.78rem` ≈ 12.5px | 400 | 继承 | — |
| 分区标签（`.section-note`） | `.7rem` ≈ 11.2px | 400 | 继承 | `.14em` |
| 页面眉标（`.page-eyebrow`） | `.67rem` ≈ 10.7px | 700 | 继承 | `.2em` |
| 侧栏标签（`.side-label`） | `.61rem` ≈ 9.8px | 400 | 继承 | `.18em` |
| 侧栏品牌副标 | `.55rem` | 500 | 继承 | `.22em` |
| 侧栏说明卡（`.side-note`） | `.73rem` | 400 | 1.7 | — |
| 侧栏按钮 | `.83rem` | 400 | 继承 | — |
| 流程条（`.step-strip span`） | `.79rem` | 400 | 继承 | — |
| 流程条序号（`b`） | `1.03rem` | 400（Georgia） | 继承 | — |
| 判定条强调（`.brief-verdict strong`） | `1rem` | 500（衬线） | 继承 | — |
| 判定条说明（`.brief-verdict span`） | `.81rem` | 400 | 继承 | — |
| 按钮（通用） | 继承 Streamlit | **500** | — | — |

---

## 3. 间距

> 实际值不是严格的 8px 网格，而是"以 4/8 为基准的手工调校值"。**新页面必须复用下表数值，不要自创。**

### 3.1 页面容器

| 项 | 桌面 | 移动端（`max-width: 900px`） |
|---|---|---|
| 最大宽度 | `1440px` | — |
| 内边距 | `3.2rem 3.2rem 6rem`（≈51px / 底 96px） | `2rem 1.2rem 4rem` |

### 3.2 模块间距

| 位置 | 值 |
|---|---|
| 页面眉标 → 标题 | `12px`（`.page-eyebrow` 的 `margin-bottom`） |
| 标题 → 导语 | `-4px 0 1.7rem`（负上边距抵消 Streamlit 默认间距） |
| 流程条 | `margin: 20px 0 30px` |
| 分区标签 | `margin: 18px 0 16px` |
| 首屏 → 旅程条 | `18px` |
| 导师横幅 | `margin: 4px 0 24px` |
| 判定条 | `margin: 12px 0 18px` |
| 侧栏说明卡 | `margin-top: 26px` |
| `h2` 上间距 | `1.4rem` |
| 侧栏分组标签 | `margin: 8px 0 10px` |

### 3.3 卡片内边距

| 组件 | 值 |
|---|---|
| `st.container(border=True)` / expander | Streamlit 默认（不覆盖） |
| 文件上传器 | `18px` |
| 侧栏说明卡 | `14px` |
| 判定条 | `15px 18px` |
| 导师横幅 | `25px 30px` |
| 旅程条（`.landing-path`） | `25px` |
| 首屏 | `70px 7%` |
| 流程条单元 | `15px 18px` |
| Tab | `.8rem 1.2rem` |
| 侧栏按钮 | `.6rem .8rem` |
| 品牌区 | `14px 6px 24px` |

### 3.4 栅格与列间距

- 页面布局统一用 `st.columns()`，两栏主区/侧栏比例用 `[2.5, 1]`（报告页）或 `[2.3, 1]`（邮件页）。
- 表单两栏用 `st.columns(2)`，左右对称。
- 事实编辑器用 `[0.08, 0.16, 0.68, 0.08]`（确认 / 类型 / 内容 / 删除）。
- 选项卡内部列表与详情用 `[1.65, 1]`。
- 列间距一律 `gap="large"`（主布局）或 `gap="small"`（紧凑表单），不手写像素。

---

## 4. 组件样式

### 4.1 圆角与阴影总表

| 组件 | 圆角 | 阴影 | 边框 |
|---|---|---|---|
| 卡片 / expander / 上传器（通用） | `6px` | **none** | `1px solid var(--line)` |
| 深色面板（首屏、导师横幅） | `7px` | none（仅装饰圆有投影） | 无 |
| 侧栏说明卡 | `7px` | none | `1px solid #45434f` |
| 按钮 / 下载按钮 / 提示条 | `5px` | none | `1px solid var(--line)` |
| 输入框 / 文本域 / 下拉 | `4px` | none | `1px solid var(--line)` |
| 侧栏导航按钮 | `6px` | none | `1px solid transparent` |
| Tab | `0` | none | 仅底边 |

> **禁止给卡片加阴影。** 层级只用"表面色 + 边框"表达。首屏装饰圆与 `.orb` 的投影属于插画元素，不是卡片样式。

### 4.2 按钮

**通用（主区）**

| 状态 | 背景 | 文字 | 边框 |
|---|---|---|---|
| 默认 | `#fffcf8` | `#44464d`（字重 500） | `1px solid var(--line)` |
| hover | `#fffcf8` | `#65558f` | `1px solid #a999cb` |
| primary | `var(--ink)` `#1d2028` | `#fff` | `1px solid var(--ink)` |
| primary hover | `#373440` | `#fff` | `1px solid #373440` |

**侧栏导航（特例）**

| 状态 | 背景 | 文字 | 边框 |
|---|---|---|---|
| 默认 | 透明 | `#d5d2df`（`.83rem`，圆角 6px，`padding .6rem .8rem`，全宽、左对齐） | 透明 |
| hover / **当前页** | `#34323d` | `#fff` | `1px solid #49434f` |
| 键盘聚焦 | 不变 | 不变 | `outline: 2px solid #b9a8de; outline-offset: 1px` |

- 侧栏按钮必须**全宽 + 文字左对齐**（内部 `div`/`span` 需 `width:100%`、`justify-content:flex-start`），否则 Streamlit 会居中。
- 当前页高亮由 `app.py` 注入一段动态 `<style>` 实现（`.st-key-nav_<page> button`），不要用 `type="primary"` 代替。

### 4.3 卡片与容器

- 一律 `st.container(border=True)`，得到：`background:#fffdf9` / `border:1px solid var(--line)` / `border-radius:6px` / `box-shadow:none`。
- `st.expander` 与 `st.container(border=True)` 同款。
- 卡片标题用 `st.subheader()`（h3），卡片内分区用 `.section-note`。

### 4.4 导航栏（侧边栏）

| 项 | 值 |
|---|---|
| 背景 | `var(--ink)` `#1d2028` |
| 右边框 | `1px solid #35343c` |
| 最小宽度 | `244px` |
| 按钮间距 | 垂直块 `gap: .16rem` |
| 结构 | 品牌区（`.studio-brand`）→ 分组标签（`.side-label` "WORKSPACE / 工作台"）→ 7 个页面按钮 → `st.divider()` → 分组标签（"CURRENT STUDY / 当前研究"）→ 当前导师 `st.caption` → 说明卡（`.side-note`） |

**页面顺序（固定，不随意增删）**：首屏 / 学生事实 / 导师档案 / 论文核验 / 匹配简报 / 联系邮件 / 研究档案。

### 4.5 Tab

| 状态 | 样式 |
|---|---|
| 容器 | `border-bottom: 1px solid var(--line)`，`gap: 0` |
| 默认 | 文字 `#82807f`，`padding: .8rem 1.2rem`，圆角 0 |
| 选中 | 文字 `var(--purple)` + `border-bottom: 2px solid var(--purple)` |

### 4.6 流程条（`.step-strip`）

- 外层：`display:flex` / `background:#fffcf8` / `1px solid var(--line)` / `margin:20px 0 30px`。
- 单元：`flex:1` / `.79rem` / `padding:15px 18px` / 右侧 `1px` 分隔（最后一项无）。
- 状态：`.current` → 底 `#eeeaf5` + 字 `#605483`；`.done` → 字 `#54806f`；默认字 `#a2a09b`。
- 序号 `b`：`1.03rem` Georgia，`margin-right:8px`。
- 只用于四步主流程页（学生事实 / 导师档案 / 论文核验 / 匹配简报）。

### 4.7 页面头部组合（每页固定三段式）

```python
st.markdown('<div class="page-eyebrow">03 / PAPER EVIDENCE</div>', unsafe_allow_html=True)
st.title("中文主标题，一句话。")
st.markdown('<p class="page-intro">一句中文说明，告诉用户这一步要做什么。</p>',
            unsafe_allow_html=True)
```

- 眉标格式：`两位数字 / 英文大写短语`。
- 主标题：中文、口语化、句号结尾，不超过 20 字。
- 导语：中文、说明"做什么 + 证据规则"，不超过 40 字。

### 4.8 深色面板

| 类 | 用途 | 关键值 |
|---|---|---|
| `.landing-hero` | 首屏 | 底 `#202129`，圆角 7px，`padding:70px 7%`，`min-height:440px`；装饰圆用 `:after` 描边 + 双层 `box-shadow` 光晕 |
| `.orb` | 首屏右侧圆心 | `210px` 圆，`radial-gradient(circle at 30% 25%, #77718b, #353542 68%)`，`font:2rem` 衬线，`letter-spacing:.15em` |
| `.profile-band` | 导师横幅 | 底 `#252630`，圆角 7px，`padding:25px 30px`，右上装饰圆 |
| `.brief-verdict` | 报告判定条 | `border-top:2px solid #bc9a68` + 底 `#f5f0e9` + `padding:15px 18px` + `display:flex; gap:20px` |

### 4.9 输入控件

- 圆角 `4px`（`!important` 覆盖 baseweb）。
- 聚焦：`border-color: var(--purple)` + `box-shadow: 0 0 0 2px #7569af22`。
- 复选框 / 单选 / 滑块沿用 Streamlit 主题的 `primaryColor`，不额外定制。

### 4.10 Agent 运行轨迹（`.trace-*`）

用途：把 Harness 的一次运行摊开给用户看——**Agent 调了哪些工具、每步多久、成功还是失败、有没有触发资源上限**。
这是本项目"Agent 可见"的核心展示区，放在「论文核验」页检索控件下方。

| 元素 | 关键值 |
|---|---|
| `.trace-panel` 容器 | 底 `#fffdf9` / `1px solid var(--line)` / 圆角 `6px` / **无阴影** |
| `.trace-task` 任务行 | `padding:13px 18px` / `border-bottom:1px solid var(--line)` / `.8rem` / 颜色 `#52545b` |
| `.trace-stages` 阶段链 | `padding:11px 18px` / `border-bottom:1px solid var(--line)` / `.72rem` / 颜色 `#847f78`；当前阶段（最后一个）用 `var(--purple)` |
| `.trace-meta` 概览行 | `display:flex` / `gap:22px` / `padding:14px 18px` / `border-bottom:1px solid var(--line)` |
| `.trace-meta span` 概览标签 | `.67rem Arial` / `letter-spacing:.14em` / 颜色 `#847f78` |
| `.trace-meta b` 概览数值 | `1.03rem Georgia` / 字重 400 / 颜色 `#282a33` |
| `.trace-step` 步骤行 | `display:flex` / `gap:14px` / `padding:12px 18px` / `font-size:.8rem` / 底部 `1px solid var(--line)`（最后一行无） |
| `.trace-step i` 序号 | `Georgia .95rem` / `font-style:normal` / 颜色 `var(--gold)` / 宽 `22px` |
| `.trace-step em` 类型标签 | `.61rem Arial` / `letter-spacing:.16em` / `font-style:normal` / 颜色 `#847f78` / 宽 `78px` |
| `.trace-step b` 步骤名 | 字重 500 / 颜色 `#292b31` |
| `.trace-step code` 参数摘要 | `.74rem` / 颜色 `#777975` / 背景透明 |
| `.trace-step span` 耗时 | `margin-left:auto` / `.74rem` / 颜色 `#81817e` |
| `.trace-step .ok` / `.error` / `.skipped` | `#54806f` / `#a8543f` / `#a2a09b` |
| `.trace-degraded` 降级条 | `border-top:2px solid #a8543f` + 底 `#f5f0e9` + `padding:12px 18px` + 颜色 `#67635d` |

约束：

- 轨迹里的**参数与结果只显示摘要**（`digest_args` / `_summarise` 已做截断），不得把整篇论文列表写进界面。
- 概览行最多 5 项：步数 / 工具调用 / 耗时 / token / 外部请求。超出就换行，不缩写数字。
- 任务行显示这次运行的目标（RunTrace.task），让「调了哪些工具」有上下文。
- 一次运行的轨迹区块**只在跑过 Agent 之后出现**；没跑过时不显示空面板。

---

## 5. 新页面检查清单

写任何新页面前，逐条确认：

- [ ] 已读本文件，且**没有引入任何新色值**（需要新增 → 先改本文件）
- [ ] 页面头部使用"眉标 + 中文标题 + 导语"三段式（4.7）
- [ ] 主流程页已插入 `_step_markup` 流程条
- [ ] 内容容器一律 `st.container(border=True)`，未加自定义阴影
- [ ] 按钮只用"默认 / primary"两态，未自创第三态
- [ ] 正文未使用衬线字体；标题字重为 400
- [ ] 所有注入的 HTML 中，用户数据都经 `html.escape()` 转义
- [ ] 术语与文案为中文，且符合"证据 / 确认"的产品红线（不出现"录取概率""保研稳了"等表述）
- [ ] 运行 `ruff check` 通过，并在浏览器里实际看过一遍

---

## 6. 变更流程

1. **先读本文件**，再动手设计。
2. 需要新 token（色值 / 字号 / 间距 / 组件）时：**先在本文件补上定义和用途**，再改 CSS，最后改页面。
3. 本文件与 `src/advisor_fit/ui_theme.py` 必须保持同步；两者冲突时**以本文件为准**，并修正 CSS。
4. 视觉规范变更属于独立提交，提交信息用 `style:` 前缀，例如：
   `style: add evidence-rail card tokens to design system`
