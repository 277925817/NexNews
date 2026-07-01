# 03_ui_spec.md

## 0. UI Data Contract

UI 只消费展示层需要的数据，不直接依赖数据库表结构。新闻列表和榜单消费 `NewsListItem`，详情页消费 `NewsDetailItem`；二者都以不含正文的 `NewsItem` 作为基础数据对象。

```ts
type NewsStatus = "ready" | "translated" | "translation_failed";

type NewsItem = {
  id: string;
  title: string;
  original_title: string;
  source_name: string;
  original_url: string;
  published_at: string;
  score: number;
  status: NewsStatus;
};

type NewsListItem = NewsItem & {
  summary_zh?: string;
};

type NewsDetailItem = NewsItem & {
  summary_zh?: string;
  content_zh?: string;
};
```

字段语义：

- `id`：新闻唯一标识，用于进入详情页。
- `title`：中文标题，仅在 `translated` 状态下渲染。
- `original_title`：原文标题，详情页必须保留展示。
- `summary_zh`：中文摘要，只在 `translated` 状态下用于 `NewsListItem` 卡片和 `NewsDetailItem` 详情页。
- `content_zh`：中文正文，只允许出现在 `NewsDetailItem`，且只在 `translated` 状态下用于详情页。
- `source_name`：RSS 来源名称。
- `original_url`：新闻原文链接，用于详情页的原文链接入口。
- `discussion_url`：来源站内讨论链接，是后端内部字段；当前 UI 不消费、不渲染。
- `published_at`：RSS 信息源发布时间。
- `score`：LLM 新闻价值评分，范围为 `0-100`。
- `status`：UI 可展示状态，只允许 `ready`、`translated`、`translation_failed`。

状态规则：

- `ready`：新闻可展示但翻译未完成；UI 只能展示原文标题、来源、发布时间、评分和 `翻译中`，不得展示英文摘要或英文正文。
- `translated`：翻译完成；UI 展示中文标题、中文摘要、中文正文、来源、发布时间和评分。
- `translation_failed`：翻译失败；UI 只能展示原文标题、来源、发布时间、评分、`翻译失败` 和原文链接，不得展示英文摘要或英文正文。

组件字段依赖：

| Component | Consumed fields |
| --- | --- |
| NewsCard | `NewsListItem`: `id`, `title`, `original_title`, `summary_zh`, `source_name`, `published_at`, `score`, `status` |
| HighScoreList | `NewsListItem`: `id`, `title`, `original_title`, `source_name`, `score`, `status` |
| ArticleView | `NewsDetailItem`: `id`, `title`, `original_title`, `summary_zh`, `content_zh`, `source_name`, `original_url`, `published_at`, `score`, `status` |
| StatusBadge | `status` |
| ScoreBadge | `score` |
| SourceMarker | `source_name` |

### 0.1 字段渲染绑定规则

每个字段只能在指定状态和指定组件中渲染。未在下表允许的字段不得渲染。

| UI field | `ready` | `translated` | `translation_failed` |
| --- | --- | --- | --- |
| `title` 展示位 | 渲染 `original_title`，显示为原文标题 | 渲染 `title`，显示为中文标题 | 渲染 `original_title`，显示为原文标题 |
| `summary_zh` | 不渲染 | 仅 NewsCard / ArticleView 可显示 | 不渲染 |
| `content_zh` | 不渲染 | 仅 ArticleView 可显示 | 不渲染 |
| `score` | 可显示，只读 | 可显示，只读 | 可显示，只读 |
| `source_name` | 可显示 | 可显示 | 可显示 |
| `published_at` | 可显示 | 可显示 | 可显示 |

字段渲染禁止项：

- `ready` 和 `translation_failed` 状态不得渲染 `summary_zh`。
- `ready` 和 `translation_failed` 状态不得渲染 `content_zh`。
- NewsCard 不得在任何状态下渲染 `content_zh`。
- `score` 只读展示，不得触发排序、筛选或跳转。
- Home News Feed 和 Top 30 Days 是用户主要阅读入口，只允许渲染 `translated` 新闻条目；`ready` / `translation_failed` 只作为 ArticleView 异常态、测试 fixture 或后续专门状态区处理。

### 0.2 UI 禁止容错逻辑

UI 不得对数据字段执行自动容错、自动 fallback 或自动猜测。

禁止：

- 自动补默认值。
- 自动隐藏字段所在 UI 区域。
- 自动降级渲染。
- 自动猜测字段含义。
- 使用其他字段替代当前字段展示。

字段缺失规则：

- 字段不存在 = 不渲染该字段。
- 字段为空 = 不渲染该字段。
- 不得用其他字段替代展示缺失字段。
- 不得为了保持布局完整而生成占位文案。

### 0.3 UI 风格规则

- 需求变更：整体界面必须使用浅灰底色，不再使用深色背景作为主界面底色。
- 页面根节点、`body` 和应用框架背景必须使用 `Background` token。
- 卡片、榜单整体容器、榜单行、表单、输入框、按钮和状态容器必须使用 `Surface` 或 `Surface subtle` token，并通过 `Border` token 分隔层级。
- 禁止把 `#0B0F14`、`#111820`、`#151E28` 等深色背景作为页面、卡片、表单或主要内容区域的大面积背景色。
- 浅灰主题不得改变信息架构、组件清单、交互白名单或字段渲染规则。

## 1. Product UI Goal

AI 新闻聚合系统的 UI 目标是最大化信息筛选与阅读效率。

核心信息流：

RSS → LLM评分 → 过滤 → 翻译 → 展示

UI 必须让用户快速完成三件事：

1. 扫描哪些 AI 新闻值得看。
2. 判断某条新闻是否值得点开。
3. 进入中文全文阅读，不被未翻译英文内容干扰。

## 2. Design Principles

### 2.1 Functional Minimalism

所有 UI 组件必须服务核心任务。无法帮助用户筛选、阅读、刷新或管理 RSS 信息源的组件不进入 MVP。

### 2.2 High Information Density

页面以新闻列表为主体，信息密度高但不拥挤。使用紧凑间距、清晰标题、短摘要、来源、时间、评分和状态标记帮助用户快速比较新闻价值。

### 2.3 Low Cognitive Load

不使用复杂导航。用户只需要理解三个位置：

- 主页面：看新闻、刷新、进入配置。
- 新闻浏览页：读中文全文。
- RSS 配置页：管理信息源。

### 2.4 Reading-first Hierarchy

标题优先级最高，摘要次之，来源、时间、评分和状态作为辅助判断信息。详情页以中文正文阅读体验为第一优先级。

### 2.5 No Decorative UI

禁止天气、无意义统计卡、复杂图表、装饰面板、无操作价值的小组件。留白优先于堆叠组件。

### 2.6 UI 不可新增原则

UI 默认不可新增。除非本文件明确列出，否则任何“体验优化”都不得实现。

禁止：

- 增加提示。
- 增加辅助信息。
- 增加交互便利性。
- 增加视觉装饰。

## 3. Information Architecture

### 3.1 Pages

1. 主页
   - 展示产品标题NexNews
   - 展示新闻卡片列表。
   - 展示 30 天高分榜单。
   - 提供刷新入口。
   - 提供 RSS 信源配置入口。

2. 新闻阅读页
   - 展示已翻译新闻的中文标题、中文摘要、中文正文和必要元信息。
   - 翻译中或翻译失败时，不展示英文摘要或英文正文。
   - 提供原文链接入口。

3. RSS 信源配置页面
   - 展示未删除 RSS 信息源列表。
   - 支持新增信息源。
   - 支持启用 / 停用信息源。
   - 支持删除信息源。

### 3.2 Navigation

只保留顶部栏导航：

- NexNews标题: 返回主页面。
- 刷新: 手动抓取 RSS。
- 信源: 进入 RSS 信源配置页。

不设计侧边导航、二级菜单或多层设置页。

## 4. Visual Direction

### 4.1 Style

整体风格为 Tech / AI / Data + Minimal / Modern，接近 Notion、Linear、Arc、Vercel Dashboard 的克制型工具界面。

需求变更后的视觉基调为浅灰工作台：页面背景使用低饱和浅灰，内容容器使用白色或近白，边框与文字层级承担信息组织，不依赖深色底或大面积高饱和色块。

### 4.2 Color Tokens

只使用少量颜色变量：

- Background: `#F3F4F6`
- Surface: `#FFFFFF`
- Surface subtle: `#F8FAFC`
- Border: `#D8DEE6`
- Text primary: `#18202A`
- Text secondary: `#64717F`
- Accent: `#2563EB`
- Success / translated: `#047857`
- Warning / translating: `#B45309`
- Error / failed: `#DC2626`

来源卡片颜色由系统自动生成，但只能作为左侧细色条或小圆点，不作为大面积装饰。

背景约束：

- `:root`、`body` 和 `.app-shell` 必须使用 `#F3F4F6`。
- 卡片、榜单整体容器、榜单行、表单行、输入框、按钮、加载骨架和错误/空状态容器必须使用 `#FFFFFF` 或 `#F8FAFC`。
- 主文字必须使用 `#18202A`，辅助文字必须使用 `#64717F`。
- 页面不得设置 `color-scheme: dark`。

### 4.3 Typography

- 页面标题：`Inter`, `system-ui`, `sans-serif`
- 正文阅读：`Inter`, `system-ui`, `sans-serif`
- 分数、时间、状态：`ui-monospace`, `SFMono-Regular`, `Menlo`, `monospace`

字体层级：

- Page title: 22px / 700
- Card title: 16px / 650
- Article title: 28px / 700
- Body text: 16px / 1.75
- Meta text: 12px / 500

### 4.4 Shape and Spacing

- Card radius: 8px
- Button radius: 6px
- Input radius: 6px
- Page max width: 1180px
- Main gap: 20px
- Card padding: 16px
- Compact item gap: 8px

### 4.5 Interaction Visual Rules

- Hover: 只允许轻微改变边框色和背景色，不使用阴影动画、抬升动画或缩放动画。
- NewsCard height: 最小高度固定为 96px，内容不足时仍保持该高度。
- List density: NewsCard 之间的垂直间距为 10px 到 12px，列表不得使用大卡片式留白。
- Skeleton: 加载骨架必须与 NewsCard 的高度、圆角、内边距和信息块位置一致。
- Animation: 除加载 spinner 外，不使用额外动画。
- Transition: hover 过渡时间不超过 120ms，只作用于 `border-color` 和 `background-color`。

## 5. Page Specifications

### 5.0 Interaction Contract

This table is the final UI interaction allowlist for the MVP.

UI 只允许以下点击行为，未列出的 UI 元素默认不可点击。

| UI element | Click behavior |
| --- | --- |
| NexNews title | 可点击，返回 Home News Page |
| Refresh button | 可点击，调用 `POST /api/refresh`，刷新中禁用 |
| Sources button | 可点击，进入 RSS 信息源配置页 |
| NewsCard | 可点击，进入对应新闻的 ArticleView |
| Title | 主要点击目标，进入对应新闻的 ArticleView |
| ScoreBadge | 不可点击 |
| SourceMarker | 不可点击 |
| HighScoreList item | 可点击，进入对应新闻的 ArticleView |
| Article original link button | 可点击，打开 `original_url` |
| Article back button | 可点击，返回 Home News Page |
| SourceForm submit | 可点击，调用 `POST /api/sources`，提交中禁用 |
| SourceRow enable / disable | 可点击，调用 `PATCH /api/sources/{id}`，提交中禁用 |
| SourceRow delete | 可点击，调用 `DELETE /api/sources/{id}`，删除中禁用 |

规则：

- NexNews title、Refresh button、Sources button 是 TopBar 仅有的可点击导航/命令。
- NewsCard 点击和 Title 点击没有不同，二者都进入同一条新闻的 ArticleView。
- ScoreBadge 只展示评分，不触发排序、筛选或跳转。
- 当前 UI 不显示 Hacker News 讨论入口；不得把内部 `discussion_url` 当作原文链接或卡片跳转目标。
- SourceMarker 只展示来源，不跳转来源站点。
- HighScoreList item 与 NewsCard 使用同一个新闻 `id` 跳转。
- Article original link button 只在 ArticleView 中出现，不得替代站内新闻详情路由。
- SourceRow 操作不得暴露 processing log、task progress、retry 或 admin 控件。
- 点击 NewsCard、Title 或 HighScoreList item 进入 ArticleView 后不得出现无解释的空阅读页。
- `translated` 详情页必须显示中文摘要和中文正文；`ready` / `translation_failed` 详情页必须显示 `摘要和正文暂不可用` 以及对应原因说明。
- `ready` / `translation_failed` 的 NewsCard 和 HighScoreList 链接 accessible name 必须包含 `摘要和正文暂不可用`，让用户在进入详情前知道当前不可阅读原因。

其他所有交互行为 = 禁止

### 5.1 Home News Page

Purpose: 让用户用最少时间找到值得阅读的 AI 新闻。

web layout:

```
┌──────────────────────────────────────────────┐
│ Top Bar: AI News     Refresh      Sources    │
├───────────────────────────────┬──────────────┤
│ News Feed                     │ Top 30 Days  │
│ ┌───────────────────────────┐ │ ┌──────────┐ │
│ │ News Card                 │ │ │ Rank Item│ │
│ └───────────────────────────┘ │ └──────────┘ │
│ ┌───────────────────────────┐ │ ┌──────────┐ │
│ │ News Card                 │ │ │ Rank Item│ │
│ └───────────────────────────┘ │ └──────────┘ │
└───────────────────────────────┴──────────────┘
```


#### 5.1.1 Layout Constraint Rule

- Desktop Home layout is fixed as two columns: left `News Feed`, right `HighScoreList`.
- News Feed must remain the left primary column.
- HighScoreList must remain the fixed right column in the Home layout grid.
- Desktop Home layout must not collapse into a single-column layout.
- HighScoreList must not be implemented as a tab, modal, drawer, dropdown, or floating sidebar.
- HighScoreList is not scroll-synced with News Feed.
- HighScoreList does not have its own scroll container in MVP; the Home page uses one page-level scroll.
- HighScoreList loads with Home page data and updates only after Refresh or Home reload.
- HighScoreList does not introduce a separate user-facing refresh action.
- UI must not require HighScoreList to have an independent API contract; it consumes the same `NewsListItem` list DTO defined in `0. UI Data Contract`.
- Top 30 Days / HighScoreList must render as one overall card in the fixed right column.
- The overall card must contain the `Top 30 Days` heading and all ranked rows inside one `Surface` with `Border`, 8px radius and 16px padding.
- Ranked items inside the card must render as compact list rows separated by dividers, not as independent cards nested inside the overall card.
- Ranked item hover may use `Surface subtle`, but the row must not introduce its own 1px card border or separate card radius.

### 5.2 新闻卡片

目的：帮助用户在列表中判断是否值得点开并进入中文全文阅读。

Home News Feed 的 NewsCard 只渲染 `translated` 新闻：

- `translated`
  - 中文标题
  - 中文摘要
  - 来源
  - 信息源发布时间
  - 评分
  - 原文标题仅作为辅助信息或详情页保留字段，不替代中文标题

卡片结构：

```
┌────────────────────────────────────┐
│ 来源标记  来源 · 发布时间             │
│ 标题                         87    │
│ 摘要或状态，最多 2 行                 │
└────────────────────────────────────┘
```

交互：

- 点击标题进入新闻阅读页。
- 整张卡片可以点击，但标题必须是最清晰的点击目标。
- 悬停状态只轻微改变边框和背景。
- 点击任一普通新闻卡片进入 ArticleView 后，必须看到中文摘要和可阅读中文正文，不得只看到占位短句或 `摘要和正文暂不可用`。

### 5.3 30 天高分榜单

目的：帮助用户直接跳到最近 30 天最高价值新闻。

内容：

- 排名序号
- 新闻标题
- 来源
- 评分

规则：

- 最多显示 10 条。
- 只显示 `translated` 新闻。
- 按评分从高到低排序。
- 标题过长时最多显示 2 行。
- 不显示摘要。
- 不显示图表。
- Top 30 Days 必须以一个整体卡片承载标题和排名列表。
- 排名项必须作为整体卡片内部的列表行渲染，通过分隔线建立层级，不得表现为多个独立卡片。
- 点击任一榜单行进入 ArticleView 后，必须看到中文摘要和可阅读中文正文，不得只看到占位短句或 `摘要和正文暂不可用`。

### 5.4 新闻阅读页

目的：提供低干扰中文全文阅读。

布局：

```
┌──────────────────────────────────────┐
│ 顶部栏 / 返回                        │
├──────────────────────────────────────┤
│ 来源 · 发布时间 · 评分               │
│ 中文标题                             │
│ 原文标题                             │
│ 原文链接                             │
│ 中文摘要                             │
│ 中文正文                             │
│                                      │
└──────────────────────────────────────┘
```

已翻译状态：

- 展示中文标题。
- 展示中文摘要。
- 展示中文正文。
- 展示原文标题。
- 展示来源、发布时间、评分。
- 展示原文链接按钮，按钮 `href` 必须等于 API 返回的 `original_url`。
- 原文链接按钮不得使用内部 `discussion_url`。
- 中文正文必须是可阅读正文，不得使用 fixture/mock/模拟/占位类短句充当全文。
- 中文摘要必须概括同一新闻正文，不得与标题或正文无关。

`ready` 状态：

- 标题区域展示原文标题。
- 展示 `翻译中` 状态。
- 展示明确状态标题：`摘要和正文暂不可用`。
- 展示说明：`翻译完成后将自动显示中文摘要和正文。`
- 不展示英文摘要或英文正文。

翻译失败状态：

- 展示原文标题。
- 展示 `翻译失败` 提示。
- 展示明确状态标题：`摘要和正文暂不可用`。
- 展示说明：`翻译失败，当前无法显示中文摘要和正文。`
- 展示原文链接按钮。
- 不展示英文摘要或英文正文。

404 / 不可用状态：

- 展示明确提示：`新闻不存在或不可展示`
- 展示仅含返回图标的返回按钮；不得显示 `返回新闻列表` 文字，但必须用 `aria-label="返回新闻列表"` 保留可访问名称。

阅读规则：

- 正文内容宽度应为 680px 到 760px。
- 正文行高应保持舒适。
- 元信息应位于标题上方，并使用较小、较弱的视觉样式。
- 原文链接按钮必须打开真实原文 URL；本地验收数据不得把保留域名或占位 URL 作为用户可点击原文链接。
- 原文链接应放在核心阅读内容之后，而不是之前。

### 5.5 RSS 信息源配置页

目的：管理 RSS 输入源，保证新闻进入系统。

布局：

```
┌──────────────────────────────────────┐
│ 顶部栏 / 返回                        │
├──────────────────────────────────────┤
│ 新增信息源                           │
│ [名称输入框] [RSS URL 输入框] [新增] │
│ 信息源列表                           │
│ 信息源名称       URL      状态  删除 │
└──────────────────────────────────────┘
```

内容：

- 信息源名称输入框
- RSS URL 输入框
- 新增按钮
- 未删除信息源列表
- 每个信息源的启用 / 停用操作
- 每个信息源的删除操作

规则：

- 不提供高级设置。
- 不提供信息源分类。
- MVP 提供启用 / 停用控件；禁止停用最后一个启用信息源。
- 删除信息源成功后，必须从列表中视觉移除。
- 已删除信息源不得在配置页重新出现。
- 停用信息源成功后，该行必须显示停用状态。
- 启用信息源成功后，该行必须显示启用状态。
- 非法 URL 错误显示在 URL 输入框下方。

## 6. 组件清单

MVP 只允许以下组件。

| 组件 | 存在理由 | 如果删除 |
| --- | --- | --- |
| AppShell | 为所有页面提供一致框架 | 导航和页面间距会不一致 |
| TopBar | 提供主页、刷新和信息源入口 | 用户无法高效刷新或配置信息源 |
| NewsCard | 支持快速扫描新闻价值 | 用户无法在信息流中比较新闻 |
| ScoreBadge | 让 LLM 价值评分立即可见 | 用户失去最主要的筛选信号 |
| StatusBadge | 传达翻译中或翻译失败状态 | 用户可能把中文内容缺失误认为故障 |
| SourceMarker | 用极低视觉成本区分 RSS 来源 | 用户扫描时失去来源上下文 |
| HighScoreList | 展示最高价值的前 10 条新闻 | 用户必须手动浏览整个信息流寻找重点新闻 |
| ArticleView | 提供专注的中文阅读体验 | 用户无法舒适阅读已翻译全文 |
| SourceForm | 新增 RSS 信息源 | 用户无法扩展输入源 |
| SourceRow | 展示、启停并删除 RSS 信息源 | 用户无法管理已配置输入源 |
| LoadingState | 解释刷新和翻译等待状态 | 用户会看到不确定或像卡住的界面 |
| EmptyState | 在没有数据时引导用户 | 用户会看到空白页面 |
| ErrorState | 解释不可用或非法状态 | 用户无法从错误中恢复 |

除非能用更少 UI 提供同等功能，否则不应新增其他 MVP 组件。

### 6.1 Final Unit Rule

所有组件必须是最终实现单元（Final Unit）。组件清单中的每个组件都应直接实现对应 UI，不再继续拆分为更小的业务子组件或抽象组件。

禁止：

- 组件拆分，例如 `NewsCardHeader`、`NewsCardMeta`、`ArticleHeader`。
- UI 抽象层，例如 `BaseButton`、`BaseCard`、`BaseInput`。
- Design system，例如独立的 tokens / primitives / variants 体系。
- 通用组件库，例如 `components/ui`、`shared/ui`、`common/components`。

允许：

- 在最终组件内部直接使用原生 HTML 元素。
- 使用 CSS 变量承载本文件定义的颜色、间距和字体值。
- 为了可读性在同一个最终组件文件内定义私有 helper 函数，但不得导出为可复用 UI 组件。
- 为满足 `06_dev_rules.md` 的单文件行数限制，允许拆分 CSS、API client、DTO/type、test fixture、route/page 文件和不渲染 UI 的纯函数 helper；这些拆分不得创建新的可复用 UI 组件或设计系统层。

## 7. 交互状态

### 7.1 刷新

- 默认：`刷新`
- 加载中：按钮禁用，文案为 `刷新中`
- 完成：重新加载新闻列表

### 7.2 新闻列表

- 加载中：显示与新闻卡片高度一致的紧凑骨架行
- 空状态：`暂无可展示新闻`
- 错误状态：`新闻加载失败`

### 7.3 新闻详情

- `translated`：渲染中文内容
- `ready`：渲染加载状态、`摘要和正文暂不可用`、等待原因说明并轮询
- `translation_failed`：渲染失败状态、`摘要和正文暂不可用`、失败原因说明和原文链接
- 未找到：渲染 404 状态

### 7.4 RSS 信息源表单

- 字段为空：新增按钮禁用
- URL 非法：显示行内校验文本
- 新增中：新增按钮禁用
- 新增完成：清空输入框并刷新列表

### 7.5 RSS 信息源行

- 启用状态：显示 `启用`，提供 `停用` 操作。
- 停用状态：显示 `停用`，提供 `启用` 操作。
- 停用或删除最后一个未删除且启用的信息源失败：显示 API 返回的结构化错误。
- 删除中：删除按钮禁用。
- 删除完成：从列表中视觉移除该行。
