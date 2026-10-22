"""主题归属审校（placement_audit）的离线测试。

覆盖：
  · 解析层不再丢框标题（丹丹攻略 等），框内条目正确挂在框下；
  · 跨主题条目（颜之推内容挂在韩愈下）被移回自己的主题，子树完整跟随；
  · 安全门：习题容器不拆、结构节点不动、多实体/跨章节不自动移动。

运行：python3 knowledge-compiler/test_placement_audit.py
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from placement_audit import fix_topic_placement, extract_entity
from tree_builder import parse_md_to_nodes, adjust_standalone_levels, build_tree, is_noise
from hierarchy_repair import apply_hierarchy_repair

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" | {detail}" if detail else ""))


def node(title, content="", children=None):
    return {"title": title, "content": content, "children": children or []}


def mk_tree():
    """复现真实案例：知识点二 韩愈 章节末尾的「丹丹攻略」框里放着颜之推的条目。"""
    return [
        node("第三节 隋唐时期与魏晋南北朝的教育思想", children=[
            node("知识点一 颜之推的教育思想", children=[
                node("（一）士大夫教育思想", "……"),
                node("（二）家庭教育思想", "……"),
            ]),
            node("知识点二 韩愈的教育思想", children=[
                node("（三）论尊师重道（教师观）", "韩愈的教师观……"),
                node("丹丹攻略", "关于颜之推家庭教育思想和韩愈的师道观……", children=[
                    node("1. 颜之推家庭教育思想的启示", "家庭教育的重要性……", children=[
                        node("（3）之前和当今的启示:", "传统文化的传承……"),
                    ]),
                    node("2. 韩愈师道观的启示", "尊师重教……"),
                ]),
            ]),
        ]),
    ]


def get_child(parent, title):
    for c in parent.get("children", []):
        if c.get("title") == title:
            return c
    return None


def test_noise_keeps_boxes():
    print("[1] 解析层：框标题不再当噪音丢弃")
    for t in ["丹丹攻略", "丹丹导学", "考情分析", "本章自测表", "精华提要", "知识导图"]:
        check(f"「{t}」保留为节点", is_noise(t) is False)
    check("页脚水印仍过滤（公众号）", is_noise("考研公众号") is True)

    md = (
        "## 知识点二 韩愈的教育思想\n\n"
        "## （三）论尊师重道（教师观）\n\n韩愈认为……\n\n"
        "## 丹丹攻略\n\n关于颜之推和韩愈思想的启示，考生需灵活把握。\n\n"
        "## 1. 颜之推家庭教育思想的启示\n\n家庭教育很重要。\n"
    )
    nodes = parse_md_to_nodes(md)
    titles = [n["title"] for n in nodes]
    check("「丹丹攻略」成为节点", "丹丹攻略" in titles, str(titles))
    box = next((n for n in nodes if n["title"] == "丹丹攻略"), None)
    check("框导语段落保留在框节点内", box is not None and "灵活把握" in box["content"], box and box["content"][:30])

    nodes = adjust_standalone_levels(nodes)
    nodes = apply_hierarchy_repair(nodes)
    roots = build_tree(nodes)
    # 框内条目应挂在框下，而不是逃到「（三）」下
    def find(n, t):
        if n.get("title") == t:
            return n
        for c in n.get("children", []):
            r = find(c, t)
            if r:
                return r
        return None
    sec = None
    box2 = None
    for r in roots:
        sec = sec or find(r, "（三）论尊师重道（教师观）")
        box2 = box2 or find(r, "丹丹攻略")
    item_under_box = box2 is not None and find(box2, "1. 颜之推家庭教育思想的启示") is not None
    item_leaked = sec is not None and find(sec, "1. 颜之推家庭教育思想的启示") is not None
    check("框内条目挂在「丹丹攻略」下（不再续排进上一节）", item_under_box and not item_leaked)


def test_move_to_owner():
    print("[2] 归属审校：跨主题条目移回自己的主题（子树跟随）")
    roots = mk_tree()
    rep = fix_topic_placement(roots, log=lambda *a: None)
    yanzhitui = roots[0]["children"][0]  # 知识点一
    moved = get_child(yanzhitui, "1. 颜之推家庭教育思想的启示")
    check("颜之推条目移回「知识点一 颜之推的教育思想」", moved is not None)
    check("移动明细被记录", len(rep["moved"]) == 1 and rep["moved"][0]["entity"] == "颜之推", str(rep["moved"]))
    check("子树（（3）之前和当今的启示）随节点一起移动",
          moved is not None and get_child(moved, "（3）之前和当今的启示:") is not None)
    han_yu = roots[0]["children"][1]
    box = get_child(han_yu, "丹丹攻略")
    check("韩愈自己的条目留在原位（不误移）",
          box is not None and get_child(box, "2. 韩愈师道观的启示") is not None)
    check("移动后原框下不再残留同名节点",
          box is not None and get_child(box, "1. 颜之推家庭教育思想的启示") is None)


def test_guards():
    print("[3] 安全门：习题容器 / 结构节点 / 多实体 / 跨章节 不动")
    # 3a：父节点是习题容器 → 不移动
    roots = [
        node("第一节 汉代的教育", children=[
            node("知识点一 董仲舒的教育思想", children=[node("（一）……", "x")]),
            node("知识点二 王充的教育思想", children=[
                node("二、请将下列人物名片补充完整。", "请将下列人物……", children=[
                    node("（一）董仲舒", "董仲舒名片……"),
                ]),
            ]),
        ]),
    ]
    rep = fix_topic_placement(roots, log=lambda *a: None)
    parent = roots[0]["children"][1]
    exercise = get_child(parent, "二、请将下列人物名片补充完整。")
    check("习题容器下的条目不被拆走",
          len(rep["moved"]) == 0 and exercise is not None and get_child(exercise, "（一）董仲舒") is not None,
          str(rep["skipped"]))

    # 3b：结构节点（知识点X）不做移动方
    roots = [
        node("第一节 古罗马的教育", children=[
            node("知识点一 共和时期的教育", children=[node("（一）xxx", "x")]),
            node("知识点二 帝国时期的罗马教育", children=[node("（一）yyy", "y")]),
        ]),
    ]
    rep = fix_topic_placement(roots, log=lambda *a: None)
    check("结构节点（知识点/章节标题）不作为移动方", len(rep["moved"]) == 0, str(rep["moved"]))

    # 3c：标题含多个注册实体 → 不动
    roots = mk_tree() + [node("比较专题 颜之推与韩愈思想对比", "对比……", children=[
        node("（一）异同", "……"),
    ])]
    roots[0]["children"][0]["children"].append(node("颜之推与韩愈思想对比", "对比……"))
    rep = fix_topic_placement(roots, log=lambda *a: None)
    moved_titles = [m["title"] for m in rep["moved"]]
    check("多实体标题不自动移动", all("对比" not in t for t in moved_titles), str(moved_titles))

    # 3d：跨章节 → 不动（只记录）
    roots = [
        node("第二节 隋唐教育思想", children=[
            node("知识点一 韩愈的教育思想", children=[node("（一）xxx", "x")]),
        ]),
        node("第三节 宋明教育思想", children=[
            node("知识点一 颜之推的古文思想散论", "颜之推……"),
        ]),
    ]
    rep = fix_topic_placement(roots, log=lambda *a: None)
    check("跨章节疑似项只记录不移动", len(rep["moved"]) == 0, str(rep["moved"]) + str(rep["skipped"]))


def test_entity_extraction():
    print("[4] 实体抽取：名称感判断")
    cases = {
        "知识点一 颜之推的教育思想": "颜之推",
        "第二节 韩愈的教育思想": "韩愈",
        "知识点二 自然主义的教育理论": "自然主义",
        "帝国时期的罗马教育": None,         # 无「实体+后缀」结构
        "科举影响学校教育的表现": "科举影响学校" if False else None,  # 通用词黑名单
        "培养大儒的教育目的": None,
    }
    for title, expect in cases.items():
        got = extract_entity(title)
        check(f"「{title[:18]}」 → {expect}", got == expect, f"got={got}")


if __name__ == "__main__":
    test_noise_keeps_boxes()
    test_move_to_owner()
    test_guards()
    test_entity_extraction()
    print(f"\n{'=' * 44}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
