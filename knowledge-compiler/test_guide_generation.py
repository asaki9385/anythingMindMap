"""
P3 导学生成 —— 离线测试

覆盖：
1. parse_guide_response：合法 JSON / 带代码围栏和前言 / 垃圾输入
2. _collect_guide_context：确定性采样
3. generate_guides：网络不可用时全链路降级（静态兜底导学、prerequisites 为空、不抛异常）
4. 前置依赖确定性清洗：越后章节引用、自引用、不存在章节、重复项全部剔除
   运行：python knowledge-compiler/test_guide_generation.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from node_enhancer import (
    parse_guide_response, _collect_guide_context, generate_guides,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def test_parse_guide_response():
    print("[1] parse_guide_response")
    ok = parse_guide_response(json.dumps({
        "guide": "本章讲教育的本质。\n重点在概念辨析。",
        "prerequisites": ["第一章 教育概述", "第二章 教育与社会"],
    }, ensure_ascii=False))
    check("合法 JSON 解析", ok.get("guide", "").startswith("本章讲"), repr(ok))
    check("prerequisites 保留", ok.get("prerequisites") == ["第一章 教育概述", "第二章 教育与社会"], repr(ok))

    fenced = parse_guide_response(
        '```json\n{"guide": "带围栏的导学内容", "prerequisites": []}\n```')
    check("代码围栏剥离", fenced.get("guide") == "带围栏的导学内容", repr(fenced))

    preamble = parse_guide_response(
        '好的，以下是结果：\n{"guide": "前言容忍", "prerequisites": ["x"]}')
    check("前言文本容忍", preamble.get("guide") == "前言容忍", repr(preamble))

    check("垃圾输入返回空", parse_guide_response("完全不是 JSON") == {})
    check("空输入返回空", parse_guide_response("") == {})
    check("缺 guide 返回空", parse_guide_response('{"prerequisites": []}') == {})
    check("prerequisites 非数组容错",
          parse_guide_response('{"guide": "g", "prerequisites": "x"}').get("prerequisites") == [])


def test_collect_context():
    print("[2] _collect_guide_context")
    module = {
        "title": "第一章", "children": [
            {"title": "第一节 教育概述", "summary": "教育的定义与要素。", "children": [
                {"title": "知识点一", "children": []},
            ]},
            {"title": "第二节 教育功能", "children": []},
        ]
    }
    text = _collect_guide_context(module)
    check("包含子节点标题", "第一节 教育概述" in text and "知识点一" in text, text[:80])
    check("包含摘要片段", "教育的定义与要素" in text, text[:120])
    check("空模块不报错", _collect_guide_context({"title": "x"}) == "")


def _mk_tree():
    """两模块树，预置非法 prerequisites 用来验证清洗。"""
    ch1 = {"title": "第一章 教育概述", "level": 1, "children": [
        {"title": "第一节 教育的概念", "level": 2, "summary": "教育的定义。", "children": []},
    ]}
    ch2 = {"title": "第二章 教育与社会", "level": 1,
           # 全部非法：越后引用 / 自引用 / 不存在 / 重复
           "prerequisites": ["第三章 教育与人的发展", "第二章 教育与社会",
                             "不存在的章节", "第一章 教育概述", "第一章 教育概述"],
           "children": [
               {"title": "第一节 社会制约教育", "level": 2, "children": []},
           ]}
    ch3 = {"title": "第三章 教育与人的发展", "level": 1, "children": [
        {"title": "第一节 人的发展规律", "level": 2, "children": []},
    ]}
    return {"title": "333教育综合", "level": 0, "children": [ch1, ch2, ch3]}


def test_generate_guides_offline():
    print("[3] generate_guides 离线降级（网络不可达 → 静态兜底）")
    tree = _mk_tree()
    # base_url 指向必然拒绝连接的端口，触发快速失败路径
    out = asyncio.run(generate_guides(
        tree, "fake-key", concurrency=3,
        base_url="http://127.0.0.1:9/chat/completions",
    ))
    check("返回原树（不抛异常）", out is tree)
    for ch in tree["children"]:
        guide = (ch.get("guide") or "").strip()
        check(f"[{ch['title']}] 有静态兜底导学", bool(guide), repr(guide))
        check(f"[{ch['title']}] 标记 _guide_fallback", ch.get("_guide_fallback") is True)
        check(f"[{ch['title']}] prerequisites 为数组", isinstance(ch.get("prerequisites"), list))
    check("根导学缺失但字段安全", "guide" not in tree or isinstance(tree.get("guide"), str))


def test_prereq_cleaning():
    print("[4] 前置依赖确定性清洗")
    tree = _mk_tree()
    asyncio.run(generate_guides(
        tree, "fake-key", concurrency=3,
        base_url="http://127.0.0.1:9/chat/completions",
    ))
    ch2 = tree["children"][1]
    check("越后章节引用被剔除（第三章）", "第三章 教育与人的发展" not in ch2["prerequisites"], ch2["prerequisites"])
    check("自引用被剔除", "第二章 教育与社会" not in ch2["prerequisites"], ch2["prerequisites"])
    check("不存在章节被剔除", "不存在的章节" not in ch2["prerequisites"], ch2["prerequisites"])
    check("合法前置保留且去重", ch2["prerequisites"] == ["第一章 教育概述"], ch2["prerequisites"])


def test_checkpoint_resume():
    print("[5] 断点续跑（enhance_book_nodes + _EnhanceCheckpointer）")
    import tempfile
    from node_enhancer import (
        enhance_book_nodes, tree_fingerprint, collect_postorder,
        _EnhanceCheckpointer,
    )

    tree = _mk_tree()
    fp = tree_fingerprint(tree)
    check("树指纹稳定且长度 12", fp == tree_fingerprint(_mk_tree()) and len(fp) == 12, fp)
    check("结构变化则指纹不同",
          fp != tree_fingerprint({"children": [{"title": "别的章", "children": []}]}))

    nodes = []
    for ch in tree["children"]:
        collect_postorder(ch, nodes)
    check("待增强节点为 3 个一级章", len(nodes) == 3, str([n["title"] for n in nodes]))

    with tempfile.TemporaryDirectory() as td:
        ppath = os.path.join(td, "progress.json")
        # 预置第一条成功记录（模拟上次运行已完成该节点）
        target = nodes[0]
        key0 = f"{target.get('level', 0)}:{target.get('title', '')[:80]}#0"
        ck = _EnhanceCheckpointer(ppath)
        target["summary"] = "预置成功摘要"
        ck.save(target, key0)

        # 全新树 + 不可达网络：应恢复节点 0、其余走兜底
        tree2 = _mk_tree()
        asyncio.run(enhance_book_nodes(
            tree2, "fake-key",
            base_url="http://127.0.0.1:9/chat/completions",
            progress_path=ppath, concurrency=3, request_delay=0,
        ))
        # 注意：collect_postorder 会跳过已有 summary 的节点，恢复后的节点
        # 不在其中，因此这里按树上位置直接取节点做断言
        c1, c2, c3 = tree2["children"]

        check("已成功节点被恢复（未重跑）",
              c1.get("summary") == "预置成功摘要", repr(c1.get("summary")))
        check("已成功节点未标记 fallback", not c1.get("fallback"))
        check("其余节点走兜底", bool(c2.get("fallback")) and bool(c3.get("fallback")),
              repr([c2.get("fallback"), c3.get("fallback")]))
        check("成功记录 is_done = True", _EnhanceCheckpointer(ppath).is_done(key0))

        # fallback 记录不应被视作完成（重跑需要重试）
        key1 = f"{c2.get('level', 0)}:{c2.get('title', '')[:80]}#1"
        ck2 = _EnhanceCheckpointer(ppath)
        ck2.done[key1] = {"summary": "x", "fallback": True}
        ck2.save(c2, key1)
        check("fallback 记录重跑会重试", not _EnhanceCheckpointer(ppath).is_done(key1))


if __name__ == "__main__":
    test_parse_guide_response()
    test_collect_context()
    test_generate_guides_offline()
    test_prereq_cleaning()
    test_checkpoint_resume()
    print(f"\n{'=' * 40}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
