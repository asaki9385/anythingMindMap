"""单独测试 AI 层级校验层，不需要跑完整 pipeline。"""
import asyncio
import os
from parser.ai_hierarchy_validator import ai_validate_with_source

# 模拟原文 Markdown
source_md = """
# 第一章 心理发展与教育

## 第一节 心理发展概述

心理发展是指个体从出生到死亡的心理变化过程。

## 第二节 认知发展理论

### 皮亚杰的认知发展阶段论

皮亚杰将认知发展分为四个阶段。

维果茨基提出了最近发展区的概念，强调社会文化对认知发展的影响。

最近发展区是指儿童独立解决问题的实际发展水平与在成人指导下解决问题的潜在发展水平之间的差距。
"""

# 模拟规则引擎输出（含明显错误）
test_nodes = [
    {"title": "第一章 心理发展与教育", "level": 1, "content": "", "children": [], "numbering_type": "chapter_cn"},
    {"title": "第一节 心理发展概述",   "level": 2, "content": "", "children": [], "numbering_type": "section_cn"},
    {"title": "心理发展是指...",       "level": 3, "content": "", "children": [], "numbering_type": None},
    {"title": "第二节 认知发展理论",   "level": 2, "content": "", "children": [], "numbering_type": "section_cn"},
    {"title": "皮亚杰的认知发展阶段论","level": 3, "content": "", "children": [], "numbering_type": None},
    {"title": "维果茨基",             "level": 1, "content": "", "children": [], "numbering_type": None},  # <- 错误：应为 level 3
    {"title": "最近发展区",           "level": 5, "content": "", "children": [], "numbering_type": None},  # <- 错误：跳层
]

api_key = os.environ.get("OPENAI_API_KEY", "")

print("=== 修复前 ===")
for n in test_nodes:
    print(f"  level={n['level']}  {n['title']}")

result = asyncio.run(ai_validate_with_source(test_nodes, source_md, api_key))

print("\n=== 修复后 ===")
for n in result:
    print(f"  level={n['level']}  {n['title']}")

# 期望：
# 维果茨基 level 1 -> 3
# 最近发展区 level 5 -> 4
