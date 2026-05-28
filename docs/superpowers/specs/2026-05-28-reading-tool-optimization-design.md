# Knowledge Tree 阅读工具优化设计

> **目标**：优化文档解析和AI分析质量，使 Knowledge Tree 成为教材/资料学习的实用级工具。

**范围**：核心能力优化（结构识别 + AI分析）+ 新功能（AI自动标注重点文本）

---

## 1. 结构识别增强

### 1.1 现状

- `tree_builder.py` 用正则匹配标题层级，`hierarchy_repair.py` 做编号规范化
- 纯语法匹配，无语义理解
- `### 第一章` 仍被识别为 level 1（正则优先于 hash 数量）
- 非标准标题（无编号、混合格式）容易错级

### 1.2 设计

在 `build_tree` 之后增加 **AI辅助结构验证** 步骤：

1. **规则优先**：现有正则匹配保持不变
2. **异常检测**：扫描树结构，识别以下异常模式：
   - 层级跳跃 > 1（如 level 1 → level 3）
   - 连续 5+ 个同级标题（可能是子节点被误判为同级）
   - 叶节点占比异常低（标题过多，可能是误识别）
3. **AI修复**：将异常标题列表（含上下文）批量发给 DeepSeek（复用现有 API key 和模型），请求判断正确层级
4. **保守策略**：只修复明确错误，不改变合理结构；LLM 调用失败时保留原始结果

### 1.3 改动文件

- `tree_builder.py`：新增 `validate_and_repair_hierarchy(nodes, api_key)` 函数
- `server.py`：在 `build_tree` 后调用验证函数

---

## 2. AI分析增强

### 2.1 现状

- `node_enhancer.py` 对 level 1-4 节点调用 DeepSeek API
- 输出：summary（100-150字）、keywords（3-6个字符串）、exam_points、mermaid、tables
- 问题：总结太泛、关键词不精准、无原文重点标记

### 2.2 优化总结

- 要求总结抓住**核心论点**和**关键细节**，避免"本章介绍了..."这类空话
- 动态长度：短内容（<500字）→ 50-80字，中等内容 → 100-150字，长内容（>1000字）→ 150-200字
- 必须包含具体概念名称

### 2.3 优化关键词

- 关键词必须是**文中的核心术语**，非泛泛学科词汇
- 输出格式改为对象数组，附带上下文解释：

```json
[
  {"term": "维果茨基", "context": "提出最近发展区理论，强调教学应走在发展前面"},
  {"term": "最近发展区", "context": "儿童独立解决问题与在指导下解决问题之间的差距"}
]
```

- 兼容处理：UI层同时支持旧格式（字符串数组）和新格式（对象数组）

### 2.4 新增 highlights 字段

AI从原文中提取 3-8 个关键片段：

```json
{
  "highlights": [
    {
      "text": "维果茨基认为，教学应当走在发展的前面，教学的最佳期是由最近发展区决定的",
      "importance": "high",
      "type": "theory"
    },
    {
      "text": "支架式教学的核心是教师提供适当的支持，随着学生能力提升逐渐撤除",
      "importance": "medium",
      "type": "method"
    }
  ]
}
```

- `text`：原文片段，20-60字
- `importance`：`high`（必须掌握）/ `medium`（重要）
- `type`：`definition`（定义）/ `theory`（理论）/ `argument`（论点）/ `example`（举例）/ `formula`（公式）/ `method`（方法）

### 2.5 改动文件

- `node_enhancer.py`：修改 prompt 模板，增加 highlights 输出要求，优化 summary/keywords prompt
- `common.js`：新增 `renderHighlights()` 函数，修改 `formatContent()` 支持高亮标记
- UI 文件：信息面板渲染逻辑更新

---

## 3. UI — 重点内容展示

### 3.1 重点标注区域（新增）

在信息面板的 summary 和 content 之间增加"重点标注"区块：

- 卡片列表形式，每张卡片包含：
  - 左侧彩色边框：`high` = `var(--accent)`，`medium` = `var(--accent-secondary)`
  - 类型标签（pill 样式）
  - 原文片段文本
- 点击卡片滚动到 content 中对应的高亮位置

### 3.2 内容区域高亮

在 content 文本渲染时，将 highlights 的原文片段用高亮背景标记：

- 暗色模式：`background: rgba(232,168,76,0.15)`
- 亮色模式：`background: rgba(192,122,46,0.1)`
- 高亮文本加 `font-weight: 500` 增强可读性
- 使用 `mark` 标签或 `span.highlight` 实现
- 文本匹配：精确匹配优先，失败时使用模糊匹配（忽略空白差异）

### 3.3 关键词展示优化

- 新格式关键词（对象数组）：hover 时显示 context 解释（tooltip）
- 旧格式关键词（字符串数组）：保持原有标签样式
- tooltip 样式复用 theme.css 的 tooltip 变量

### 3.4 改动文件

- `common.js`：新增 `renderHighlights(highlights)`、修改 `formatContent(text, keywords, maxLen, highlights)` 支持高亮
- `upload_mindmap.html`：信息面板增加 highlights 区域
- `tree_mindmap.html`：同上
- `tree_display.html`：同上
- `theme.css`：新增 `.highlight-card`、`.highlight-mark`、`.keyword-tooltip` 样式

---

## 4. 数据兼容性

### 4.1 旧数据兼容

- `keywords` 字段：UI层同时支持字符串数组和对象数组
- `highlights` 字段：如果节点没有此字段，不显示重点标注区域
- `summary` 字段：格式不变，仅内容质量提升

### 4.2 存储格式

- 新增字段直接写入 `tree_parts/Part_X_tree.json` 和 `tree_parts_enhanced/`
- 与现有字段共存，不破坏已有结构

---

## 5. 不在范围内

- 文档格式支持扩展（当前 PDF/Word/TXT 已足够）
- 学习/复习功能（如闪卡、测试）
- 笔记/批注功能（用户选择不做）
- 多模型支持（当前使用 DeepSeek）
- 前端框架迁移（保持 vanilla HTML/CSS/JS）
