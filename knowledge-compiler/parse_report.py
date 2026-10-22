"""解析报告：token 用量累计 + 知识树统计 + 结果面板的数据组装。

用途：上传页在"解析完成"后不直接跳转，先展示一张数据面板
（节点规模 / 增强覆盖率 / LLM token 消耗 / 文件数与页数），用户点"预览"才离开。

统计口径 —— **只覆盖「AI 增强」阶段**（server._enhance_tree 内）：
  ① 结构审校（structure_repair）
  ② 节点增强（node_enhancer.enhance_book_nodes）
  ③ 导学生成（node_enhancer.generate_guides，含全书导学与逐章导学）
OCR（MinerU）、无结构文本的 LLM 结构化、建树期的层级校验**不计入** token ——
它们发生在增强阶段之前，且 MinerU 本身不返回 token，只返回页数级的信息。

UsageMeter 必须显式传递实例，不要用模块级全局：两个上传任务可能同时在跑，
全局会互相污染。累计过程对锁保护，因为增强阶段是并发调用。
"""

import threading


# ────────────────────────────────────────────
# token 累计
# ────────────────────────────────────────────

def _as_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class UsageMeter:
    """AI 阶段 token 用量累计器（协程/线程安全）。

    add() 在拿不到 usage 时静默返回 —— 统计绝不能拖垮主流程。
    """

    def __init__(self, scope: str = "ai_enhancing"):
        self.scope = scope
        self._lock = threading.Lock()
        self.calls = 0              # 计入统计的调用次数（仅 200 响应）
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0   # 思考模型的思考量，已含在 completion 内
        self.cached_tokens = 0
        self._models = {}

    def add(self, usage, model: str = "") -> None:
        if not isinstance(usage, dict):
            return
        p = _as_int(usage.get("prompt_tokens"))
        c = _as_int(usage.get("completion_tokens"))
        det = usage.get("completion_tokens_details")
        r = _as_int(det.get("reasoning_tokens")) if isinstance(det, dict) else 0
        pdet = usage.get("prompt_tokens_details")
        cached = _as_int(pdet.get("cached_tokens")) if isinstance(pdet, dict) else 0
        with self._lock:
            self.calls += 1
            self.prompt_tokens += p
            self.completion_tokens += c
            self.reasoning_tokens += r
            self.cached_tokens += cached
            if model:
                self._models[model] = self._models.get(model, 0) + p + c

    def from_response(self, resp, model: str = "") -> bool:
        """从 httpx 响应里取 usage 并累计。返回是否真的计到了数。"""
        before = self.calls
        try:
            self.add(resp.json().get("usage"), model)
        except Exception:
            return False
        return self.calls > before

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "scope": self.scope,
                "calls": self.calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "cached_tokens": self.cached_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "models": dict(self._models),
            }


# ────────────────────────────────────────────
# 知识树统计
# ────────────────────────────────────────────

def book_modules(tree: dict) -> list:
    """与前端 `KT.bookModules` 完全同一口径地取"模块"。

    为什么必须对齐：真实数据里 4 棵树有 3 棵是
    「Knowledge Tree → 单本书 → 17 章」的单册包装结构，前端会解包成 17 个模块。
    后端若按原始 children 数，面板会显示"模块 1"，与页面上的 17 直接矛盾。
    """
    top = tree.get("children") or []
    if len(top) == 1 and (top[0].get("children") or []):
        return top[0]["children"]
    return top


def tree_stats(tree: dict) -> dict:
    """节点规模与增强覆盖。全部从树上现算，老数据也能出数。

    total **含根节点** —— 与前端 `moduleStats(root)` 的节点数一致
    （外教树 1591 这个可对外引用的数字就是这么来的，少算根节点会变成 1590）。
    """
    # 畸形/空输入（损坏的 JSON、空字典）按"没有树"处理，而不是报 1 个知识点
    if not tree or not (tree.get("title") or tree.get("children")):
        return {"total": 0, "modules": 0, "enhanced": 0, "exam_points": 0,
                "exam_nodes": 0, "max_depth": 0, "levels": {}, "coverage_pct": 0.0}

    levels = {}
    enhanced = 0
    exam_nodes = 0
    total = 0
    max_depth = 0

    def walk(node: dict, depth: int) -> None:
        nonlocal enhanced, exam_nodes, total, max_depth
        total += 1
        if depth > max_depth:
            max_depth = depth
        lv = node.get("level")
        if isinstance(lv, int):
            levels[str(lv)] = levels.get(str(lv), 0) + 1
        if (node.get("summary") or "").strip():
            enhanced += 1
        if node.get("exam_points"):
            exam_nodes += 1
        for ch in node.get("children") or []:
            walk(ch, depth + 1)

    walk(tree, 0)          # 从根开始，含根
    modules = book_modules(tree)

    return {
        "total": total,
        "modules": len(modules),
        "enhanced": enhanced,
        "exam_points": sum(len(n.get("exam_points") or []) for n in _iter_nodes(tree)),
        "exam_nodes": exam_nodes,
        "max_depth": max_depth,
        "levels": levels,
        "coverage_pct": round(enhanced * 100.0 / total, 1) if total else 0.0,
    }


def _iter_nodes(node: dict):
    yield node
    for ch in node.get("children") or []:
        yield from _iter_nodes(ch)


def count_guides(tree: dict) -> dict:
    """导学覆盖：多少章有 guide（前端只有 guide 非空才渲染导学块）。

    wrapper_guide 单独报出来，用于识别"导学挂错层级"——
    单册包装结构下 generate_guides 遍历的是 tree['children']（只有一个包装节点），
    若如此，guide 会落在包装节点上而 17 个真模块一个都没有，前端导学块全空。
    """
    modules = book_modules(tree)
    top = tree.get("children") or []
    return {
        "modules": len(modules),
        "with_guide": sum(1 for m in modules if (m.get("guide") or "").strip()),
        "wrapper_guide": bool(len(top) == 1 and (top[0].get("guide") or "").strip()),
        "book_guide": bool((tree.get("guide") or "").strip()),
    }


# ────────────────────────────────────────────
# 报告组装
# ────────────────────────────────────────────

def structure_summary(judgements: list) -> dict:
    """聚合片段级的结构判断，供结果面板展示。

    judgements 是 score_fragment 的返回值列表（每个片段一份）。
    口径：片段数 = 处理单元数；avg/min 是结构质量分（0–1）；
    routes 是每个片段走了哪条建树路径。
    """
    if not judgements:
        return {"fragments": 0, "routes": {"regex": 0, "skeleton": 0},
                "avg_score": 0.0, "min_score": 0.0, "reasons": []}
    routes = {"regex": 0, "skeleton": 0}
    scores = []
    reasons = {}
    for j in judgements:
        if not isinstance(j, dict):
            continue
        routes[j.get("route", "regex")] = routes.get(j.get("route", "regex"), 0) + 1
        try:
            scores.append(float(j.get("score") or 0.0))
        except (TypeError, ValueError):
            pass
        for r in (j.get("reasons") or [])[:2]:
            reasons[r] = reasons.get(r, 0) + 1
    top = sorted(reasons.items(), key=lambda kv: -kv[1])[:4]
    return {
        "fragments": len(judgements),
        "routes": routes,
        "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "min_score": round(min(scores), 3) if scores else 0.0,
        "reasons": [{"text": k, "count": v} for k, v in top],
    }


def build_report(tree: dict, *, task_id: str = "", files: dict | None = None,
                 tokens: dict | None = None, ai: dict | None = None,
                 audit: dict | None = None, structure: dict | None = None,
                 skeleton: dict | None = None,
                 duration_sec: float | None = None,
                 generated_at: float | None = None) -> dict:
    """组装结果面板需要的全部数据。

    files / tokens / ai / audit / structure 都是"活任务才有"的信息；缺了就省略，
    前端按缺失降级。树统计永远现算，所以磁盘上的老任务也能出报告。
    """
    report = {
        "task_id": task_id,
        "nodes": tree_stats(tree),
        "guides": count_guides(tree),
    }
    if generated_at is not None:
        report["generated_at"] = generated_at
    if files:
        report["files"] = files
    if tokens:
        report["tokens"] = tokens
    if ai:
        report["ai"] = ai
    if audit:
        report["audit"] = audit
    if structure:
        report["structure"] = structure
    if skeleton:
        report["skeleton"] = skeleton
    if duration_sec is not None:
        report["duration_sec"] = round(float(duration_sec), 1)
    return report


def summarize_for_message(report: dict) -> str:
    """一行摘要，写进 task.messages。

    这句**会直接显示在进度浮层上给用户看**，所以用用户口径写：
    不出现"增强阶段 / 结构审校 / 兜底 / OCR"这类内部流程词，也不给"入/出"这种拆解。
    """
    n = report.get("nodes") or {}
    t = report.get("tokens") or {}
    parts = [f"共 {n.get('total', 0)} 个知识点，已解读 {n.get('enhanced', 0)} 个"
             f"（{n.get('coverage_pct', 0)}%）"]
    if t.get("total_tokens"):
        parts.append(f"本次 AI 用量 {t['total_tokens']} token")
    if report.get("duration_sec") is not None:
        parts.append(f"用时 {report['duration_sec']} 秒")
    return "解析完成：" + "，".join(parts) + "。"
