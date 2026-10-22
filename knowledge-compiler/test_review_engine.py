"""
对话复习引擎测试 —— "模拟学习者"覆盖全部判定路径（离线，不依赖 LLM）。

覆盖：
  单元生成 / scope 过滤 / 首次即达标 / C4 二次独立作答 / 靠提示后复测 /
  S1 提示用尽 / S2 连续卡壳 / S3 轮次超限 / S4 主动放下 / 求助给要点 /
  提示不跳级 / 判分归一化 / 答案泄漏拦截 / 求助轮不计入止损 /
  对话无过程记账词 / 多单元推进 / 持久化 / 崩溃续跑 / 复测范围。
运行：python knowledge-compiler/test_review_engine.py
"""

import asyncio
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import review_engine as re_mod
from review_engine import (
    build_review_units, ReviewSession, ReviewStore,
    detect_leak, _normalise_judged,
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
        print(f"  FAIL {name} | {detail}")


def J(**kw):
    """构造一条判分。"""
    base = {"level": "L2", "matched_points": [], "gaps": [],
            "confusions": [], "trap_resolved": False,
            "asks_for_answer": False, "reply": "再想想，还有哪一块没说到？",
            "next_action": "probe", "hint_level": None}
    base.update(kw)
    return base


def scripted(judgments):
    """脚本化 judge：按调用顺序返回，耗尽后重复最后一条。"""
    calls = []

    async def judge(unit, state, answer, is_restate=False, on_delta=None):
        calls.append({"title": unit["title"], "answer": answer, "is_restate": is_restate,
                      "hint_count": state["hint_count"],
                      "prompt_had_hint": state["prompt_had_hint"]})
        idx = min(len(calls) - 1, len(judgments) - 1)
        raw = judgments[idx]
        if callable(raw):
            raw = raw(unit, state, answer, is_restate)
        return _normalise_judged(raw, len(unit.get("reference_points") or []))

    judge.calls = calls
    return judge


# ── 测试用树 ──

def _tiny_tree():
    """1 个单元：第一章 概述（两个小节）。"""
    return {"title": "书", "children": [
        {"title": "教心", "level": 0, "children": [
            {"title": "第一章 概述", "level": 1, "children": [
                {"title": "第一节 对象", "level": 2, "children": []},
                {"title": "第二节 方法", "level": 2, "children": []},
            ]},
        ]},
    ]}


def _learn_tree():
    """中教史小树：章节 → 两个知识点（一个带摘要/正文，一个只有正文）。"""
    return {"title": "中教史", "children": [
        {"title": "第四章 封建国家教育体制的完善", "children": [
            {"title": "第三节 隋唐时期的选士制度", "children": [
                {"title": "知识点一 颜之推的教育思想", "summary": "颜之推重视家庭教育。",
                 "content": "颜之推主张德艺周厚，家庭教育工作要尽早进行。", "children": []},
                {"title": "知识点二 韩愈的教育思想", "content": "韩愈论尊师重道，提出学无常师。", "children": []},
            ]},
        ]},
    ]}


def _two_unit_tree():
    """2 个单元：第一章 / 第二章。"""
    return {"title": "教心", "children": [
        {"title": "教心", "level": 0, "children": [
            {"title": "第一章", "level": 1, "children": [
                {"title": "第一节 A", "children": []}, {"title": "第二节 B", "children": []}]},
            {"title": "第二章", "level": 1, "children": [
                {"title": "第三节 C", "children": []}, {"title": "第四节 D", "children": []}]},
        ]},
    ]}


def _run(units, judgments, session_id="rs_t", store=None, file_key="file_x"):
    s = ReviewSession(session_id, file_key, units,
                      judge=scripted(judgments), store=store)
    asyncio.run(s.start())
    return s


# ── 1. 单元生成 ──

def test_units():
    print("[1] 复习单元生成")
    tree = _two_unit_tree()
    r = build_review_units(tree)
    units = r["units"]
    check("两个块单元", [u["title"] for u in units] == ["第一章", "第二章"],
          str([u["title"] for u in units]))
    check("应含条目=子标题", units[0]["reference_points"] == ["第一节 A", "第二节 B"],
          str(units[0]["reference_points"]))
    check("组=模块名", units[0]["group"] == "第一章", units[0]["group"])
    check("unit_id 稳定", build_review_units(tree)["units"][0]["unit_id"] == units[0]["unit_id"])

    # 考点叶子单元 + 屏蔽后代
    t2 = {"title": "t", "children": [{"title": "章", "level": 1, "children": [
        {"title": f"节{i}", "children": []} for i in range(9)]}]}
    t2["children"][0]["children"][3]["exam_points"] = [
        {"point": "教育心理学的定义与研究对象", "frequency": "高频", "type": "名词解释"}]
    r2 = build_review_units(t2)
    check("9 子节点的章不是块，只取考点叶子", len(r2["units"]) == 1,
          str([u["title"] for u in r2["units"]]))
    check("考点条目进入应含列表",
          "教育心理学的定义与研究对象" in r2["units"][0]["reference_points"],
          str(r2["units"][0]["reference_points"]))
    check("高频标记", r2["units"][0]["high_freq"] is True)

    # scope
    only2 = build_review_units(_two_unit_tree(), scope={"chapter": "第二章"})["units"]
    check("chapter 过滤", [u["title"] for u in only2] == ["第二章"])
    hf = build_review_units(t2, scope={"high_freq": True})["units"]
    check("high_freq 过滤", len(hf) == 1 and hf[0]["high_freq"])


# ── 2. 首次即达标 → 直接收掉 ──

def test_first_answer_pass():
    print("[2] 首次独立作答即达标 → 直接收掉")
    units = build_review_units(_tiny_tree())["units"]
    with tempfile.TemporaryDirectory() as td:
        store = ReviewStore(td)
        s = _run(units, [J(level="L3", matched_points=[0, 1], gaps=[],
                           reply="两节都说全了。这俩是什么关系？")], store=store)
        opening = s.messages[0]["text"]
        check("开场邀请凭记忆", "第一章 概述" in opening, opening)
        check("开场不泄条目", "第一节" not in opening, opening)
        snap = asyncio.run(s.handle_answer("第一节讲对象，第二节讲方法"))
        check("内部判定通过", snap["units"][0]["status"] == "passed", str(snap["units"][0]))
        check("会话结束", snap["done"] is True)
        check("收尾为自然话语", bool(snap["report"]) and "过关" not in (snap["report"] or ""),
              snap["report"])
        prog = store.load_progress("file_x")
        check("进入待复测队列", len(prog["retest_queue"]) == 1
              and prog["retest_queue"][0]["reason"] == "C1-C4",
              str(prog["retest_queue"]))
        check("最佳层级记录 L3", prog["units"][units[0]["unit_id"]]["best_level"] == "L3")


# ── 3. 渐好型：C4 第二次独立作答达标 ──

def test_progressive_c4():
    print("[3] 渐好型 → 引导后完整重述达标")
    units = build_review_units(_tiny_tree())["units"]
    judge = scripted([
        J(level="L2", matched_points=[0], gaps=["缺第二节"],
          confusions=["把两节混为一谈"], reply="第一节对了。另一节呢？"),
        J(level="L3", matched_points=[0, 1], gaps=[], trap_resolved=True, reply="都齐了。"),
        J(level="L3", matched_points=[0, 1], gaps=[], trap_resolved=True, reply="顺。"),
    ])
    s = ReviewSession("rs_t3", "f", units, judge=judge)
    asyncio.run(s.start())
    asyncio.run(s.handle_answer("第一节是对象"))
    asyncio.run(s.handle_answer("第一节是对象，第二节是方法，二者并列"))
    check("第二轮触发完整重述", "完整重说一遍" in s.messages[-1]["text"], s.messages[-1]["text"])
    snap = asyncio.run(s.handle_answer("第一节……第二节……"))
    check("重述达标 → 通过", snap["units"][0]["status"] == "passed", str(snap["units"][0]))
    check("全程零提示", s.states[0]["hint_count"] == 0)
    check("复述轮 is_restate=True", judge.calls[-1]["is_restate"] is True)
    check("三次独立作答计数", s.states[0]["independent_answers"] == 3,
          str(s.states[0]["independent_answers"]))


# ── 4. 靠提示型 → 半过关（协议 §7） ──

def test_hint_assisted_half():
    print("[4] 靠提示达成 → 待复测")
    units = build_review_units(_tiny_tree())["units"]
    with tempfile.TemporaryDirectory() as td:
        store = ReviewStore(td)
        judge = scripted([
            J(level="L1", gaps=["都没有"], reply="还差得远。再想想？"),
            J(level="L3", matched_points=[0, 1], gaps=[], next_action="restate", reply="这回齐了。"),
            J(level="L3", matched_points=[0, 1], gaps=[], reply="顺。"),
        ])
        s = ReviewSession("rs_t4", "f", units, judge=judge, store=store)
        asyncio.run(s.start())
        asyncio.run(s.handle_answer("对象……方法……大概"))
        check("自动给出 H1 提示", s.states[0]["hint_levels"] == ["H1"]
              and s.messages[-1]["kind"] == "hint", str(s.states[0]["hint_levels"]))
        asyncio.run(s.handle_answer("第一节是对象，第二节是方法"))
        snap = asyncio.run(s.handle_answer("第一节……第二节……"))
        check("用过提示 → 半过关", snap["units"][0]["status"] == "half", str(snap["units"][0]))
        prog = store.load_progress("f")
        check("待复测原因为 hint_assisted",
              prog["retest_queue"][0]["reason"] == "hint_assisted",
              str(prog["retest_queue"]))
        check("提示计数为 1", s.states[0]["hint_count"] == 1)


# ── 5. 碎片型 → S1（提示用尽） ──

def test_s1_hints_exhausted():
    print("[5] 提示用尽 → S1 止损")
    units = build_review_units(_tiny_tree())["units"]
    judge = scripted([
        J(level="L1", reply="再想想？"),                            # → H1
        J(level="L3", matched_points=[0], gaps=["缺"], reply="继续"),  # 不卡 → 无提示
        J(level="L1", reply="再想想？"),                            # → H2
        J(level="L1", reply="再想想？"),                            # 连续两轮 L1 且提示用尽 → S1
    ])
    s = ReviewSession("rs_t5", "f", units, judge=judge)
    asyncio.run(s.start())
    for txt in ["a", "b", "c", "d"]:
        snap = asyncio.run(s.handle_answer(txt))
    check("S1 止损", snap["units"][0]["status"] == "failed", str(snap["units"][0]))
    check("原因为 S1_hints", s.states[0]["reason"] == "S1_hints", s.states[0]["reason"])
    check("恰好两次提示且不跳级", s.states[0]["hint_levels"] == ["H1", "H2"],
          str(s.states[0]["hint_levels"]))
    check("卡层记录 L1", s.states[0]["stuck_level"] == "L1")


# ── 6. 卡死型 → S2 ──

def test_s2_stuck():
    print("[6] 连续真实回忆卡壳 → S2 止损")
    units = build_review_units(_tiny_tree())["units"]
    judge = scripted([J(level="L0", reply="再想想？")])
    s = ReviewSession("rs_t6", "f", units, judge=judge)
    asyncio.run(s.start())
    asyncio.run(s.handle_answer("嗯……好像是汉朝教育那块"))          # 真实回忆轮（乱猜）→ L0
    snap = asyncio.run(s.handle_answer("零碎地记得一点，说不太清"))   # 真实回忆轮 → L0 → S2
    check("S2 止损", s.states[0]["reason"] == "S2_stuck", s.states[0]["reason"])
    check("卡层 L0", s.states[0]["stuck_level"] == "L0")
    check("会话继续推进/结束", snap["done"] is True)


# ── 7. 磨蹭型 → S3（6 轮） ──

def test_s3_turn_limit():
    print("[7] 真实回忆轮超限 → S3 止损")
    units = build_review_units(_tiny_tree())["units"]
    judge = scripted([J(level="L2", matched_points=[0], gaps=["缺一条"], reply="继续？")])
    s = ReviewSession("rs_t7", "f", units, judge=judge)
    asyncio.run(s.start())
    for i in range(6):
        snap = asyncio.run(s.handle_answer(f"回答{i}"))
    check("恰好 6 轮", s.states[0]["turn_count"] == 6, str(s.states[0]["turn_count"]))
    check("S3 止损", s.states[0]["reason"] == "S3_turns", s.states[0]["reason"])
    check("计数器上限展示", snap["counters"]["turn_max"] == 6)


# ── 8. 主动放弃 → S4 ──

def test_s4_skip():
    print("[8] 主动放下 → 先讲答案再换题")
    units = build_review_units(_two_unit_tree())["units"]
    s = ReviewSession("rs_t8", "f", units, judge=scripted([J()]))
    asyncio.run(s.start())
    snap = asyncio.run(s.skip())
    check("第一点标记失败", s.states[0]["reason"] == "S4_abandon")
    check("自动进入下一个单元", snap["current"] and snap["current"]["title"] == "第二章",
          str(snap["current"]))
    taught = [m for m in s.messages if m.get("kind") == "reveal"]
    check("跳过后给出整理好的答案", any("第一节 A" in m["text"] and "第二节 B" in m["text"]
                                     for m in taught), str(taught))
    ans = [m for m in taught if "第一节 A" in m["text"]][0]["text"]
    check("答案陈述收尾不追问", not ans.rstrip().endswith("？"), ans[-20:])


# ── 9. 索要答案 → 给要点，回头再测 ──

def test_h4_reveal():
    print("[9] 索要答案 → 给要点、回头再测")
    units = build_review_units(_tiny_tree())["units"]
    with tempfile.TemporaryDirectory() as td:
        store = ReviewStore(td)
        judge = scripted([
            J(level="L1", asks_for_answer=True, reply="要提示吗？"),
            J(level="L2", reply="复述得如何？"),
        ])
        s = ReviewSession("rs_t9", "f", units, judge=judge, store=store)
        asyncio.run(s.start())
        snap = asyncio.run(s.handle_answer("别问了，直接告诉我答案"))
        reveal = [m for m in s.messages if m["kind"] == "reveal"]
        check("给出要点 reveal", len(reveal) == 1 and "第一节 对象" in reveal[0]["text"],
              str(reveal))
        check("进入 h4 复述", s.states[0]["awaiting"] == "h4_restate")
        snap = asyncio.run(s.handle_answer("第一节是对象……"))
        check("判定作废 → 半过关", snap["units"][0]["status"] == "half", str(snap["units"][0]))
        check("原因为 h4_reveal", s.states[0]["reason"] == "h4_reveal")
        check("H4 不计入提示次数", s.states[0]["hint_count"] == 0)
        prog = store.load_progress("f")
        check("进入待复测", prog["retest_queue"][0]["reason"] == "h4_reveal")

        # 关键词兜底：judge 未标记 asks_for_answer，仅靠文本识别
        s2 = ReviewSession("rs_t9b", "f", units, judge=scripted([J(level="L1")]))
        asyncio.run(s2.start())
        asyncio.run(s2.handle_answer("直接说答案吧"))
        check("关键词识别索要答案", s2.states[0]["awaiting"] == "h4_restate")


# ── 10. 提示阶梯不跳级 ──

def test_hint_no_skip():
    print("[10] 提示阶梯校验（LLM 要 H3 先给 H1）")
    units = build_review_units(_tiny_tree())["units"]
    judge = scripted([J(level="L2", matched_points=[0], next_action="hint",
                        hint_level="H3", reply="再想想？")])
    s = ReviewSession("rs_t10", "f", units, judge=judge)
    asyncio.run(s.start())
    asyncio.run(s.handle_answer("只记得一点"))
    check("强制从 H1 开始", s.states[0]["hint_levels"] == ["H1"],
          str(s.states[0]["hint_levels"]))


# ── 11. 判分归一化 ──

def test_normalise():
    print("[11] 判分归一化")
    n = _normalise_judged({"level": "L3（结构化）", "matched_points": [1, "条目2", 0, 99],
                           "reply": "为什么？因为结构清晰。", "hint_level": "H9"}, 3)
    check("层级容错", n["level"] == "L3", n["level"])
    check("编号混合解析+去重", n["matched_points"] == [0, 1], str(n["matched_points"]))
    check("非法提示级别清空", n["hint_level"] is None)
    check("reply 原样保留", n["reply"] == "为什么？因为结构清晰。", n["reply"])
    n2 = _normalise_judged({"level": "L2", "feedback": "说对了一半。",
                            "question": "另一半呢？"}, 2)
    check("旧字段 feedback/question 拼成 reply", "说对了一半" in n2["reply"]
          and "另一半呢" in n2["reply"], n2["reply"])


# ── 12. 答案泄漏拦截 ──

def test_leak():
    print("[12] 答案泄漏检测与拦截")
    pts = ["教育心理学的定义与研究对象"]
    check("直接包含条目 → 泄漏", detect_leak("那「教育心理学的定义与研究对象」你记得吗？", pts, None))
    check("H4 豁免", not detect_leak("教育心理学的定义与研究对象", pts, "H4"))
    check("无关文本不误报", not detect_leak("再想想还有哪一块？", pts, None))

    units = build_review_units(_two_unit_tree())["units"]  # points: 第一节 A / 第二节 B
    units[0]["reference_points"] = ["教育心理学的定义与研究对象", "研究方法体系"]
    judge = scripted([J(level="L2", matched_points=[0],
                        reply="说对了一部分。那「教育心理学的定义与研究对象」这部分呢？")])
    s = ReviewSession("rs_t12", "f", units, judge=judge)
    asyncio.run(s.start())
    asyncio.run(s.handle_answer("大概讲研究什么"))
    out = s.messages[-1]["text"]
    check("泄漏追问被替换", "教育心理学的定义" not in out, out)
    check("替换为通用追问", "再想想" in out or "哪一块" in out, out)


# ── 13. 求助轮不计入止损 ──

def test_help_turns_exempt():
    print("[13] 求助轮不计入止损（想不起来/给点提示）")
    units = build_review_units(_tiny_tree())["units"]
    judge = scripted([J(level="L0", reply="再想想？")])
    s = ReviewSession("rs_t13h", "f", units, judge=judge)
    asyncio.run(s.start())
    asyncio.run(s.handle_answer("提示我一下"))
    check("求助给 H1 且不计轮次", s.states[0]["hint_levels"] == ["H1"]
          and s.states[0]["turn_count"] == 0,
          f"hints={s.states[0]['hint_levels']} turns={s.states[0]['turn_count']}")
    check("求助未判分（judge 未调用）", len(judge.calls) == 0, str(len(judge.calls)))
    asyncio.run(s.handle_answer("想不起来了"))
    check("再求助给 H2 仍不计轮次", s.states[0]["hint_levels"] == ["H1", "H2"]
          and s.states[0]["turn_count"] == 0,
          f"hints={s.states[0]['hint_levels']} turns={s.states[0]['turn_count']}")
    snap = asyncio.run(s.handle_answer("想不起来"))
    check("提示用尽仍求助 → 讲答案并收掉", s.states[0]["status"] == "failed"
          and s.states[0]["reason"] == "S4_abandon", str(s.states[0]))
    check("求助轮从未进 S2/S3", s.states[0]["turn_count"] == 0)
    taught = [m for m in s.messages if m.get("kind") == "reveal"]
    check("求助放弃后给出答案", any("第一节 对象" in m["text"] for m in taught), str(taught))

    # 真实回忆轮才计止损：求助轮夹在中间不打断"连续真实失败"的计数
    s2 = ReviewSession("rs_t13h2", "f", units, judge=scripted([J(level="L0", reply="再想想？")]))
    asyncio.run(s2.start())
    asyncio.run(s2.handle_answer("第一个想法是汉朝那边"))       # 真实轮 1 → L0 → 自动 H1
    asyncio.run(s2.handle_answer("提示我一下"))                  # 求助 → H2，不计轮
    asyncio.run(s2.handle_answer("只零碎记得一点，说不太清"))    # 真实轮 2 → L0 → 止损
    check("止损只看真实回忆轮", s2.states[0]["reason"] in ("S2_stuck", "S1_hints"),
          s2.states[0]["reason"])
    check("真实轮计数为 2", s2.states[0]["turn_count"] == 2, str(s2.states[0]["turn_count"]))


# ── 14. 对话无过程记账词 ──

BANNED = re.compile(r"L[0-4]|回合|提示次数|半过关|未过关|过关判定|待复测|兜底|止损|判定作废|H[0-4]|S[1-4]|C[1-4]")


def test_no_process_words():
    print("[14] 对外话语无过程记账词")
    units = build_review_units(_two_unit_tree())["units"]
    with tempfile.TemporaryDirectory() as td:
        store = ReviewStore(td)
        judge = scripted([
            J(level="L1", gaps=["缺"], reply="说到一点，还差一截。再想想？"),
            J(level="L2", matched_points=[0], gaps=["缺"], reply="比刚才全了。"),
        ])
        s = ReviewSession("rs_t14w", "f", units, judge=judge, store=store)
        asyncio.run(s.start())
        asyncio.run(s.handle_answer("第一节 A 什么的"))   # → 自动 H1 提示
        asyncio.run(s.handle_answer("直接说答案吧"))      # → 给要点
        asyncio.run(s.handle_answer("第一节是对象……"))   # → 复述后收掉 → 过渡
        asyncio.run(s.handle_answer("提示我一下"))        # → 求助提示
        asyncio.run(s.handle_answer("想不起来了"))        # → 求助提示
        asyncio.run(s.skip())                            # → 讲答案（teach）
        snap = asyncio.run(s.end_now())                  # → 自然收尾
        bad = [(m["text"], BANNED.findall(m["text"])) for m in s.messages
               if m["role"] != "learner" and BANNED.search(m["text"])]
        check("全部对外话语无记账词", not bad, str(bad))
        check("收尾语存在", bool(snap["report"]), str(snap.get("report")))


# ── 15. 多单元推进 / 持久化 / 续跑 ──

def test_multi_unit_report_persist():
    print("[15] 多单元推进 + 持久化 + 崩溃续跑")
    units = build_review_units(_two_unit_tree())["units"]
    with tempfile.TemporaryDirectory() as td:
        store = ReviewStore(td)
        judge = scripted([
            J(level="L3", matched_points=[0, 1], gaps=[], reply="好"),   # u1 通过
            J(level="L0", reply="再想想？"),                              # u2 → H1
            J(level="L0", reply="再想想？"),                              # u2 → S2
        ])
        s = ReviewSession("rs_t15", "f", units, judge=judge, store=store)
        asyncio.run(s.start())
        asyncio.run(s.handle_answer("第一节 A，第二节 B"))
        snap = asyncio.run(s.handle_answer("嗯……好像是汉朝那块"))
        check("通过后自动出下一单元题", snap["current"]["title"] == "第二章",
              str(snap["current"]))
        snap = asyncio.run(s.handle_answer("只想起一个词，剩下的说不清"))
        check("结束并自然收尾", snap["done"] and bool(snap["report"]), snap["report"])
        check("收尾无计数与层级", "过关" not in (snap["report"] or "")
              and "L0" not in (snap["report"] or ""), snap["report"])
        check("计数快照（内部）", snap["counts"] == {"passed": 1, "half": 0, "failed": 1},
              str(snap["counts"]))

        prog = store.load_progress("f")
        check("待复测=通过点", [x["unit_id"] for x in prog["retest_queue"]] == [units[0]["unit_id"]])
        check("薄弱=未通过点", [x["unit_id"] for x in prog["weak_units"]] == [units[1]["unit_id"]])
        check("会话摘要落盘", len(prog["sessions"]) == 1
              and prog["sessions"][0]["passed"] == 1)

        # 崩溃续跑：从磁盘状态重建
        state = store.load_session("rs_t15")
        check("会话运行态落盘", state is not None and state["done"] is True)
        s2 = ReviewSession.from_state(state, judge=scripted([J()]), store=store)
        snap2 = s2.snapshot()
        check("续跑快照一致", snap2["counts"] == snap["counts"] and snap2["done"] is True)


# ── 16. 复测范围 ──

def test_retest_scope():
    print("[16] 复测范围（待复测队列 → 单元）")
    tree = _two_unit_tree()
    units = build_review_units(tree)["units"]
    with tempfile.TemporaryDirectory() as td:
        store = ReviewStore(td)
        judge = scripted([J(level="L3", matched_points=[0, 1], gaps=[], reply="好")])
        s = ReviewSession("rs_t16", "f", units, judge=judge, store=store)
        asyncio.run(s.start())
        asyncio.run(s.handle_answer("答"))
        prog = store.load_progress("f")
        ids = [x["unit_id"] for x in prog["retest_queue"]]
        picked = build_review_units(tree, scope={"unit_ids": ids})["units"]
        check("复测范围只取队列单元", len(picked) == 1 and picked[0]["unit_id"] == units[0]["unit_id"])


# ── 17. 空范围 ──

def test_empty_scope():
    print("[17] 空范围")
    tree = {"title": "t", "children": [{"title": "章", "children": [
        {"title": "唯一一节", "children": []}]}]}
    units = build_review_units(tree)["units"]
    check("无可复习单元", units == [])
    s = ReviewSession("rs_t17", "f", units, judge=scripted([J()]))
    snap = asyncio.run(s.start())
    check("会话直接结束并给出说明",
          snap["done"] and "没有找到合适" in snap["messages"][-1]["text"])


# ── 18. 流式出字 ──

def test_stream_delta():
    print("[18] 流式出字（on_delta 回调）")
    units = build_review_units(_tiny_tree())["units"]

    chunks = []
    async def sayer(kind, ctx, on_delta=None):
        for piece in ["咱们聊聊", "**建构主义**。"]:
            if on_delta:
                on_delta(piece)
        return "咱们聊聊**建构主义**。"
    s = ReviewSession("rs_t18", "f", units, judge=scripted([J()]), sayer=sayer)
    asyncio.run(s.start(on_delta=chunks.append))
    check("开场流式出字与消息一致", "".join(chunks) == s.messages[0]["text"]
          == "咱们聊聊**建构主义**。", str(chunks))

    jchunks = []
    async def judge(unit, state, answer, is_restate=False, on_delta=None):
        if on_delta:
            on_delta("说对了一块，")
            on_delta("再补一块？")
        return _normalise_judged({"level": "L2", "matched_points": [0], "gaps": ["缺"],
                                  "reply": "说对了一块，再补一块？"}, 2)
    s2 = ReviewSession("rs_t18b", "f", units, judge=judge)
    asyncio.run(s2.start())
    asyncio.run(s2.handle_answer("第一节 对象", on_delta=jchunks.append))
    check("判分回应流式出字", "".join(jchunks) == "说对了一块，再补一块？", str(jchunks))

    # 分隔符过滤：判分 JSON 段不得播给前端
    from review_engine import _sep_filter
    got = []
    f = _sep_filter(got.append)
    f("你")
    f("好###JS")
    f("ON###\n{\"level\":\"L1\"}")
    f("尾巴不发")
    check("JSON 段被过滤", "".join(got) == "你好", str(got))

    # 考点讲解流要先带 kind=reveal，前端才能从第一个 token 起使用考点气泡
    async def reveal_sayer(kind, ctx, on_delta=None):
        if kind == "explain":
            if on_delta:
                on_delta("考点第一段")
                on_delta("，考点第二段。")
            return "考点第一段，考点第二段。"
        return "开场问题？"
    s3 = ReviewSession("rs_t18c", "f", units, judge=scripted([J()]), sayer=reveal_sayer)
    asyncio.run(s3.start())
    reveal_events = []
    asyncio.run(s3.skip(on_delta=reveal_events.append))
    check("考点讲解首个 token 携带 reveal 类型",
          reveal_events and isinstance(reveal_events[0], dict)
          and reveal_events[0].get("kind") == "reveal"
          and "".join(e.get("text", "") for e in reveal_events if isinstance(e, dict))
              == "考点第一段，考点第二段。",
          str(reveal_events[:3]))


# ── 19. 长期学习者模型：间隔调度 ──

def test_learner_model_schedule():
    print("[19] 长期学习者模型：间隔阶梯 / 失败立即到期 / 到期排序")
    units = build_review_units(_two_unit_tree())["units"]
    u1, u2 = units[0], units[1]
    with tempfile.TemporaryDirectory() as td:
        store = ReviewStore(td)
        now = time.time()
        store.record_unit_result("f", u1, "passed", "L3", "s1")
        e = store.load_progress("f")["units"][u1["unit_id"]]
        check("首次过关 → 约 1 天后到期", abs(e["next_due"] - (now + 86400)) < 60,
              str(e.get("next_due")))
        check("连过计数 1", e["passes"] == 1, str(e))
        store.record_unit_result("f", u1, "passed", "L4", "s2")
        e = store.load_progress("f")["units"][u1["unit_id"]]
        check("第二次过关 → 间隔拉长到约 2 天",
              abs(e["next_due"] - (time.time() + 2 * 86400)) < 60, str(e.get("next_due")))
        store.record_unit_result("f", u1, "passed", "L4", "s3")
        e = store.load_progress("f")["units"][u1["unit_id"]]
        check("第三次过关 → 间隔约 4 天", abs(e["next_due"] - (time.time() + 4 * 86400)) < 60)
        check("未到期不列入今日到期", store.due_count("f") == 0, str(store.due_count("f")))
        store.record_unit_result("f", u2, "failed", "L1", "s1", "S1_hints")
        ids = store.due_unit_ids("f")
        check("复练顺序：立即到期的薄弱点在前", ids == [u2["unit_id"], u1["unit_id"]], str(ids))
        check("due_count 只数已到期", store.due_count("f") == 1, str(store.due_count("f")))
        e2 = store.load_progress("f")["units"][u2["unit_id"]]
        check("失败清零连过并记遗忘", e2["passes"] == 0 and e2["lapses"] == 1, str(e2))
        r = build_review_units(_two_unit_tree(),
                               scope={"unit_ids": [u2["unit_id"], u1["unit_id"]]})["units"]
        check("unit_ids 顺序被尊重（复练调度顺序）",
              [x["unit_id"] for x in r] == [u2["unit_id"], u1["unit_id"]],
              str([x["unit_id"] for x in r]))


# ── 20. 学习模式：锚点检索 / 章节范围 / 截断 / 出处 ──

def test_learn_mode():
    print("[20] 学习模式：锚点检索 / 章节范围 / 截断 / 出处")
    from review_engine import (build_learn_context, build_learn_prompt,
                               find_anchor_nodes, _query_terms,
                               is_book_scope_question,
                               LEARN_SYSTEM, LEARN_SYSTEM_COMPLETE)
    tree = _learn_tree()
    ctx = build_learn_context(tree, chapter="", question="颜之推的家庭教育思想是什么？")
    check("问题命中颜之推节点且含原文",
          any("颜之推" in src for src in ctx["sources"]) and "德艺周厚" in ctx["text"],
          str(ctx["sources"]))
    check("出处按路径给出（1–4 条）",
          1 <= len(ctx["sources"]) <= 4 and all(" › " in src for src in ctx["sources"]))
    ctx2 = build_learn_context(tree, chapter="第三节 隋唐时期的选士制度", question="这章讲了什么")
    check("章节模式：结构含两个知识点标题",
          "【结构】" in ctx2["text"] and "知识点一" in ctx2["text"] and "知识点二" in ctx2["text"])
    check("章节模式头部标注当前章节", "当前章节：第四章" in ctx2["text"], ctx2["text"][:60])
    check("章节模式含两个节点内容", len(ctx2["sources"]) >= 2, str(ctx2["sources"]))
    small = build_learn_context(tree, chapter="", question="颜之推", max_chars=260)
    check("上下文按上限截断", len(small["text"]) <= 260, str(len(small["text"])))
    bare = build_learn_context(tree, chapter="", question="zzzz")
    check("无命中时退化为模块概览", "【结构】" in bare["text"] and "第四章" in bare["text"])

    book_tree = {"title": "外国教育史", "guide": "全书导学：古代教育、近代教育、现代教育三大部分。",
                 "children": [
                     {"title": "第一章 古代教育", "summary": "古代教育的发展脉络。", "children": [
                         {"title": "第一节 古希腊教育", "content": "古希腊教育内容。", "children": []}]},
                     {"title": "第二章 近代教育", "summary": "近代教育思想与制度。", "children": [
                         {"title": "第一节 赫尔巴特", "content": "赫尔巴特教育思想。", "children": []}]},
                     {"title": "第三章 现代教育", "summary": "现代教育思潮。", "children": [
                         {"title": "第一节 杜威", "content": "杜威教育思想。", "children": []}]},
                 ]}
    book_ctx = build_learn_context(book_tree, chapter="第二章 近代教育",
                                   question="这本书主要讲了什么？")
    check("全书问题不再被当前章节劫持",
          book_ctx.get("scope") == "book"
          and "全书导学：古代教育、近代教育、现代教育三大部分" in book_ctx["text"]
          and "第一章 古代教育" in book_ctx["text"]
          and "第二章 近代教育" in book_ctx["text"]
          and "第三章 现代教育" in book_ctx["text"],
          book_ctx["text"][:320])
    check("全书问题出处覆盖多个顶层章节",
          any("第一章" in x for x in book_ctx["sources"])
          and any("第三章" in x for x in book_ctx["sources"]),
          str(book_ctx["sources"]))
    check("章节问题仍按当前章节检索",
          not is_book_scope_question("这一章主要讲了什么？")
          and is_book_scope_question("这本书主要讲了什么？"))
    prompt = build_learn_prompt(ctx["text"], "颜之推讲了什么？",
                                [{"role": "learner", "text": "上一个问题"},
                                 {"role": "guide", "text": "上一个回答"}])
    check("提示词含资料 / 问题 / 历史",
          "【资料】" in prompt and "颜之推讲了什么？" in prompt
          and "上一个问题" in prompt and "老师：上一个回答" in prompt)
    prompt_complete = build_learn_prompt(ctx["text"], "颜之推讲了什么？",
                                         completion_mode=True)
    check("知识补全提示词要求区分资料与 AI 补充",
          "【AI 补充】" in prompt_complete and "资料没有覆盖" in prompt_complete,
          prompt_complete[-180:])
    check("默认学习提示仍坚持资料接地",
          "只依据" in LEARN_SYSTEM and "绝不编造" in LEARN_SYSTEM)
    check("知识补全系统提示要求标注补充来源",
          "资料依据" in LEARN_SYSTEM_COMPLETE and "AI 补充" in LEARN_SYSTEM_COMPLETE)
    prompt_prefs = build_learn_prompt(
        ctx["text"], "颜之推讲了什么？", reply_style="concise",
        thinking_effort="deep", citation_style="strong",
        language="bilingual", custom_instruction="多举日常例子")
    check("个性化偏好进入学习提示词",
          "3–5 个要点" in prompt_prefs and "可核验的推导摘要" in prompt_prefs
          and "更多书内出处" in prompt_prefs and "关键术语后括注英文" in prompt_prefs
          and "多举日常例子" in prompt_prefs,
          prompt_prefs[-420:])
    terms = _query_terms("颜之推的家庭教育思想是什么")
    check("关键词去停用词", "什么" not in terms and "颜之推" in terms, str(terms[:8]))
    anchors = find_anchor_nodes(tree, "", "隋唐 颜之推")
    overlap = False
    for i, (pi, _) in enumerate(anchors):
        for j, (pj, _) in enumerate(anchors):
            if i != j and pi[:len(pj)] == pj:
                overlap = True
    check("锚点去重（祖先/后代不重叠）", not overlap and len(anchors) >= 1,
          str([p for p, _ in anchors]))


# ── 21. 「不知道」= 求助：不计轮次、走提示阶梯（修复"说了不知道就结束"） ──

def test_unknown_counts_as_help():
    print("[21] 「不知道」按求助处理：提示阶梯感知 / 不计卡壳止损")
    from review_engine import is_help_only
    check("整句识别为求助",
          all(is_help_only(x) for x in [
              "不知道", "不知道啊", "我也不知道", "还是不知道", "不会", "忘了",
              "不记得了", "想不起来了", "没印象", "没学过", "答不上来"]),
          str([x for x in ["不知道", "不知道啊", "我也不知道", "还是不知道", "不会", "忘了",
                           "不记得了", "想不起来了", "没印象", "没学过", "答不上来"]
               if not is_help_only(x)]))
    check("作答内容不被误判",
          not is_help_only("尊孔崇儒")
          and not is_help_only("我还不知道文教政策是啥，但太学我记得")
          and not is_help_only("不知道怎么办"))
    units = build_review_units(_tiny_tree())["units"]
    judge = scripted([J(level="L3", matched_points=[0, 1], gaps=[], reply="顺。")])
    s = ReviewSession("rs_t21", "f", units, judge=judge)
    asyncio.run(s.start())
    n0 = len(judge.calls)
    snap = asyncio.run(s.handle_answer("不知道"))
    check("求助不计轮次、不判分",
          len(judge.calls) == n0 and s.states[0]["turn_count"] == 0,
          f"calls={len(judge.calls)} turns={s.states[0]['turn_count']}")
    check("第一次求助 → H1 提示（对话形式）",
          s.states[0]["hint_count"] == 1 and s.states[0]["hint_levels"] == ["H1"]
          and snap["messages"][-1]["kind"] == "hint",
          str(s.states[0]["hint_levels"]))
    snap = asyncio.run(s.handle_answer("还是不知道"))
    check("第二次求助 → H2 提示（仍未结束）",
          s.states[0]["hint_count"] == 2 and s.states[0]["hint_levels"] == ["H1", "H2"]
          and snap["done"] is False,
          str(s.states[0]["hint_levels"]))
    snap = asyncio.run(s.handle_answer("不知道啊"))
    check("提示用尽才讲解并收掉该点（S4 而非 S2 卡壳止损）",
          s.states[0]["status"] == "failed" and s.states[0]["reason"] == "S4_abandon"
          and snap["done"] is True,
          f"reason={s.states[0]['reason']} done={snap['done']}")
    # 开场透明化：intro_label → 第一条消息说明复习范围
    s2 = ReviewSession("rs_t21b", "f", build_review_units(_tiny_tree())["units"],
                       judge=scripted([J()]), intro_label="第一章 概述（1 个考点）")
    snap2 = asyncio.run(s2.start())
    check("开局先说明本轮复习范围",
          snap2["messages"][0]["text"] == "开始复习 · 第一章 概述（1 个考点）",
          str(snap2["messages"][0]["text"])[:60])
    # 正常作答不受影响：有内容的回答仍然走判分
    s3 = ReviewSession("rs_t21c", "f", build_review_units(_tiny_tree())["units"],
                       judge=scripted([J(level="L3", matched_points=[0, 1], gaps=[], reply="好。")]))
    asyncio.run(s3.start())
    asyncio.run(s3.handle_answer("第一节讲对象，第二节讲方法"))
    check("正常作答仍被判定", s3.states[0]["turn_count"] == 1, str(s3.states[0]["turn_count"]))


# ── 22. 原生多轮 / 先诊断后说话 / 临时答疑 / 关联检索 / 真实推理参数 ──

def test_native_multiturn_and_deep_dialogue():
    print("[22] 原生多轮 / JSON 先行 / 临时答疑 / 关联检索 / 推理参数")
    from review_engine import (
        build_learn_messages, build_learn_context, build_judge_messages,
        _split_judge_output, _say_after_filter, LEARN_SYSTEM,
        is_conceptual_question,
    )
    from app_settings import chat_body_extras

    long_reply = "上一轮推导细节。" * 120
    history = [
        {"role": "learner", "text": "更早的问题"},
        {"role": "guide", "text": "更早的回答"},
        {"role": "learner", "text": "上一轮追问"},
        {"role": "guide", "text": long_reply},
        {"role": "learner", "text": "这一轮接着问"},
        {"role": "guide", "text": "最近一条完整回答"},
    ]
    msgs = build_learn_messages("【结构】略", "刚才第二点再解释一下", history,
                                reply_style="detailed", language="bilingual",
                                custom_instruction="多用类比")
    roles = [m["role"] for m in msgs]
    check("学习消息使用原生 system/user/assistant 数组",
          roles[0] == "system" and "user" in roles and "assistant" in roles
          and "学习者：" not in msgs[-1]["content"], str(roles))
    recent_asst = [m["content"] for m in msgs if m["role"] == "assistant"]
    check("最近两轮回答完整保留，不再 300 字截断",
          any(len(x) == len(long_reply) for x in recent_asst),
          str([len(x) for x in recent_asst]))
    check("更早历史压缩进 system 脉络",
          "较早对话脉络" in msgs[0]["content"] and "更早的问题" in msgs[0]["content"])

    judge_msgs = build_judge_messages(
        build_review_units(_tiny_tree())["units"][0],
        {"turn_count": 1, "hint_count": 0, "last_gaps": [], "trap_list": []},
        "学习者本轮回答",
        history=[{"role": "guide", "text": "上一轮老师原话"},
                 {"role": "learner", "text": "上一轮学生原话"}],
        preferences={"language": "bilingual", "customInstruction": "语气温和"})
    check("判分消息携带真实对话历史",
          any(m.get("content") == "上一轮老师原话" for m in judge_msgs)
          and any(m.get("content") == "上一轮学生原话" for m in judge_msgs),
          str(judge_msgs))
    check("追问偏好进入判分 system",
          "关键术语后括注英文" in judge_msgs[0]["content"]
          and "语气温和" in judge_msgs[0]["content"])
    check("判分最新消息带硬性 JSON/SAY 顺序提醒",
          "###SAY###" in judge_msgs[-1]["content"]
          and "不要省略 JSON" in judge_msgs[-1]["content"])
    check("概念疑问识别不误伤正常作答",
          is_conceptual_question("为什么这两者有关？")
          and not is_conceptual_question("第一节讲对象，第二节讲方法"))

    raw = ('{"level":"L2","matched_points":[0],"gaps":["第二点"],'
           '"confusions":[],"trap_resolved":false,"asks_for_answer":false,'
           '"next_action":"hint","hint_level":"H1"}\n'
           '###SAY###\n先说结论，再想第二点。')
    parsed, reply = _split_judge_output(raw)
    check("新协议先解析诊断 JSON 再取回复",
          parsed and parsed["level"] == "L2" and reply == "先说结论，再想第二点。",
          str((parsed, reply)))
    emitted = []
    push = _say_after_filter(emitted.append)
    push('{"level":"L2"}')
    push('###SA')
    push('Y###\n先说结论')
    push('，再想第二点。')
    check("流式转发先静默诊断段，只播 SAY 之后的回复",
          "".join(emitted).strip() == "先说结论，再想第二点。" and '"level"' not in "".join(emitted),
          repr("".join(emitted)))

    # 临时答疑：问概念不计回忆轮，回答后恢复考测
    units = build_review_units(_tiny_tree())["units"]
    clarify_judge = scripted([
        J(level="L3", matched_points=[0, 1], gaps=[], reply="这次说全了。"),
    ])
    s = ReviewSession("rs_t22", "f", units, judge=clarify_judge)
    asyncio.run(s.start())
    snap = asyncio.run(s.handle_answer("为什么这两节要并列？"))
    check("理解性反问进入 clarify", s.messages[-1]["kind"] == "clarify", str(s.messages[-1]))
    check("答疑不占回忆轮与层级记录",
          s.states[0]["turn_count"] == 0 and s.states[0]["levels"] == [],
          f"turns={s.states[0]['turn_count']} levels={s.states[0]['levels']}")
    check("答疑后仍停留在当前考点", snap["current"]["title"] == units[0]["title"])
    snap = asyncio.run(s.handle_answer("第一节讲对象，第二节讲方法"))
    check("答疑后正常作答继续判定并过关",
          s.states[0]["status"] == "passed" and s.states[0]["turn_count"] == 1,
          str(s.states[0]))

    # 非显式问句、但模型识别为理解性反问时，也走 clarify 分支
    judged_clarify = scripted([
        J(level="L1", next_action="clarify", reply="这两块不是一回事，先把界线理清。回到原题再说说？"),
    ])
    s2 = ReviewSession("rs_t22b", "f", units, judge=judged_clarify)
    asyncio.run(s2.start())
    asyncio.run(s2.handle_answer("嗯，这两块我老是混在一起"))
    check("判分器返回 clarify 时同样不扣回忆轮",
          s2.messages[-1]["kind"] == "clarify" and s2.states[0]["turn_count"] == 0,
          str(s2.states[0]))

    # 检索：锚点兄弟 + 跨章节关联
    tree = {"title": "教育史", "children": [
        {"title": "第一章 家训", "children": [
            {"title": "颜之推", "keywords": ["家庭教育", "德艺周厚"],
             "content": "颜之推强调家庭教育应尽早进行。", "children": []},
            {"title": "韩愈", "keywords": ["师道"], "content": "韩愈强调尊师重道。", "children": []},
        ]},
        {"title": "第二章 家庭", "children": [
            {"title": "家庭教育原则", "keywords": ["家庭教育", "早期教育"],
             "content": "家庭教育重视早期习惯养成。", "children": []},
        ]},
    ]}
    ctx = build_learn_context(tree, question="颜之推")
    check("检索带入同父兄弟节点便于对比", "韩愈" in ctx["text"], ctx["text"][:300])
    check("检索补充跨章节关联节点",
          any("第二章" in x for x in ctx.get("related_sources", [])),
          str(ctx.get("related_sources")))

    # 同名父级会让标题路径回溯到错误分支；检索应降级而不是 500
    duplicate_tree = {"title": "重复路径教材", "children": [
        {"title": "同名章", "children": [
            {"title": "甲节点", "content": "甲内容", "children": []},
        ]},
        {"title": "同名章", "children": [
            {"title": "乙节点", "keywords": ["目标词"], "content": "乙节点内容", "children": []},
        ]},
    ]}
    try:
        dup_ctx = build_learn_context(duplicate_tree, question="目标词")
        dup_ok = "乙节点内容" in dup_ctx.get("text", "")
    except Exception as e:
        dup_ok = False
        dup_ctx = {"error": repr(e)}
    check("同名父级路径回溯失败时检索不崩溃",
          dup_ok, str(dup_ctx)[:300])

    check("学习提示按问题类型组织并包含微追问",
          "为什么" in LEARN_SYSTEM and "微追问" in LEARN_SYSTEM
          and "逻辑桥梁" in LEARN_SYSTEM and "短句、要点式" not in LEARN_SYSTEM)
    check("深入档向 opencode 发送真实推理预算",
          chat_body_extras("https://opencode.ai/zen/go/v1/chat/completions",
                           reasoning_effort="medium").get("reasoning_effort") == "medium")
    check("默认解析调用仍关闭无谓思考",
          chat_body_extras("https://opencode.ai/zen/go/v1/chat/completions")
          .get("reasoning_effort") == "none")


if __name__ == "__main__":
    test_units()
    test_first_answer_pass()
    test_progressive_c4()
    test_hint_assisted_half()
    test_s1_hints_exhausted()
    test_s2_stuck()
    test_s3_turn_limit()
    test_s4_skip()
    test_h4_reveal()
    test_hint_no_skip()
    test_normalise()
    test_leak()
    test_help_turns_exempt()
    test_no_process_words()
    test_multi_unit_report_persist()
    test_retest_scope()
    test_empty_scope()
    test_stream_delta()
    test_learner_model_schedule()
    test_learn_mode()
    test_unknown_counts_as_help()
    test_native_multiturn_and_deep_dialogue()
    print(f"\n{'=' * 44}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
