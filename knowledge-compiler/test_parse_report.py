"""parse_report 的离线测试：token 累计 / 树统计 / 报告组装。

运行：python3 knowledge-compiler/test_parse_report.py
"""

import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import parse_report as PR

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


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


# 真实形态：单册包装结构（Knowledge Tree → 书 → 模块）
WRAPPED = {
    "title": "Knowledge Tree",
    "children": [{
        "title": "外教", "level": 1, "children": [
            {"title": "第一章", "level": 2, "exam_points": [{"point": "a"}, {"point": "b"}],
             "children": [{"title": "1.1", "level": 3, "summary": "有摘要", "children": []}]},
            {"title": "第二章", "level": 2, "children": []},
        ],
    }],
}

# 非包装结构：顶层就是模块
FLAT = {
    "title": "Knowledge Tree",
    "children": [
        {"title": "模块 A", "level": 1, "summary": "s", "children": []},
        {"title": "模块 B", "level": 1, "children": []},
    ],
}


def test_meter_basic():
    print("[1] UsageMeter 基本累计")
    m = PR.UsageMeter()
    check("初始为零", m.to_dict()["total_tokens"] == 0 and m.calls == 0)
    m.add({"prompt_tokens": 100, "completion_tokens": 40,
           "completion_tokens_details": {"reasoning_tokens": 25},
           "prompt_tokens_details": {"cached_tokens": 10}}, "m1")
    d = m.to_dict()
    check("分类累加", d["prompt_tokens"] == 100 and d["completion_tokens"] == 40, json.dumps(d))
    check("思考量单列", d["reasoning_tokens"] == 25)
    check("缓存命中单列", d["cached_tokens"] == 10)
    check("合计 = 入 + 出", d["total_tokens"] == 140)
    check("按模型记账", d["models"] == {"m1": 140}, json.dumps(d["models"]))
    check("scope 标记为增强阶段", d["scope"] == "ai_enhancing")


def test_meter_robustness():
    print("[2] UsageMeter 容错（统计绝不能被脏数据打挂）")
    m = PR.UsageMeter()
    m.add(None)
    m.add({})
    m.add({"prompt_tokens": "123", "completion_tokens": None})
    m.add({"prompt_tokens": "abc", "completion_tokens": 5})
    m.add({"prompt_tokens": 7, "completion_tokens_details": "not-a-dict",
           "prompt_tokens_details": None})
    d = m.to_dict()
    check("脏数据不抛异常且只计合法部分",
          d["prompt_tokens"] == 130 and d["completion_tokens"] == 5, json.dumps(d))
    check("只有能解析出字典的才计入调用次数", d["calls"] == 4, json.dumps(d))

    r = PR.UsageMeter()
    check("from_response 遇非 JSON 返回 False",
          r.from_response(FakeResp(ValueError("bad json"))) is False and r.calls == 0)
    check("from_response 无 usage 字段时返回 False",
          r.from_response(FakeResp({"choices": []})) is False)
    check("from_response 正常时返回 True",
          r.from_response(FakeResp({"usage": {"prompt_tokens": 3, "completion_tokens": 4}}), "m")
          is True and r.calls == 1 and r.total_tokens == 7)


def test_meter_concurrency():
    print("[3] UsageMeter 并发累加（增强阶段是并发调用，不能丢数）")
    m = PR.UsageMeter()
    per_thread, threads = 250, 8

    def worker():
        for _ in range(per_thread):
            m.add({"prompt_tokens": 10, "completion_tokens": 5})

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    d = m.to_dict()
    check(f"{threads} 线程 × {per_thread} 次全部计入",
          d["calls"] == threads * per_thread, json.dumps(d))
    check("并发下金额无丢失",
          d["prompt_tokens"] == threads * per_thread * 10
          and d["total_tokens"] == threads * per_thread * 15, json.dumps(d))


def test_tree_stats():
    print("[4] tree_stats 口径（必须与前端 KT.bookModules / moduleStats 一致）")
    w = PR.tree_stats(WRAPPED)
    check("模块数按包装结构解包（1 → 2）", w["modules"] == 2, json.dumps(w))
    # 根 + 书 + 第一章 + 1.1 + 第二章 = 5
    check("节点总数含根节点（= 前端 moduleStats 的 Nodes）", w["total"] == 5, json.dumps(w))
    check("已增强计数", w["enhanced"] == 1)
    check("考点条数累加", w["exam_points"] == 2)
    check("最大层数", w["max_depth"] == 3, json.dumps(w))
    check("覆盖率", w["coverage_pct"] == 20.0, json.dumps(w))
    check("层级分布", w["levels"] == {"1": 1, "2": 2, "3": 1}, json.dumps(w["levels"]))

    f = PR.tree_stats(FLAT)
    check("扁平结构不解包（顶层即模块）", f["modules"] == 2, json.dumps(f))
    check("扁平结构总数（根 + 2 模块）", f["total"] == 3)

    e = PR.tree_stats({"children": []})
    check("空树按无树处理（不是 1 个知识点）",
          e["total"] == 0 and e["modules"] == 0 and e["coverage_pct"] == 0.0, json.dumps(e))
    e2 = PR.tree_stats({})
    check("空字典不炸且计 0", e2["total"] == 0)
    e3 = PR.tree_stats(None)
    check("None 不炸", e3["total"] == 0)


def test_count_guides():
    print("[5] count_guides（含「导学挂错层级」的识别）")
    flat = {"title": "R", "children": [
        {"title": "A", "guide": "GA", "children": []},
        {"title": "B", "children": []},
    ]}
    g = PR.count_guides(flat)
    check("按模块统计导学覆盖", g["modules"] == 2 and g["with_guide"] == 1, json.dumps(g))
    check("不误报包装导学", g["wrapper_guide"] is False)

    wrapped = {"title": "R", "guide": "全书导学", "children": [
        {"title": "书", "guide": "挂错层级的导学", "children": [{"title": "第一章", "children": []}]},
    ]}
    g2 = PR.count_guides(wrapped)
    check("真实模块无导学时覆盖为 0", g2["modules"] == 1 and g2["with_guide"] == 0, json.dumps(g2))
    check("识别出包装节点上挂着导学（misattachment 信号）", g2["wrapper_guide"] is True)
    check("识别全书导学", g2["book_guide"] is True)


def test_build_report():
    print("[6] build_report 组装")
    r = PR.build_report(WRAPPED, task_id="t1")
    check("最小报告只有 task_id/nodes/guides",
          sorted(r.keys()) == ["guides", "nodes", "task_id"], json.dumps(sorted(r.keys())))
    check("节点统计带上了", r["nodes"]["modules"] == 2)

    full = PR.build_report(
        WRAPPED, task_id="t2",
        files={"count": 2, "names": ["a.pdf"], "pages": 290, "pdf": 1, "word": 0, "text": 1},
        tokens=PR.UsageMeter().to_dict(),
        ai={"provider": "opencode", "model": "m", "targeted": 30, "ok": 27, "fallback": 3},
        audit={"deterministic_groups": 231, "llm_groups": 3},
        duration_sec=214.567, generated_at=1.0)
    check("活任务才有 files/tokens/ai/audit",
          all(k in full for k in ("files", "tokens", "ai", "audit", "duration_sec")))
    check("耗时保留一位小数", full["duration_sec"] == 214.6, json.dumps(full["duration_sec"]))
    check("空 dict 不写入（ai=None 时省略）",
          "ai" not in PR.build_report(WRAPPED, ai=None))

    msg = PR.summarize_for_message(full)
    check("摘要含知识点与解读数", "5 个知识点" in msg and "已解读 1 个" in msg, msg)
    check("摘要含耗时（用户口径「秒」）", "214.6 秒" in msg, msg)
    check("摘要面向用户：不出现内部流程词",
          all(w not in msg for w in ("增强阶段", "结构审校", "兜底", "OCR", "节点增强", "入 ")),
          msg)
    msg2 = PR.summarize_for_message(r)
    check("无用量的任务不硬凑 token", "token" not in msg2, msg2)


if __name__ == "__main__":
    test_meter_basic()
    test_meter_robustness()
    test_meter_concurrency()
    test_tree_stats()
    test_count_guides()
    test_build_report()
    print(f"\n{'=' * 44}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
