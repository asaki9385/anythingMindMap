"""
对话复习助手引擎 —— 自然对话的 AI 复习陪练（流式）。

体验层：学习者看到的一切话语（开场、回应、追问、提示、讲解答案、过渡、收尾）
        由 LLM 即兴生成并**流式逐字输出**（on_delta 回调），断线退回同口吻话术模板。
        对话里绝不出现回合数、提示次数、层级代号（L0–L4）、过关判定等内部记账；
        关键知识点允许 **加粗** 高光（前端渲染 Markdown 轻量子集）。
        学习者卡住/放弃/跳过时，及时把整理好的答案讲出来（陈述收尾），
        不是句句追问；确实还有可挖时才追问。

脚手架层（学习者不可见）：
- LLM：理解学习者说了什么（判分信号）+ 生成自然话语
- 代码：计数（真实回忆轮 / 提示 / 命中条目）、判定（C1–C4）、止损（S1–S4）、
        提示阶梯（H1–H3 不跳级、合计 ≤ 2）、索要答案给要点、答案泄漏检测。
  这些只用来决定何时换题、何时间隔复测，绝不播报给学习者。

求助轮（「想不起来 / 给点提示」）不计入止损：不算回忆轮、不进 S2/S3 计数，
只沿提示阶梯给提示；提示用尽仍求助 → 直接讲解答案并收掉该点。

模型一律走 resolve_chat()（与解析同源的 opencode 网关）。judge / sayer 可注入。
"""

import hashlib
import inspect
import json
import os
import re
import time
import uuid

import httpx
from app_settings import chat_body_extras, chat_headers, resolve_chat

# ────────────────────────────────────────────
# 常量（内部判定阈值，全部可数；不对外播报）
# ────────────────────────────────────────────

LEVELS = ["L0", "L1", "L2", "L3", "L4"]
PASS_LEVELS = {"L3", "L4"}
STUCK_LEVELS = {"L0", "L1"}
HINT_ORDER = ["H1", "H2", "H3"]
MAX_HINTS = 2          # 提示合计 ≤ 2
MAX_TURNS = 6          # 单考点真实回忆轮 ≥ 6 止损
STUCK_ROUNDS = 2       # 连续 2 轮真实回忆 L0/L1 止损
MAX_UNITS_DEFAULT = 40
# 长期学习者模型：间隔复习阶梯（天）。通过次数越多、间隔越长；
# 提示辅助过关 = 明天再来；未过关 = 立即重新到期（下一轮优先复练）。
REVIEW_STEPS_DAYS = [1, 2, 4, 7, 15, 30]
DAY_SECONDS = 24 * 3600

# 提示级别规则（写进提示词，供 LLM 生成对应内容；不直接展示）
HINT_RULE = {
    "H1": "场景锚点：用对比、反例或应用场景把注意力引向考点，不透露条目内容",
    "H2": "对立概念/挖空提示：点出相对机制、结构位置或给一个起始词，让对方自己补全",
    "H3": "锚点提示：给一个关键词或首字让对方往下接",
}

# 新协议：先输出单行诊断 JSON，再输出 SAY_SEP，最后输出自然语言回复。
# 旧协议 (对话在前 + SEP + JSON) 仍保留解析兼容。
SAY_SEP = "###SAY###"
SEP = "###JSON###"
MAX_CLARIFY = 2        # 同一考点最多连续答疑 2 次，不计真实回忆轮

ASK_FOR_ANSWER_RE = re.compile(
    r"(告诉我答案|直接说答案|给(我)?答案|答案是什么|别问了|不想了|直接讲|公布答案|看答案)")

# 求助轮识别：这些话是"求提示"，不是回忆作答，不计轮次
HELP_HINT_RE = re.compile(
    r"(想不起|想起来再说|提示我|给点提示|提示一下|来点提示|给我提示|帮帮我|教教我|"
    r"不知道.{0,6}(怎么办|从哪|下手))")

# 明确的理解性反问：优先进入临时答疑，不消耗回忆轮，也不触发“索要答案”。
CONCEPTUAL_Q_RE = re.compile(
    r"(为什么|为何|怎么理解|如何理解|啥意思|什么意思|什么关系|有什么关系|"
    r"有什么区别|区别是|有什么联系|联系是|没懂|不懂|解释一下|讲讲.{0,8}(区别|关系))")

# 整句话就是"不知道/不会/忘了…"（允许"我/也/还是"等前缀与语气词尾巴）：
# 与「想不起来了」同样按求助处理——给提示、不计轮次、不进止损计数。
# 只匹配短句，避免把真正作答里顺带出现的"不知道"误判成求助。
_HELP_ONLY_PHRASES = ("不知道", "不晓得", "不会", "不记得", "忘了", "想不起", "想不起来",
                      "没印象", "没学过", "不清楚", "不懂", "答不上来", "说不上来")


def is_conceptual_question(text: str) -> bool:
    """短句且带疑问/求解释意图时，认定为概念答疑而非回忆作答。"""
    t = re.sub(r"\s+", "", text or "")
    if not t or len(t) > 180:
        return False
    asks = bool(re.search(r"[?？]|吗$|呢$|没懂|不懂|解释一下", t))
    return asks and bool(CONCEPTUAL_Q_RE.search(t))


def is_help_only(text: str) -> bool:
    t = re.sub(r"[^\u4e00-\u9fff]", "", text or "")
    if not t or len(t) > 9:
        return False
    t = re.sub(r"^(我|也|真|都|确实|完全|一点|一点也|啥也|还是|仍然|依然|就|是|这|那)+", "", t)
    t = re.sub(r"(啊|呀|吧|呢|哦|嗯|嘛|了)+$", "", t)
    if t in _HELP_ONLY_PHRASES:
        return True
    return any((t + suf) in _HELP_ONLY_PHRASES for suf in ("了", "过", "着", "得", "起来", "上来"))

# ────────────────────────────────────────────
# 复习单元生成（知识树 → 考点清单）
# ────────────────────────────────────────────

def _norm_title(s) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _clean_point(s, max_len: int = 44) -> str:
    s = _norm_title(s)
    s = re.sub(r"^[（(【\[]?[0-9０-９一二三四五六七八九十]+[)）】\].、]?\s*", "", s)
    return s[:max_len]

def unit_id_for(path_titles: list) -> str:
    h = hashlib.md5("|".join(path_titles).encode("utf-8")).hexdigest()[:10]
    return "u_" + h

def _unit_from_node(node: dict, path: list, group: str) -> dict:
    title = _norm_title(node.get("title") or node.get("name") or "")
    kids = node.get("children") or []
    exam = node.get("exam_points") or []

    points = []
    if 2 <= len(kids) <= 8:
        points += [_clean_point(c.get("title") or c.get("name") or "") for c in kids]
    for ep in exam[:4]:
        p = _clean_point(ep.get("point") or "")
        if p and p not in points:
            points.append(p)
    if len(points) < 2:
        summary = node.get("summary") or ""
        for seg in re.split(r"[。；;!?！？\n]", summary):
            seg = _clean_point(seg, 40)
            if len(seg) >= 6 and seg not in points:
                points.append(seg)
            if len(points) >= 6:
                break
    seen, dedup = set(), []
    for p in points:
        if p and p not in seen:
            seen.add(p)
            dedup.append(p)
    points = dedup[:8]

    freqs = [ep.get("frequency") or "" for ep in exam]
    return {
        "unit_id": unit_id_for(path + [title]),
        "title": title,
        "path": path,
        "group": group,
        "summary": _norm_title(node.get("summary") or "")[:160],
        "reference_points": points,
        "exam_points": [{"point": _clean_point(ep.get("point") or "", 60),
                         "frequency": ep.get("frequency") or "",
                         "type": ep.get("type") or ""} for ep in exam],
        "has_exam": bool(exam),
        "high_freq": "高频" in freqs,
        "level": node.get("level", len(path)),
    }

def build_review_units(tree: dict, scope: dict = None, max_units: int = MAX_UNITS_DEFAULT) -> dict:
    """知识树 → 复习单元清单。

    单元 = 有 2–8 个子节点的"块"（子标题天然就是应含条目）或带考点标注的叶子。
    选中的单元会屏蔽其后代（避免父子重复考）。
    scope: {"chapter": 标题, "high_freq": bool, "unit_ids": [...]}
    返回 {"units": [...], "total_candidates": n, "truncated": m}
    """
    scope = scope or {}
    top = tree.get("children") or []
    unwrap = (len(top) == 1 and len((top[0].get("children") or [])) >= 2)

    picked = []

    def _walk(node: dict, path: list):
        title = _norm_title(node.get("title") or node.get("name") or "")
        kids = node.get("children") or []
        exam = node.get("exam_points") or []
        if title and (2 <= len(kids) <= 8 or (exam and not kids)):
            if unwrap:
                group = path[1] if len(path) >= 2 else title
            else:
                group = path[0] if path else title
            picked.append(_unit_from_node(node, path, group))
            return  # 屏蔽后代，避免父子重复考
        for c in kids:
            _walk(c, path + [title])

    if unwrap:
        container = top[0]
        base = _norm_title(container.get("title") or container.get("name") or "")
        for c in (container.get("children") or []):
            _walk(c, [base])
    else:
        for c in top:
            _walk(c, [])

    total = len(picked)

    chapter = (scope.get("chapter") or "").strip()
    if chapter:
        picked = [u for u in picked if chapter in u["path"] or chapter == u["title"]]
    if scope.get("high_freq"):
        picked = [u for u in picked if u["high_freq"] or u["has_exam"]]
    if scope.get("unit_ids"):
        order = {uid: i for i, uid in enumerate(scope["unit_ids"])}
        picked = [u for u in picked if u["unit_id"] in order]
        picked.sort(key=lambda u: order[u["unit_id"]])

    truncated = 0
    if max_units and len(picked) > max_units:
        picked.sort(key=lambda u: (0 if u["high_freq"] else 1,))
        truncated = len(picked) - max_units
        picked = picked[:max_units]

    return {"units": picked, "total_candidates": total, "truncated": truncated}


# ────────────────────────────────────────────
# 持久化（只存复习进度，不回写知识树）
# ────────────────────────────────────────────

def _file_key(file_key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.一-鿿-]+", "_", file_key or "unknown")[:80]

class ReviewStore:
    """进度与会话持久化：data/review/<file_key>/progress.json + sessions/<sid>.json"""

    def __init__(self, base_dir: str):
        self.base = base_dir
        self.sessions_dir = os.path.join(base_dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

    def _progress_path(self, file_key: str) -> str:
        d = os.path.join(self.base, _file_key(file_key))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "progress.json")

    def load_progress(self, file_key: str) -> dict:
        try:
            with open(self._progress_path(file_key), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"file_key": file_key, "units": {}, "retest_queue": [],
                    "weak_units": [], "sessions": []}

    def save_progress(self, file_key: str, data: dict) -> None:
        path = self._progress_path(file_key)
        data["updated_at"] = time.time()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception as e:
            print(f"  WARN review progress save failed: {e}")

    # ── 会话运行态（崩溃可续） ──

    def save_session(self, state: dict) -> None:
        sid = state.get("session_id")
        if not sid:
            return
        path = os.path.join(self.sessions_dir, _file_key(sid) + ".json")
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            print(f"  WARN review session save failed: {e}")

    def load_session(self, session_id: str) -> dict | None:
        path = os.path.join(self.sessions_dir, _file_key(session_id) + ".json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def record_unit_result(self, file_key: str, unit: dict, status: str,
                           best_level: str, session_id: str, reason: str = "") -> None:
        prog = self.load_progress(file_key)
        entry = prog["units"].get(unit["unit_id"], {})
        prev_best = entry.get("best_level", "L0")
        if LEVELS.index(best_level) > LEVELS.index(prev_best):
            entry["best_level"] = best_level

        # ── 长期学习者模型：通过次数 / 遗忘次数 / 下次到期（确定性间隔调度）──
        now = time.time()
        passes = int(entry.get("passes") or 0)
        lapses = int(entry.get("lapses") or 0)
        if status == "passed":
            passes += 1
            step = REVIEW_STEPS_DAYS[min(passes, len(REVIEW_STEPS_DAYS)) - 1]
            next_due = now + step * DAY_SECONDS
        elif status == "half":
            next_due = now + REVIEW_STEPS_DAYS[0] * DAY_SECONDS
        else:  # failed —— 立即重新到期，下一轮优先复练
            passes = 0
            lapses += 1
            next_due = now
        entry.update({"title": unit["title"], "path": unit["path"],
                      "last_status": status, "last_reason": reason,
                      "last_session": session_id, "last_seen": now,
                      "passes": passes, "lapses": lapses, "next_due": next_due})
        prog["units"][unit["unit_id"]] = entry

        def _drop(lst, uid):
            return [x for x in lst if x.get("unit_id") != uid]

        prog["retest_queue"] = _drop(prog.get("retest_queue", []), unit["unit_id"])
        prog["weak_units"] = _drop(prog.get("weak_units", []), unit["unit_id"])
        item = {"unit_id": unit["unit_id"], "title": unit["title"],
                "path": unit["path"], "reason": reason or status,
                "added_at": now, "session_id": session_id, "due_at": next_due}
        if status in ("passed", "half"):
            prog["retest_queue"].append(item)
        elif status == "failed":
            prog["weak_units"].append(item)
        self.save_progress(file_key, prog)

    def due_unit_ids(self, file_key: str, now: float = None) -> list:
        """需要复练的单元：薄弱点 + 复测队列，按到期时间排序（已到期在前，越早越优先）。"""
        prog = self.load_progress(file_key)
        now = time.time() if now is None else now
        entries = prog.get("units", {})
        items, seen = [], set()
        for src in ("weak_units", "retest_queue"):
            for x in prog.get(src, []):
                uid = x.get("unit_id")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                e = entries.get(uid) or {}
                due = float(e.get("next_due") or x.get("due_at") or 0)
                items.append((0 if (not due or due <= now) else 1, due, uid))
        items.sort(key=lambda t: (t[0], t[1]))
        return [t[2] for t in items]

    def due_count(self, file_key: str, now: float = None) -> int:
        """已到期的复练条目数（入口「今日待复练」）。"""
        prog = self.load_progress(file_key)
        now = time.time() if now is None else now
        entries = prog.get("units", {})
        n, seen = 0, set()
        for src in ("weak_units", "retest_queue"):
            for x in prog.get(src, []):
                uid = x.get("unit_id")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                e = entries.get(uid) or {}
                due = float(e.get("next_due") or x.get("due_at") or 0)
                if not due or due <= now:
                    n += 1
        return n

    def record_session_summary(self, file_key: str, summary: dict) -> None:
        prog = self.load_progress(file_key)
        prog.setdefault("sessions", []).append(summary)
        prog["sessions"] = prog["sessions"][-30:]
        self.save_progress(file_key, prog)


# ────────────────────────────────────────────
# 判定器（纯代码，内部脚手架）
# ────────────────────────────────────────────

def _normalise_judged(raw: dict, n_points: int) -> dict:
    """清洗 LLM 判分输出：层级、条目编号、提示级别、自然回复。

    reply 是说给学习者听的话语（允许 **加粗**）；兼容旧字段 feedback/question。
    """
    if not isinstance(raw, dict):
        raw = {}
    level = raw.get("level")
    if level not in LEVELS:
        m = re.search(r"L[0-4]", str(level or ""))
        level = m.group(0) if m else "L0"

    matched = []
    for item in raw.get("matched_points") or []:
        idx = None
        if isinstance(item, bool):
            idx = None
        elif isinstance(item, (int, float)):
            i = int(item)
            idx = i if 0 <= i < n_points else (i - 1 if 1 <= i <= n_points else None)
        elif isinstance(item, str):
            d = re.search(r"\d+", item)
            if d:
                i = int(d.group(0))
                idx = i - 1 if 1 <= i <= n_points else (i if 0 <= i < n_points else None)
        if idx is not None and idx not in matched:
            matched.append(idx)

    hint_level = raw.get("hint_level")
    if hint_level not in HINT_ORDER:
        hint_level = None

    next_action = raw.get("next_action")
    if next_action not in ("clarify", "probe", "restate", "hint"):
        next_action = "probe"

    reply = (raw.get("reply") or "").strip()
    if not reply:
        fb = _norm_title(raw.get("feedback") or "")
        q = _norm_title(raw.get("question") or "")
        reply = "\n".join(p for p in [fb, q] if p)
    reply = reply[:1000]

    def _lst(key):
        v = raw.get(key) or []
        if not isinstance(v, list):
            v = [str(v)]
        return [_norm_title(str(x))[:60] for x in v if str(x).strip()][:6]

    return {
        "level": level,
        "matched_points": sorted(matched),
        "gaps": _lst("gaps"),
        "confusions": _lst("confusions"),
        "trap_resolved": bool(raw.get("trap_resolved")),
        "asks_for_answer": bool(raw.get("asks_for_answer")),
        "reply": reply,
        "next_action": next_action,
        "hint_level": hint_level,
        "raw_fallback": bool(raw.get("_fallback", False)),
    }


def _core_of_point(p: str) -> str:
    """取条目核心（去标点数字），用于泄漏检测。"""
    core = re.sub(r"[\s，。、；：:;!?！？（）()【】\[\]“”\"'·\-—*`]+", "", p or "")
    core = re.sub(r"^[0-9０-９一二三四五六七八九十]+", "", core)
    return core

def detect_leak(outbound_text: str, points: list, hint_level) -> bool:
    """答案泄漏检测：出站文本中若实质包含某条应含条目内容，且不是 H4 → 泄漏。

    只用于"引导回忆"的话语；讲解答案（teach/reveal）豁免。
    """
    if hint_level == "H4":
        return False
    text = re.sub(r"[\s*`]", "", outbound_text or "")
    if not text:
        return False
    for p in points or []:
        core = _core_of_point(p)
        if len(core) >= 6 and core in text:
            return True
        if len(core) >= 10:
            for i in range(0, len(core) - 7):
                if core[i:i + 8] in text:
                    return True
    return False


# ────────────────────────────────────────────
# LLM 调用（流式；与解析同源 resolve_chat）
# ────────────────────────────────────────────

def _resolve_or(api_key: str, base_url: str, model: str) -> dict | None:
    key = (api_key or "").strip()
    if key:
        return {"api_key": key, "base_url": base_url, "model": model}
    try:
        return resolve_chat()
    except Exception:
        return None


def _chat_payload(url, model, system=None, user=None, temperature=0.7,
                  max_tokens=500, *, messages=None, reasoning_effort=None):
    """构造兼容 OpenAI chat-completions 的请求体。

    messages 传入时使用原生多轮数组；否则退回 system + user 两段式。
    reasoning_effort 仅由支持该参数的网关消费（见 app_settings.chat_body_extras）。
    """
    if messages is None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if user:
            messages.append({"role": "user", "content": user})
    return {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
        **chat_body_extras(url, reasoning_effort=reasoning_effort),
    }


async def _chat_stream(url: str, key: str, model: str, system: str = None,
                       user: str = None, *, temperature: float = 0.7,
                       max_tokens: int = 500, on_delta=None,
                       messages: list | None = None,
                       reasoning_effort: str | None = None) -> str | None:
    """流式 chat；网关不支持流式时自动退回整体请求。失败返回 None。"""
    payload = _chat_payload(url, model, system, user, temperature, max_tokens,
                            messages=messages, reasoning_effort=reasoning_effort)
    payload["stream"] = True
    collected = ""
    finish_reason = ""
    for _attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                async with client.stream("POST", url, json=payload,
                                         headers=chat_headers(url, key)) as resp:
                    if resp.status_code != 200:
                        # 网关可能不支持 stream 参数 → 退回非流式
                        return await _chat_once(url, key, model, system, user,
                                                temperature=temperature,
                                                max_tokens=max_tokens,
                                                on_delta=on_delta,
                                                messages=messages,
                                                reasoning_effort=reasoning_effort)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            choice = json.loads(data)["choices"][0]
                            delta = choice.get("delta", {}).get("content") or ""
                            finish_reason = choice.get("finish_reason") or finish_reason
                        except Exception:
                            continue
                        if delta:
                            collected += delta
                            if on_delta:
                                on_delta(delta)
            if not collected:
                # 有的路由会忽略 stream=true，直接返回普通 JSON；此时退回非流式。
                return await _chat_once(url, key, model, system, user,
                                        temperature=temperature,
                                        max_tokens=max_tokens,
                                        on_delta=on_delta,
                                        messages=messages,
                                        reasoning_effort=reasoning_effort)
            if finish_reason == "length":
                print(f"  WARN chat stream hit max_tokens at {max_tokens} tokens")
            return collected
        except Exception:
            continue
    if collected:
        return collected
    return await _chat_once(url, key, model, system, user,
                            temperature=temperature, max_tokens=max_tokens,
                            on_delta=on_delta, messages=messages,
                            reasoning_effort=reasoning_effort)


async def _chat_once(url: str, key: str, model: str, system: str = None,
                     user: str = None, *, temperature: float = 0.7,
                     max_tokens: int = 500, on_delta=None,
                     messages: list | None = None,
                     reasoning_effort: str | None = None) -> str | None:
    payload = _chat_payload(url, model, system, user, temperature, max_tokens,
                            messages=messages, reasoning_effort=reasoning_effort)
    for _ in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(url, json=payload, headers=chat_headers(url, key))
            if resp.status_code != 200:
                continue
            text = resp.json()["choices"][0]["message"]["content"] or ""
            if text and on_delta:
                on_delta(text)
            return text or None
        except Exception:
            continue
    return None


def _sep_filter(on_delta, sep: str = SEP):
    """旧协议过滤器：把流式文本截成"分隔符之前的对话段"。

    保留用于兼容旧模型输出与既有测试；新追问协议使用 _say_after_filter。
    """
    if not on_delta:
        return None
    st = {"buf": "", "sent": 0, "done": False}

    def push(chunk: str):
        if st["done"] or not chunk:
            return
        st["buf"] += chunk
        i = st["buf"].find(sep)
        if i >= 0:
            visible, st["done"] = st["buf"][:i], True
        else:
            keep = len(sep) - 1
            visible = st["buf"][:-keep] if len(st["buf"]) > keep else ""
        if len(visible) > st["sent"]:
            on_delta(visible[st["sent"]:])
            st["sent"] = len(visible)
    return push


def _say_after_filter(on_delta, sep: str = SAY_SEP):
    """新协议过滤器：诊断 JSON 段先静默缓存，遇到 SAY_SEP 后才流式转发回复。"""
    if not on_delta:
        return None
    st = {"buf": "", "sent": 0, "started": False}

    def push(chunk: str):
        if not chunk:
            return
        st["buf"] += chunk
        if not st["started"]:
            i = st["buf"].find(sep)
            if i < 0:
                return
            st["started"] = True
            st["buf"] = st["buf"][i + len(sep):]
            st["sent"] = 0
        visible = st["buf"][st["sent"]:]
        if visible:
            on_delta(visible)
            st["sent"] = len(st["buf"])
    return push


# ── 判分（对话段 + ###JSON### + 判分 JSON） ──

def normalise_chat_prefs(prefs) -> dict:
    """统一学习 / 追问两种模式共用的个性化偏好。"""
    prefs = prefs if isinstance(prefs, dict) else {}
    reply_style = prefs.get("replyStyle", prefs.get("reply_style", "auto"))
    thinking_effort = prefs.get("thinkingEffort", prefs.get("thinking_effort", "standard"))
    citation_style = prefs.get("citationStyle", prefs.get("citation_style", "auto"))
    language = prefs.get("language", "zh")
    custom = prefs.get("customInstruction", prefs.get("custom_instruction", ""))
    return {
        "replyStyle": reply_style if reply_style in {"concise", "auto", "detailed"} else "auto",
        "thinkingEffort": thinking_effort if thinking_effort in {"fast", "standard", "deep"} else "standard",
        "citationStyle": citation_style if citation_style in {"minimal", "auto", "strong"} else "auto",
        "language": language if language in {"zh", "bilingual"} else "zh",
        "customInstruction": str(custom or "").strip()[:300],
    }


def _probe_preference_text(preferences=None) -> str:
    p = normalise_chat_prefs(preferences)
    style = {
        "concise": "回复尽量精炼，优先一句点中要害。",
        "auto": "回复长度随情境调整。",
        "detailed": "需要讲清时可以把关键逻辑说透，但仍保持对话感。",
    }[p["replyStyle"]]
    lang = ("回答语言：中文为主，关键术语后括注英文。"
            if p["language"] == "bilingual" else "回答语言：使用中文术语与自然表达。")
    lines = ["【个性化表达偏好（不覆盖判分、防泄漏与输出格式）】", style, lang]
    if p["customInstruction"]:
        lines.append("自定义表达偏好：" + p["customInstruction"])
    return "\n".join(lines)


def _normalise_history(history) -> list:
    """把持久消息清洗成 OpenAI 原生 user / assistant 交替数组。"""
    out = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role in ("assistant", "guide"):
            role = "assistant"
        elif role in ("user", "learner"):
            role = "user"
        else:
            continue
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    return out


JUDGE_SYSTEM = """你是复习陪练，和学习者进行连续的一对一对话。你必须先完成内部诊断，再开口说话。

输出格式（严格遵守，不要 Markdown 代码围栏，不要额外解释）：
第一行只输出一个紧凑 JSON：
{"level":"L0空白|L1零星关键词|L2有点无框架|L3完整骨架|L4能迁移","matched_points":[命中条目编号，从0起],"gaps":["没说到的"],"confusions":["易混点"],"trap_resolved":false,"asks_for_answer":false,"next_action":"clarify|probe|restate|hint","hint_level":"H1|H2|H3|null"}
第二行只输出 ###SAY###
第三段才是说给学习者听的自然语言。

诊断与话语规则：
- 先根据学习者最新回答和前面的对话，判断命中、缺口、混淆，再决定下一句话；不要先安慰再临时找缺口。
- 不整段背要点原文；不提回合/提示次数/层级代号/过关判定。
- level 为 L0/L1 且 next_action=hint 时，回复必须给出当前允许的提示，并将 hint_level 填成该级别。
- 如果学习者是在追问概念、问“为什么/怎么理解/二者关系”，而不是在回忆作答：next_action 填 clarify。先讲清 2–4 句，再自然邀请他回到原问题；不要给出应含条目的完整答案，不要扣回忆轮次。
- 如果学习者卡住、放弃、索要答案，或确实没有更多可挖：直接讲清框架并收尾，不强行追问。
- 如果还有可挖但暂不需要提示：一次只问一个真正能推进思考的问题。
- {hint_line}"""


def build_judge_prompt(unit: dict, state: dict, answer: str = "",
                       is_restate: bool = False, preferences=None) -> str:
    """构造本轮考点与状态上下文；对话原文另走原生 messages 历史。"""
    points = "\n".join(f"{i}. {p}" for i, p in enumerate(unit.get("reference_points") or []))
    if state.get("hint_count", 0) < MAX_HINTS:
        allowed = HINT_ORDER[state.get("hint_count", 0)]
        hint_line = f"本轮最多只能给 {allowed}（{HINT_RULE[allowed]}）。"
    else:
        hint_line = "提示已给过两次，不要再给提示。"
    clarify_line = (
        f"本考点已答疑 {state.get('clarify_count', 0)} 次，最多 {MAX_CLARIFY} 次。"
        if state.get("clarify_count", 0) else ""
    )
    body = f"""【本轮考点】{unit.get('title')}
应含条目（编号从 0 起）：
{points or '（无固定条目）'}
状态（勿提及）：第 {state.get('turn_count', 0)} 轮，已提示 {state.get('hint_count', 0)} 次；
上次缺口：{'、'.join(state.get('last_gaps') or []) or '无'}；易混：{'、'.join(state.get('trap_list') or []) or '无'}；
{hint_line}{clarify_line}
{'本轮是完整重述轮，按完整骨架校验。' if is_restate else ''}
{_probe_preference_text(preferences)}"""
    if answer:
        body += "\n学习者最新回答：\n" + answer
    return body


def build_judge_messages(unit: dict, state: dict, answer: str,
                         is_restate: bool = False, history=None,
                         preferences=None) -> list:
    """原生多轮判分消息：system（规则+考点）+ 真实历史 + 最新作答。"""
    system = JUDGE_SYSTEM.replace(
        "{hint_line}",
        (f"本轮要提示时只能用 {HINT_ORDER[state.get('hint_count', 0)]}。"
         if state.get("hint_count", 0) < MAX_HINTS else "本次不能再给提示。"))
    system += "\n\n" + build_judge_prompt(unit, state, "", is_restate, preferences)
    msgs = [{"role": "system", "content": system}]
    msgs.extend(_normalise_history(history)[-10:])
    reminder = ("\n\n【本轮输出协议（硬约束）】先输出一行诊断 JSON，换行输出 ###SAY###，"
                "之后才输出对学习者说的话。不要省略 JSON，也不要先说话。")
    msgs.append({"role": "user", "content": (answer or "").strip() + reminder})
    return msgs


def _split_judge_output(raw_text: str) -> tuple[dict | None, str]:
    """解析新旧两种协议，返回 (诊断 JSON, 给学习者的话)。"""
    raw_text = raw_text or ""
    head, reply = raw_text, ""
    if SAY_SEP in raw_text:
        head, reply = raw_text.split(SAY_SEP, 1)
    elif SEP in raw_text:
        reply, head = raw_text.split(SEP, 1)
    elif raw_text.lstrip().startswith(("{", "```")):
        end = raw_text.rfind("}")
        if end >= 0:
            head, reply = raw_text[:end + 1], raw_text[end + 1:]
    return _extract_json(head), reply.strip()


async def llm_judge(unit: dict, state: dict, answer: str,
                    is_restate: bool = False, *,
                    api_key: str = "", base_url: str = "", model: str = "",
                    history=None, preferences=None,
                    on_delta=None) -> dict:
    """默认判分（流式）。模型先输出诊断 JSON，再流式输出自然回复。"""
    cfg = _resolve_or(api_key, base_url, model)
    n = len(unit.get("reference_points") or [])
    if not cfg or not cfg.get("api_key"):
        return _deterministic_judge(unit, state, answer)
    prefs = normalise_chat_prefs(preferences)
    effort = "medium" if prefs.get("thinkingEffort") == "deep" else "none"
    # 深入档的隐藏推理也会占用输出预算；为可见回复预留足够空间。
    judge_tokens = 4000 if prefs.get("thinkingEffort") == "deep" else 1100
    judge_temp = 0.6 if prefs.get("thinkingEffort") == "deep" else 0.35
    raw_text = await _chat_stream(
        cfg.get("base_url") or "", cfg["api_key"], cfg.get("model") or "",
        messages=build_judge_messages(unit, state, answer, is_restate,
                                      history=history, preferences=prefs),
        temperature=judge_temp, max_tokens=judge_tokens, reasoning_effort=effort,
        on_delta=_say_after_filter(on_delta))
    if raw_text:
        try:
            raw, reply_part = _split_judge_output(raw_text)
            if raw is not None:
                raw["reply"] = reply_part
                return _normalise_judged(raw, n)
            # 没有 JSON：保留模型话语，判定信号走确定性兜底
            det = _deterministic_judge(unit, state, answer)
            det["reply"] = (reply_part or raw_text).strip()[:1000]
            return det
        except Exception:
            pass
    return _deterministic_judge(unit, state, answer)


def _extract_json(text: str) -> dict | None:
    t = re.sub(r"^```(?:json)?\s*", "", (text or "").strip())
    t = re.sub(r"\s*```$", "", t)
    start = t.find("{")
    if start < 0:
        return None
    try:
        return json.loads(t[start:t.rfind("}") + 1])
    except Exception:
        return None


def _deterministic_judge(unit: dict, state: dict, answer: str) -> dict:
    """离线兜底判分：条目 4-gram 匹配估算命中；话语自然简短，不报数字。"""
    points = unit.get("reference_points") or []
    text = re.sub(r"[\s*`]", "", answer or "")
    matched = []
    for i, p in enumerate(points):
        core = _core_of_point(p)
        if not core:
            continue
        if len(core) <= 4:
            hit = core in text
        else:
            hit = any(core[j:j + 4] in text for j in range(len(core) - 3))
        if hit:
            matched.append(i)
    n = len(points)
    if len(text) < 8:
        level = "L0"
    elif not matched:
        level = "L1"
    elif n and len(matched) >= n:
        level = "L3"
    else:
        level = "L2"
    return _normalise_judged({
        "level": level,
        "matched_points": matched,
        "gaps": [f"第 {i + 1} 条" for i in range(n) if i not in matched][:4],
        "reply": "先想到哪说到哪，缺的待会儿我帮你补上。",
        "next_action": "restate" if (n and len(matched) >= n) else "probe",
        "_fallback": True,
    }, n)


# ── llm_say：代码驱动话语（开场 / 提示 / 讲解答案 / 过渡 / 收尾 / 复述邀请） ──

SAY_SYSTEM = """你是复习陪练，说话像真人老师：自然、口语化、有来有回，通常 1–3 句，必要时可稍展开。不报内部进度，不提层级代号，不机械加粗关键词。只输出要说的话本身。"""


def _say_prompt(kind: str, ctx: dict) -> str | None:
    title = ctx.get("title") or ""
    if kind == "open":
        pts = "、".join(ctx.get("points") or [])
        where = " > ".join(ctx.get("path") or [])
        return (f"开场复习「{title}」（{where}）。它包含的内容块：{pts or '（按总结判断）'}。"
                f"总结：{ctx.get('summary') or '无'}。"
                f"写 1–3 句开场白：用一句话点出这个主题讲什么、包含哪几块（可以点出小节名，但不解释内容），"
                f"再自然邀请对方凭记忆展开。{ctx.get('exam_tip') or ''}")
    if kind == "hint":
        level = ctx.get("level") or "H1"
        return (f"对方在「{title}」上求助。给一个{HINT_RULE.get(level, '提示')}，"
                f"融进对话，绝不说出应含条目内容。"
                f"优先用具体场景、对比、反例或挖空来激活记忆；不要说'多想想''先搭框架'这类空话，"
                f"也不要让他翻书、抄标题或看目录。")
    if kind == "explain":
        pts = "\n".join(f"- {p}" for p in ctx.get("points") or [])
        return (f"对方在「{title}」上卡住/放过了，现在直接讲答案。参考要点：\n{pts}\n"
                f"总结：{ctx.get('summary') or '无'}。对方刚才的情况：{ctx.get('note') or '没说上来'}。"
                f"把这个考点的答案整理讲清楚：分点、关键词 **加粗**，一两句点出他容易漏的地方，陈述收尾，不追问。")
    if kind == "transition":
        flavor = ctx.get("flavor") or ""
        return (f"「{ctx.get('prev', '')}」聊完（{flavor}）。自然衔接到下一个「{title}」，"
                f"可以顺口邀请对方说说，不必每句都问。")
    if kind == "restate":
        return (f"对方在引导下已把「{title}」说得比较全。请他完整重说一遍：脱离提示、用自己的话。")
    if kind == "clarify":
        pts = "；".join(ctx.get("points") or [])
        return (f"学习者围绕「{title}」提出了理解性问题：{ctx.get('question') or ''}\n"
                f"当前资料内容块：{pts or '（按总结判断）'}；摘要：{ctx.get('summary') or '无'}。\n"
                f"用 2–4 句话把这个具体疑问讲透：可以先用类比或场景，再给出因果/区别；"
                f"不要整段罗列应含条目，不要评价他答得对不对，也不要用考问替代解释。"
                f"最后用一句自然的话把他带回原来的问题。")
    if kind == "wrap":
        shaky = ctx.get("shaky") or ""
        extra = f"还没稳的：{shaky}。" if shaky else ""
        return f"这轮收尾。{extra}像老师一样自然收个尾，说说下次怎么练，不报数字。"
    return None


async def llm_say(kind: str, ctx: dict, *, api_key: str = "",
                  base_url: str = "", model: str = "", preferences=None,
                  on_delta=None) -> str | None:
    """生成一段自然话语（流式）；失败返回 None（调用方退回话术模板）。"""
    cfg = _resolve_or(api_key, base_url, model)
    if not cfg or not cfg.get("api_key"):
        return None
    user = _say_prompt(kind, ctx)
    if not user:
        return None
    prefs = normalise_chat_prefs(preferences)
    system = SAY_SYSTEM + "\n\n" + _probe_preference_text(prefs)
    limit = 700 if kind == "explain" else (520 if kind == "clarify" else 360)
    text = await _chat_stream(cfg.get("base_url") or "", cfg["api_key"],
                              cfg.get("model") or "", system, user,
                              temperature=0.7, max_tokens=limit,
                              reasoning_effort="low" if prefs.get("thinkingEffort") == "deep" else None,
                              on_delta=on_delta)
    if not text:
        return None
    text = text.strip().strip('"').strip("「」")
    return text[:limit] or None


# ── 话术模板（LLM 不可用时的自然回退） ──

def _md_points(unit: dict) -> str:
    pts = unit.get("reference_points") or []
    return "\n".join(f"{i + 1}. **{p}**" for i, p in enumerate(pts))

def _opening_question(unit: dict) -> str:
    types = {e.get("type") for e in (unit.get("exam_points") or []) if e.get("type")}
    tip = "这可是名词解释的常客。" if "名词解释" in types else (
        "这道题爱考简答论述。" if types & {"简答", "论述"} else "")
    return f"先不翻书——说说「{unit['title']}」，现在能想起什么就说什么，碎片也算。{tip}"

def _restate_question(unit: dict) -> str:
    return f"你基本说全了。这回不看提示，把「{unit['title']}」完整重说一遍，我听听顺不顺。"

def _hint_fallback(level: str) -> str:
    return {
        "H1": "先想一个具体场景：如果条件反过来，结论还成立吗？从这个对比往考点上靠。",
        "H2": "你刚才说的是正向过程，那与之相对的机制或结果叫什么？顺着这个对照往下补。",
        "H3": "给你一个起始词，接着往下说。",
    }.get(level, "换个角度想想？")

def _reveal_text(unit: dict) -> str:
    return f"行，这次我把要点给你，你顺着过一遍：\n{_md_points(unit)}"

def _teach_fallback(unit: dict) -> str:
    """讲解答案的离线回退：组织好的答案 + 陈述收尾（不追问）。"""
    return (f"这个点我直接说，你记框架就行：\n**{unit['title']}**\n{_md_points(unit)}\n"
            f"先把这几块过一遍，回头我再来考你。")

def _safe_probe() -> str:
    return "先不急着下结论——你自己再顺一遍，还缺的是哪一块？"

def _wrap_fallback(shaky_titles: list) -> str:
    if shaky_titles:
        return "今天先到这儿。「" + "」「".join(shaky_titles[:2]) + "」还不太稳，过两天咱们再过一遍。"
    return "今天先到这儿，感觉还行。过两天我再来找你聊几个点。"

def _transition_fallback(prev: str, nxt: str) -> str:
    return f"好，「{prev}」先到这儿。换下一个——「{nxt}」。"


# ────────────────────────────────────────────
# 学习模式（自由问答 · 原文接地 · 出处由系统附上）
#
# 与追问模式的边界：追问模式 = AI 出题、学习者回忆、代码判定；
# 学习模式 = 学习者出题、AI 依据原文讲解，不判分、不记账。
# 出处不交给模型生成，而是由检索层在答案末尾确定性附上（防编造）。
# ────────────────────────────────────────────

LEARN_SYSTEM = """你在给考研学生做辅导，像一位有经验、会顺着学生思路讲下去的老师。
只依据用户给出的【资料】回答：资料里明确写到的概念、人物、观点，要依据资料讲清楚；绝不编造具体事实或出处。
先判断问题意图，再组织语言：
- 问“是什么/有哪些/包括什么”时，用清晰的结构化要点作答；
- 问“为什么/怎么理解/有什么区别/有什么联系”时，先用 1–2 句直觉、类比或场景破题，再沿着因果链展开，少用冷冰冰的列表；
- 需要推导时，可以说明“基于资料可以推出”的逻辑桥梁，但不要把合理推导说成资料原文。
如果确实超出当前资料范围，要明确说“这部分当前资料没有展开”，并可以自然提示学习者可开启【知识补全】做学科背景延展；不要因为资料没直接写就机械终止对话。
讲完一个核心或易错概念后，如果上下文合适，可以在结尾顺势抛出一个简短的思考题或应用场景（微追问），引导对话继续；不要每轮都强行提问。
不要输出“参考、出处、来源”之类的行——出处由系统自动附上。"""

LEARN_SYSTEM_COMPLETE = """你在给考研学生做辅导，像一位有经验、会顺着学生思路讲下去的老师。
优先依据用户给出的【资料】回答，资料里明确写到的概念、人物、观点要依据资料讲清楚。
按问题意图组织表达：事实梳理类用清晰要点；“为什么/怎么理解/区别/联系”类先用直觉、类比或场景破题，再展开逻辑链，避免只罗列短句。
资料没有直接覆盖、但学习者确实需要的内容，可以用你已有的通用学科知识补全；补全时必须区分：
1. “资料依据”：资料中明确写过、可由资料支撑的内容；
2. “AI 补充”：资料未覆盖、基于通用知识补充的内容。补充内容要单独用“【AI 补充】”标注。
不能把 AI 补充内容说成资料原文；不得为补充内容编造资料出处。对不确定的信息使用“通常/可能”等措辞。
讲完一个核心或易错概念后，如果上下文合适，可在结尾提出一个简短的思考题或应用场景，但不要每轮都追问。
不要输出“参考、出处、来源”之类的行——书内出处由系统自动附上。"""


BOOK_SCOPE_RE = re.compile(
    r"(这本书|本书|全书|整本书|整本资料|这份资料|该资料|整书|"
    r"整体框架|总体框架|知识框架|全书目录|书的结构|书的目录)")


def is_book_scope_question(question: str) -> bool:
    """判断问题是否在问整本书，而不是当前章节或单个知识点。"""
    q = re.sub(r"\s+", "", question or "")
    return bool(q and BOOK_SCOPE_RE.search(q))


_QUERY_STOP = {
    "什么", "怎么", "如何", "为什", "为什么", "哪些", "哪个", "一个", "一下",
    "这个", "那个", "意思", "请问", "帮我", "讲讲", "解释", "说说", "告诉",
    "知道", "对比", "比较", "区别", "联系", "内容", "主要", "包括", "以及",
    "它们", "他是", "她的", "应该", "可以", "需要", "简单", "详细",
}


_QUERY_FILLER = set("的了呢吗吧啊是什")


def _query_terms(question: str, limit: int = 24) -> list:
    """把问题拆成 2–4 字 n-gram 关键词（去停用词/虚词；长词优先，作为近义权重）。"""
    q = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", question or "")
    grams = set()
    for size in (2, 3, 4):
        for i in range(0, len(q) - size + 1):
            g = q[i:i + size]
            if g in _QUERY_STOP or all(ch in _QUERY_FILLER for ch in g):
                continue
            grams.add(g)
    return sorted(grams, key=lambda g: (-len(g), g))[:limit]


def _iter_nodes(tree: dict):
    """深度优先遍历，产出 (完整路径, 节点)。"""
    top = tree.get("children") or []
    stack = [(c, []) for c in reversed(top)]
    while stack:
        node, path = stack.pop()
        title = _norm_title(node.get("title") or node.get("name") or "")
        full = path + [title]
        yield full, node
        for c in reversed(node.get("children") or []):
            stack.append((c, full))


def _score_node(node: dict, terms: list) -> int:
    title = _norm_title(node.get("title") or node.get("name") or "")
    kws = node.get("keywords")
    kws = " ".join(str(k) for k in kws) if isinstance(kws, list) else str(kws or "")
    body = _norm_title(node.get("summary") or "") + " " + kws + " " + (node.get("content") or "")
    score = 0
    for g in terms:
        if g in title:
            score += len(g) * 4
        elif g in body:
            score += len(g)
    return score


def find_anchor_nodes(tree: dict, chapter: str = "", question: str = "",
                      limit: int = 3) -> list:
    """定位回答锚点：优先章节标题匹配，其次按问题关键词在全树打分。返回 [(path, node)]。"""
    nodes = list(_iter_nodes(tree))
    ch = _norm_title(chapter)
    if ch:
        exact = [(p, n) for p, n in nodes
                 if _norm_title(n.get("title") or n.get("name") or "") == ch]
        if exact:
            return exact[:1]
        sub = [(p, n) for p, n in nodes
               if ch in _norm_title(n.get("title") or n.get("name") or "")
               or _norm_title(n.get("title") or n.get("name") or "") in ch]
        if sub:
            sub.sort(key=lambda t: len(_norm_title(t[1].get("title") or "")))
            return sub[:1]
    terms = _query_terms(question)
    if terms:
        scored = [(_score_node(n, terms), p, n) for p, n in nodes]
        scored.sort(key=lambda t: -t[0])
        hits = [(p, n) for sc, p, n in scored if sc > 0][:max(limit * 3, limit)]
        best = []
        for p, n in hits:
            # 两个方向都去重：不再选已入选节点的后代，也不再选已入选节点的祖先
            # （更具体的节点得分通常更高；父级语境由上下文组装时补一块即可）
            overlap = any(p[:len(kp)] == kp or kp[:len(p)] == p for kp, _ in best)
            if overlap:
                continue
            best.append((p, n))
            if len(best) >= limit:
                break
        if best:
            return best
    return []


def _find_by_path(tree: dict, path: list):
    """按标题路径查节点（用于取锚点父级作语境）。"""
    cur_children = tree.get("children") or []
    node = None
    for seg in path:
        node = None
        for c in cur_children:
            if _norm_title(c.get("title") or c.get("name") or "") == seg:
                node = c
                break
        if node is None:
            return None
        cur_children = node.get("children") or []
    return node


def _node_block(node: dict, path: list, max_content: int = 500) -> str:
    parts = ["——" + " › ".join(path) + "——"]
    summary = _norm_title(node.get("summary") or "")
    if summary:
        parts.append("摘要：" + summary[:200])
    kws = node.get("keywords")
    if isinstance(kws, list) and kws:
        parts.append("关键词：" + "、".join(str(k) for k in kws[:8]))
    ex = node.get("exam_points") or []
    pts = [str(e.get("point")) + ("（" + str(e.get("frequency")) + "）" if e.get("frequency") else "")
           for e in ex[:4] if e.get("point")]
    if pts:
        parts.append("考点：" + "；".join(pts))
    content = (node.get("content") or "").strip()
    if content:
        parts.append(content[:max_content])
    return "\n".join(parts)


def _has_detail(node: dict) -> bool:
    return bool(node.get("summary") or node.get("content")
                or node.get("exam_points") or node.get("keywords"))


def _node_related_text(node: dict) -> str:
    kws = node.get("keywords")
    if isinstance(kws, list):
        kws = " ".join(str(k) for k in kws)
    return " ".join(str(x or "") for x in (
        node.get("title") or node.get("name") or "", node.get("summary") or "",
        kws or "", node.get("guide") or ""))


def _path_overlaps(path: list, anchors: list) -> bool:
    for ap, _ in anchors:
        if path[:len(ap)] == ap or ap[:len(path)] == path:
            return True
    return False


def _related_nodes_for(tree: dict, anchors: list, question: str,
                       limit: int = 2) -> list:
    """补充兄弟节点之外的跨章节关联：显式 related_* 优先，再做轻量词面召回。"""
    if not anchors:
        return []
    nodes = list(_iter_nodes(tree))
    id_map = {n.get("id"): (p, n) for p, n in nodes if n.get("id")}
    chosen, seen = [], set()

    def _add(p, n):
        key = tuple(p)
        if not n or key in seen or _path_overlaps(p, anchors):
            return
        seen.add(key)
        chosen.append((p, n))

    for p, n in anchors:
        for rid in (n.get("related_node_ids") or [])[:4]:
            if rid in id_map:
                _add(*id_map[rid])
        for title in (n.get("related_titles") or [])[:4]:
            for p2, n2 in nodes:
                if _norm_title(n2.get("title") or n2.get("name") or "") == _norm_title(title):
                    _add(p2, n2)
                    break

    anchor_chapters = {p[0] for p, _ in anchors if len(p) > 1}
    seed_text = question + " " + " ".join(_node_related_text(n) for _, n in anchors)
    terms = _query_terms(seed_text, limit=30)
    if terms:
        scored = []
        for p, n in nodes:
            if not _has_detail(n) or _path_overlaps(p, anchors):
                continue
            if len(p) > 1 and p[0] in anchor_chapters:
                continue
            sc = _score_node(n, terms)
            if sc >= 4:
                scored.append((sc, p, n))
        scored.sort(key=lambda x: -x[0])
        for _sc, p, n in scored:
            _add(p, n)
            if len(chosen) >= limit:
                break
    return chosen[:limit]


def _build_book_overview_context(tree: dict, title: str = "",
                                     max_chars: int = 7000) -> dict:
    """整本书问答：用根导学 + 全部顶层章节 + 章节摘要组装总览上下文。"""
    kids = tree.get("children") or []
    book = tree
    base = []
    if len(kids) == 1:
        only = kids[0]
        grandchildren = only.get("children") or []
        if grandchildren and (len(grandchildren) >= 2
                              or any((g.get("children") or []) for g in grandchildren)):
            book = only
            base = [_norm_title(only.get("title") or only.get("name") or "")]
    chapters, seen_titles = [], set()
    for c in (book.get("children") or []):
        ctitle = _norm_title(c.get("title") or c.get("name") or "")
        if ctitle and ctitle not in seen_titles:
            seen_titles.add(ctitle)
            chapters.append((base + [ctitle], c))

    book_title = _norm_title(tree.get("title") or "") or (
        _norm_title(book.get("title") or book.get("name") or "") or title)
    intro = (_norm_title(book.get("summary") or "")
             or _norm_title(book.get("guide") or "")
             or _norm_title(tree.get("summary") or "")
             or _norm_title(tree.get("guide") or ""))

    lines = ["《" + (book_title or "资料") + "》",
             "【检索范围】整本书（当前浏览章节不限制本次检索）",
             "【全书导学】",
             intro[:1200] if intro else "（知识树未提供全书导学，以下按顶层章节结构组织。）",
             "【章节结构】"]
    for p, n in chapters[:24]:
        ctitle = p[-1]
        lines.append("· " + ctitle)
        secs, seen_secs = [], set()
        for c in (n.get("children") or []):
            stitle = _norm_title(c.get("title") or c.get("name") or "")
            if stitle and stitle not in seen_secs:
                seen_secs.add(stitle)
                secs.append((stitle, c))
        for stitle, _sec in secs[:8]:
            lines.append("  · " + stitle)
        if len(secs) > 8:
            lines.append(f"  …另有 {len(secs) - 8} 个小节")

    text = "\n".join(lines) + "\n【章节摘要】\n"
    blocks = []
    if intro:
        blocks.append("——全书导学——\n" + intro[:1200])
    for p, n in chapters[:24]:
        if not _has_detail(n):
            continue
        block = _node_block(n, p, max_content=420)
        if block and block not in blocks:
            blocks.append(block)

    budget = max(0, max_chars - len(text))
    picked = []
    for block in blocks:
        if len(block) > budget:
            if budget > 120:
                picked.append(block[:budget])
            break
        picked.append(block)
        budget -= len(block) + 2

    # 章节较多的书优先让更多章节标题进入出处，而不是只留下前几章。
    sources = []
    for p, _ in chapters:
        path = " › ".join(p)
        if path and path not in sources:
            sources.append(path)
    text += "\n\n".join(picked)
    return {"text": text[:max_chars], "sources": sources[:6],
            "related_sources": [], "scope": "book"}


def build_learn_context(tree: dict, chapter: str = "", question: str = "",
                        max_chars: int = 7000) -> dict:
    """为学习模式组装接地上下文：结构纲要 + 相关节点 + 相邻/跨章关联。

    返回 {"text": …, "sources": [...], "related_sources": [...]}；text 不超过 max_chars。
    """
    title = _norm_title(tree.get("title") or "")
    kids = tree.get("children") or []
    if len(kids) == 1:   # 单册包装（根 → 一本书）取真实的书名
        title = _norm_title(kids[0].get("title") or kids[0].get("name") or "") or title
    if is_book_scope_question(question):
        # “这本书讲了什么”不能被当前浏览章节劫持；直接给全书导学与章节结构。
        return _build_book_overview_context(tree, title=title, max_chars=max_chars)

    anchors = find_anchor_nodes(tree, chapter, question)
    outline_lines, blocks, sources = [], [], []
    related = _related_nodes_for(tree, anchors, question)

    if anchors:
        if _norm_title(chapter):
            # 章节模式：给整章结构（BFS 到近处）+ 逐节点内容
            context_nodes = [anchors[0]]
            queue = [anchors[0]]
            base = len(anchors[0][0]) - 1
            while queue:
                p, n = queue.pop(0)
                for c in (n.get("children") or []):
                    cp = p + [_norm_title(c.get("title") or c.get("name") or "")]
                    context_nodes.append((cp, c))
                    queue.append((cp, c))
            outline_lines = ["  " * min(max(0, len(p) - 1 - base), 3) + "· " + p[-1]
                             for p, _ in context_nodes[:60]]
            for p, n in related:
                outline_lines.append("↳ 跨章关联：" + " › ".join(p))
            # 让关联内容紧跟章节锚点，避免被大章节的节点预算挤掉
            context_nodes = context_nodes[:1] + related + context_nodes[1:]
        else:
            # 问题定位：锚点 + 父级语境 + 直接子节点 + 相邻兄弟（便于对比辨析）
            context_nodes, seen = [], set()
            def _push(pth, nd):
                key = tuple(pth)
                if key in seen or nd is None:
                    return
                seen.add(key)
                context_nodes.append((pth, nd))
            for p, n in anchors:
                if len(p) > 1:
                    _push(p[:-1], _find_by_path(tree, p[:-1]))
                _push(p, n)
                for c in (n.get("children") or []):
                    _push(p + [_norm_title(c.get("title") or c.get("name") or "")], c)
                parent = _find_by_path(tree, p[:-1]) if len(p) > 1 else tree
                # 同名章节可能让按标题路径回溯到错误分支并返回 None；
                # 此时跳过兄弟扩展，不能让检索层直接 500。
                for sib in ((parent or {}).get("children") or [])[:8]:
                    stitle = _norm_title(sib.get("title") or sib.get("name") or "")
                    if stitle != _norm_title(n.get("title") or n.get("name") or ""):
                        _push(p[:-1] + [stitle], sib)
            for p, n in related:
                _push(p, n)
            outline_lines = ["· " + " › ".join(p) for p, _ in anchors][:6]
            if related:
                outline_lines += ["↳ 跨章关联：" + " › ".join(p) for p, _ in related]
        for p, n in context_nodes:
            if len(blocks) >= 18:
                break
            if not _has_detail(n):
                continue
            blocks.append(_node_block(n, p))
            sources.append(" › ".join(p))
    else:
        # 无锚点：退化为模块级概览（标题 + 导学）
        for p, n in list(_iter_nodes(tree))[:80]:
            if len(p) <= 2:
                g = (n.get("guide") or "").strip()
                outline_lines.append("  " * min(len(p), 3) + "· " + p[-1]
                                     + ("——" + g[:60] if g else ""))

    head = "《" + (title or "资料") + "》"
    if anchors:
        head += (" · 当前章节：" if _norm_title(chapter) else " · 相关内容：") + " › ".join(anchors[0][0])
    text = head + "\n【结构】\n" + ("\n".join(outline_lines) or "（略）") + "\n【节点内容】\n"
    budget = max(0, max_chars - len(text))
    picked = []
    for b in blocks:
        if len(b) > budget:
            if budget > 120:
                picked.append(b[:budget])
            break
        picked.append(b)
        budget -= len(b) + 2
    text += "\n\n".join(picked)
    related_paths = [" › ".join(p) for p, _ in related]
    return {"text": text[:max_chars], "sources": sources[:6],
            "related_sources": related_paths[:4]}


def _learn_preference_text(reply_style: str, thinking_effort: str,
                           citation_style: str, language: str,
                           custom_instruction: str = "") -> str:
    reply_style = reply_style if reply_style in {"concise", "auto", "detailed"} else "auto"
    thinking_effort = thinking_effort if thinking_effort in {"fast", "standard", "deep"} else "standard"
    citation_style = citation_style if citation_style in {"minimal", "auto", "strong"} else "auto"
    language = language if language in {"zh", "bilingual"} else "zh"
    reply = {
        "concise": "简洁：先给结论，控制在 3–5 个要点，不展开无关背景。",
        "auto": "自动：根据问题复杂度调整篇幅。",
        "detailed": "详细：给出解释、例子、适用条件与易错点，必要时分小标题。",
    }[reply_style]
    effort = {
        "fast": "快速：优先直接作答，减少展开和推导。",
        "standard": "标准：先梳理关键依据，再组织回答。",
        "deep": "深入：先梳理关键依据和可核验的推导摘要，再给答案；说明适用条件、边界和易错点。只给可核验的推导摘要，不输出隐藏思维链。",
    }[thinking_effort]
    citation = {
        "minimal": "精简：只在最关键结论处点明资料依据。",
        "auto": "自动：按回答需要引用资料中的关键依据。",
        "strong": "强调：尽量点明资料中的关键术语和依据，系统会附更多书内出处。",
    }[citation_style]
    lang = "中文为主；关键术语后括注英文。" if language == "bilingual" else "使用中文术语与表达。"
    lines = [
        "【回答偏好】",
        "回复风格：" + reply,
        "思考强度：" + effort,
        "引用偏好：" + citation,
        "回答语言：" + lang,
    ]
    custom = str(custom_instruction or "").strip()[:300]
    if custom:
        lines.append("自定义表达偏好（只影响组织与表达，不覆盖资料依据、AI 补充标注和安全约束）：" + custom)
    return "\n".join(lines)


def _compact_early_history(history: list) -> str:
    """早期轮次做确定性摘录压缩；最近两轮原文由 messages 完整保留。"""
    if not history:
        return ""
    lines = []
    for m in history[-8:]:
        label = "学习者" if m.get("role") == "user" else "老师"
        text = re.sub(r"\s+", " ", m.get("content") or "").strip()
        if len(text) > 260:
            text = text[:260].rstrip() + "…"
        if text:
            lines.append(f"{label}：{text}")
    return "【较早对话脉络（压缩）】\n" + "\n".join(lines) if lines else ""


def build_learn_messages(ctx_text: str, question: str, history: list = None,
                         completion_mode: bool = False, *,
                         reply_style: str = "auto", thinking_effort: str = "standard",
                         citation_style: str = "auto", language: str = "zh",
                         custom_instruction: str = "") -> list:
    """构造学习模式的原生多轮 messages。

    - 最近两轮（最多 4 条 user/assistant 消息）完整保留，不再做 300 字硬截断；
    - 更早轮次压成确定性摘录，作为 system 中的上下文承接；
    - 资料与系统规则放 system，最新问题与偏好放当前 user。
    """
    norm = _normalise_history(history)
    # 防止前端误把本轮问题也放进 history
    if norm and norm[-1]["role"] == "user" and norm[-1]["content"].strip() == (question or "").strip():
        norm = norm[:-1]
    older, recent = (norm[:-4], norm[-4:]) if len(norm) > 4 else ([], norm)
    pref = _learn_preference_text(reply_style, thinking_effort, citation_style,
                                  language, custom_instruction)
    if completion_mode:
        answer_rule = ("回答约束：资料没有覆盖、但有助于完整理解的内容，"
                       "必须放在明确的“【AI 补充】”段落中；不要把补充内容说成资料原文。")
    else:
        answer_rule = ("回答约束：资料有明确依据的内容要讲清楚；可由资料合理推出的逻辑桥梁要标明是推导；"
                       "确实超出资料范围时自然说明边界，不编造具体事实或出处。")
    system = (LEARN_SYSTEM_COMPLETE if completion_mode else LEARN_SYSTEM)
    system += "\n\n【资料】\n" + ctx_text
    early = _compact_early_history(older)
    if early:
        system += "\n\n" + early
    user = ("【本轮问题】\n" + (question or "").strip()
            + "\n\n" + answer_rule + "\n\n" + pref)
    return [{"role": "system", "content": system}] + recent + [{"role": "user", "content": user}]


def build_learn_prompt(ctx_text: str, question: str, history: list = None,
                       completion_mode: bool = False, *,
                       reply_style: str = "auto", thinking_effort: str = "standard",
                       citation_style: str = "auto", language: str = "zh",
                       custom_instruction: str = "") -> str:
    """调试/兼容用的扁平视图；实际模型调用使用 build_learn_messages。"""
    msgs = build_learn_messages(
        ctx_text, question, history, completion_mode=completion_mode,
        reply_style=reply_style, thinking_effort=thinking_effort,
        citation_style=citation_style, language=language,
        custom_instruction=custom_instruction)
    labels = {"system": "系统", "user": "学习者", "assistant": "老师"}
    return "\n\n".join(labels.get(m["role"], m["role"]) + "：" + m["content"] for m in msgs)


async def llm_learn(ctx_text: str, question: str, history: list = None, *,
                    completion_mode: bool = False,
                    reply_style: str = "auto", thinking_effort: str = "standard",
                    citation_style: str = "auto", language: str = "zh",
                    custom_instruction: str = "",
                    api_key: str = "", base_url: str = "", model: str = "",
                    on_delta=None) -> str | None:
    """学习模式回答（流式）。未配置或调用失败返回 None（调用方走降级话术）。"""
    cfg = _resolve_or(api_key, base_url, model)
    if not cfg or not cfg.get("api_key"):
        return None
    prefs = normalise_chat_prefs({
        "replyStyle": reply_style, "thinkingEffort": thinking_effort,
        "citationStyle": citation_style, "language": language,
        "customInstruction": custom_instruction,
    })
    style_bonus = {"concise": -260, "auto": 0, "detailed": 800}[prefs["replyStyle"]]
    # 深入档的真实推理 token 和可见回答共用 max_tokens，必须给可见回答留足预算。
    effort_bonus = {"fast": -180, "standard": 0, "deep": 3000}[prefs["thinkingEffort"]]
    max_tokens = max(420, min(6400, 1600 + style_bonus + effort_bonus
                              + (220 if completion_mode else 0)))
    temperature = {"fast": 0.5, "standard": 0.55, "deep": 0.65}[prefs["thinkingEffort"]]
    # “深入”在支持的网关上开启真实推理预算；不支持该参数的端点会被 app_settings 过滤。
    reasoning_effort = "medium" if prefs["thinkingEffort"] == "deep" else "none"
    messages = build_learn_messages(
        ctx_text, question, history, completion_mode=completion_mode,
        reply_style=prefs["replyStyle"], thinking_effort=prefs["thinkingEffort"],
        citation_style=prefs["citationStyle"], language=prefs["language"],
        custom_instruction=prefs["customInstruction"])
    text = await _chat_stream(
        cfg.get("base_url") or "", cfg["api_key"], cfg.get("model") or "",
        messages=messages, temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort, on_delta=on_delta)
    text = (text or "").strip()
    return text or None


# ────────────────────────────────────────────
# 会话（单考点状态机 + 代码判定，对学习者完全隐形）
# ────────────────────────────────────────────

class ReviewSession:
    """一轮复习会话：多个考点顺序推进，每考点一个状态机（内部脚手架）。

    judge / sayer 可注入：
      async judge(unit, state, answer, is_restate, on_delta=None, history=None) -> dict
      async sayer(kind, ctx, on_delta=None) -> str | None
    on_delta(str) 为流式出字回调（一轮请求只给"主话语"用一次）。
    判分器若接受 history，会收到最近 10 条原生对话消息（user/assistant）。
    """

    def __init__(self, session_id: str, file_key: str, units: list,
                 judge=None, sayer=None, *, tree_title: str = "",
                 store: ReviewStore = None, session_no: int = 0,
                 intro_label: str = "", prefs=None):
        self.session_id = session_id
        self.file_key = file_key
        self.tree_title = tree_title
        self.intro_label = (intro_label or "").strip()
        self.store = store
        self.session_no = session_no
        self.judge = judge or llm_judge
        self.sayer = sayer
        self.prefs = normalise_chat_prefs(prefs)
        try:
            jparams = inspect.signature(self.judge).parameters
            self._judge_history_ok = ("history" in jparams
                                      or any(p.kind == inspect.Parameter.VAR_KEYWORD
                                             for p in jparams.values()))
            self._judge_prefs_ok = ("preferences" in jparams
                                    or any(p.kind == inspect.Parameter.VAR_KEYWORD
                                           for p in jparams.values()))
        except Exception:
            self._judge_history_ok = False
            self._judge_prefs_ok = False

        self.units = units
        self.states = [self._init_state(u) for u in units]
        self.idx = -1
        self.done = False
        self.messages = []          # {role, text, kind}
        self.started_at = time.time()
        self.ended_at = None
        self._busy = False

    # ── 状态初始化 ──

    @staticmethod
    def _init_state(unit: dict) -> dict:
        return {
            "turn_count": 0,
            "hint_count": 0,
            "hint_levels": [],
            "levels": [],
            "independent_answers": 0,
            "prompt_had_hint": False,
            "awaiting": "answer",        # answer | restate | h4_restate
            "feedback_given": False,
            "trap_list": [],
            "trap_resolved": False,
            "last_gaps": [],
            "h4_used": False,
            "clarify_count": 0,          # 概念答疑次数（不计真实回忆轮）
            "status": "pending",         # pending|passed|half|failed
            "reason": "",
            "stuck_level": "",
            "taught": False,             # 本考点是否已讲过答案
        }

    # ── 基础 ──

    def _cur(self):
        return self.states[self.idx] if 0 <= self.idx < len(self.states) else None

    def _cur_unit(self):
        return self.units[self.idx] if 0 <= self.idx < len(self.units) else None

    def _push(self, role: str, text: str, kind: str = "text") -> dict:
        msg = {"role": role, "text": text, "kind": kind, "ts": time.time()}
        self.messages.append(msg)
        return msg

    def _judge_history(self) -> list:
        """当前轮之前的真实对话，映射为原生 user / assistant。"""
        out = []
        for m in self.messages[:-1][-12:]:
            role = m.get("role")
            if role == "learner":
                role = "user"
            elif role == "guide":
                role = "assistant"
            else:
                continue
            text = str(m.get("text") or "").strip()
            if text:
                out.append({"role": role, "text": text})
        return out

    async def _say(self, kind: str, ctx: dict, fallback: str, on_delta=None) -> str:
        text = None
        if self.sayer:
            try:
                text = await self.sayer(kind, ctx, on_delta=on_delta)
            except TypeError:
                # 兼容不接受 on_delta 的注入实现
                try:
                    text = await self.sayer(kind, ctx)
                except Exception:
                    text = None
            except Exception:
                text = None
        return text or fallback

    # ── 生命周期 ──

    async def start(self, on_delta=None) -> dict:
        if self.idx < 0 and self.units:
            self.idx = 0
            unit = self._cur_unit()
            if self.intro_label:
                # 开场先说清这轮在复习什么、共几个考点——AI 直接出题时不再显得"凭空发问"
                self._push("system", "开始复习 · " + self.intro_label)
            text = await self._say("open", self._open_ctx(unit),
                                   _opening_question(unit), on_delta=on_delta)
            self._push("guide", text, "question")
        elif not self.units:
            self.done = True
            self._push("system", "这个范围里没有找到合适的考点，换一章或者整本试试？", "report")
        self._persist()
        return self.snapshot()

    async def skip(self, on_delta=None) -> dict:
        """主动放下当前考点：先讲答案，再自然换题。"""
        st = self._cur()
        if not st or self.done:
            return self.snapshot()
        st["status"] = "failed"
        st["reason"] = "S4_abandon"
        await self._finish_unit(on_delta=on_delta)
        return self.snapshot()

    async def end_now(self, on_delta=None) -> dict:
        """手动收尾：自然话别并落盘。"""
        if self.done:
            return self.snapshot()
        self.done = True
        self.ended_at = time.time()
        self._push("system", await self._wrap_text(on_delta=on_delta), "report")
        if self.store:
            self.store.record_session_summary(self.file_key, self.session_summary())
        self._persist()
        return self.snapshot()

    async def handle_answer(self, text: str, on_delta=None) -> dict:
        st = self._cur()
        unit = self._cur_unit()
        if not st or not unit or self.done or self._busy:
            return self.snapshot()
        self._busy = True
        try:
            return await self._handle_answer_inner(text, st, unit, on_delta)
        finally:
            self._busy = False

    async def _handle_answer_inner(self, text: str, st: dict, unit: dict, on_delta=None) -> dict:

        text = (text or "").strip()
        st.setdefault("clarify_count", 0)
        self._push("learner", text)

        # ── 求助：索要答案（直接给要点） ──
        if ASK_FOR_ANSWER_RE.search(text) and not st["h4_used"]:
            await self._do_reveal(st, unit)
            return self.snapshot()

        # ── 求助：想不起来 / 不知道 / 要提示（不计回忆轮，不进止损计数） ──
        if HELP_HINT_RE.search(text) or is_help_only(text):
            await self._do_help_hint(st, unit, on_delta)
            return self.snapshot()

        # ── 明确的概念反问：先答疑，再回到原问题（不计回忆轮） ──
        if is_conceptual_question(text) and st.get("clarify_count", 0) < MAX_CLARIFY:
            await self._do_clarify(st, unit, text, on_delta=on_delta)
            return self.snapshot()

        st["turn_count"] += 1
        is_restate = st["awaiting"] in ("restate", "h4_restate")
        independent = (not st["prompt_had_hint"]) and st["awaiting"] != "h4_restate"

        judge_kwargs = {"on_delta": on_delta}
        if self._judge_history_ok:
            judge_kwargs["history"] = self._judge_history()
        if self._judge_prefs_ok:
            judge_kwargs["preferences"] = self.prefs
        judged = await self.judge(unit, st, text, is_restate, **judge_kwargs)
        if not isinstance(judged, dict) or "level" not in judged:
            judged = _normalise_judged(judged or {}, len(unit.get("reference_points") or []))
        n_points = len(unit.get("reference_points") or [])
        matched = [i for i in (judged.get("matched_points") or []) if 0 <= i < n_points]
        gaps_count = max(0, n_points - len(matched))

        # ── 临时答疑优先于“索要答案”识别：理解性反问不走揭晓答案 ──
        if (judged.get("next_action") == "clarify" and not is_restate
                and st.get("clarify_count", 0) < MAX_CLARIFY):
            st["turn_count"] = max(0, st["turn_count"] - 1)
            st["clarify_count"] += 1
            reply = judged.get("reply") or "先把这层逻辑理顺，再回到刚才的问题。"
            self._push("guide", reply, "clarify")
            self._persist()
            return self.snapshot()

        asks = judged.get("asks_for_answer") or bool(ASK_FOR_ANSWER_RE.search(text))
        if asks and not st["h4_used"]:
            await self._do_reveal(st, unit)
            return self.snapshot()

        # 记录层级与陷阱（内部）
        st["levels"].append(judged["level"])
        if independent:
            st["independent_answers"] += 1
        new_traps = [c for c in judged.get("confusions", []) if c not in st["trap_list"]]
        if st["trap_list"]:
            overlap = any(c in st["trap_list"] for c in judged.get("confusions", []))
            if judged.get("trap_resolved") and not overlap:
                st["trap_resolved"] = True
        st["trap_list"] = (st["trap_list"] + new_traps)[:6]
        st["last_gaps"] = judged.get("gaps", [])

        # 求助复述轮：收掉该点，回头再测
        if st["awaiting"] == "h4_restate":
            st["status"] = "half"
            st["reason"] = "h4_reveal"
            await self._finish_unit()
            return self.snapshot()

        # ── 止损（S1–S3，内部判定）→ 讲答案后换题 ──
        stop_reason = self._check_stop(st, judged)
        if stop_reason:
            st["status"] = "failed"
            st["reason"] = stop_reason
            st["stuck_level"] = judged["level"]
            await self._finish_unit(on_delta=on_delta, note=text)
            return self.snapshot()

        # ── 过关判定（C1–C4，内部） ──
        c1 = judged["level"] in PASS_LEVELS and not st["prompt_had_hint"]
        c2 = (n_points == 0) or (gaps_count == 0)
        c3 = (not st["trap_list"]) or st["trap_resolved"]
        criteria = c1 and c2 and c3

        if criteria:
            if st["awaiting"] != "restate" and not (st["turn_count"] == 1 and st["independent_answers"] == 1):
                st["awaiting"] = "restate"
                st["prompt_had_hint"] = False
                self._push_reply(judged, unit, on_delta=on_delta)
                text_out = await self._say(
                    "restate", {"title": unit["title"]}, _restate_question(unit))
                self._push("guide", text_out, "question")
                self._persist()
                return self.snapshot()
            st["status"] = "passed" if st["hint_count"] == 0 else "half"
            st["reason"] = "C1-C4" if st["hint_count"] == 0 else "hint_assisted"
            self._push_reply(judged, unit, on_delta=on_delta)
            await self._finish_unit()
            return self.snapshot()

        # ── 未达标：自然回应 + 追问/提示 ──
        reply = judged.get("reply") or _safe_probe()
        hint_level = None

        if judged["level"] in STUCK_LEVELS:
            if st["hint_count"] < MAX_HINTS:
                hint_level = HINT_ORDER[st["hint_count"]]
            else:
                # 提示已用尽且仍卡 → 讲答案，止损换题
                st["status"] = "failed"
                st["reason"] = "S1_hints"
                st["stuck_level"] = judged["level"]
                await self._finish_unit(on_delta=on_delta, note=text)
                return self.snapshot()
        elif judged.get("next_action") == "hint" and st["hint_count"] < MAX_HINTS:
            hint_level = HINT_ORDER[st["hint_count"]]

        outbound = reply
        if hint_level:
            st["hint_count"] += 1
            st["hint_levels"].append(hint_level)
            st["prompt_had_hint"] = True
            # judge 已在 reply 里带提示（标了 hint_level）或已用问句引导 → 只记账；
            # 完全没带引导时才补一句模板提示兜底
            engaged = judged.get("hint_level") is not None or reply.rstrip().endswith(("？", "?"))
            if not engaged:
                outbound = reply + "\n" + _hint_fallback(hint_level)
        else:
            st["prompt_had_hint"] = False

        if detect_leak(outbound, unit.get("reference_points") or [], hint_level):
            outbound = _safe_probe()
            if judged.get("level") in STUCK_LEVELS and st["hint_count"] < MAX_HINTS:
                hint_level = HINT_ORDER[st["hint_count"]]
                st["hint_count"] += 1
                st["hint_levels"].append(hint_level)
                st["prompt_had_hint"] = True
                outbound += "\n" + _hint_fallback(hint_level)
            print(f"  LEAK-BLOCK[{unit['unit_id']}] 已拦截疑似泄漏的追问")

        st["awaiting"] = "restate" if judged.get("next_action") == "restate" and not hint_level else "answer"
        st["feedback_given"] = True
        self._push("guide", outbound, "hint" if hint_level else "question")
        self._persist()
        return self.snapshot()

    # ── 求助 / 讲解 / 过渡（内部判定 → 自然话语） ──

    def _push_reply(self, judged: dict, unit: dict, hint_level=None, on_delta=None) -> None:
        """把判分轮的自然回应说出去；流式时前端已看到增量，这里以最终文本落库。"""
        reply = (judged or {}).get("reply") or ""
        if not reply:
            return
        if detect_leak(reply, unit.get("reference_points") or [], hint_level):
            # 流式可能已播出带内容的文本，以安全话术为准（快照重渲染会覆盖）
            print(f"  LEAK-BLOCK[{unit['unit_id']}] 已拦截疑似泄漏的回应")
            self._push("guide", _safe_probe(), "question")
            return
        self._push("guide", reply, "question")

    async def _do_clarify(self, st: dict, unit: dict, question: str,
                          on_delta=None) -> None:
        """理解性反问：讲清概念但不揭晓整题答案，不计回忆轮。"""
        st["clarify_count"] = st.get("clarify_count", 0) + 1
        ctx = {"title": unit["title"], "points": unit.get("reference_points") or [],
               "summary": unit.get("summary") or "", "question": question}
        fallback = ("这个点先抓住核心关系：它是理解这一类问题的前提。"
                    "理清这层逻辑后，我们回到刚才的问题，你按自己的话再串一遍。")
        text = await self._say("clarify", ctx, fallback, on_delta=on_delta)
        self._push("guide", text, "clarify")
        self._persist()

    async def _do_reveal(self, st: dict, unit: dict) -> None:
        st["h4_used"] = True
        st["hint_levels"].append("H4")
        self._push("system", _reveal_text(unit), "reveal")
        st["awaiting"] = "h4_restate"
        st["prompt_had_hint"] = True
        self._push("guide", "好，照着上面这些，用自己的话完整说一遍。", "question")
        self._persist()

    async def _do_help_hint(self, st: dict, unit: dict, on_delta=None) -> None:
        if st["hint_count"] >= MAX_HINTS:
            # 提示用尽仍求助 → 直接讲答案并收掉该点
            st["status"] = "failed"
            st["reason"] = "S4_abandon"
            await self._finish_unit(on_delta=on_delta, note="想不起来了")
            return
        level = HINT_ORDER[st["hint_count"]]
        st["hint_count"] += 1
        st["hint_levels"].append(level)
        st["prompt_had_hint"] = True
        st["awaiting"] = "answer"
        text = await self._say(
            "hint", {"title": unit["title"], "level": level}, _hint_fallback(level),
            on_delta=on_delta)
        if detect_leak(text, unit.get("reference_points") or [], level):
            text = _hint_fallback(level)
            print(f"  LEAK-BLOCK[{unit['unit_id']}] 已拦截疑似泄漏的提示")
        self._push("guide", text, "hint")
        self._persist()

    async def _do_teach(self, st: dict, unit: dict, on_delta=None, note: str = "") -> None:
        """把整理好的答案讲出来（陈述收尾，不追问）。讲解豁免泄漏检测。"""
        if st.get("taught"):
            return
        st["taught"] = True
        ctx = {"title": unit["title"], "points": unit.get("reference_points") or [],
               "summary": unit.get("summary") or "", "note": note}
        reveal_delta = None
        if on_delta:
            # 让前端从第一个 token 起就用考点气泡样式流式渲染，
            # 而不是先显示普通气泡、整段结束后再替换。
            def reveal_delta(chunk):
                on_delta({"text": chunk, "kind": "reveal"})
        text = await self._say("explain", ctx, _teach_fallback(unit),
                               on_delta=reveal_delta)
        self._push("system", text, "reveal")

    async def _finish_unit(self, on_delta=None, note: str = "") -> None:
        """收掉当前考点：没掌握的先讲答案，再一句自然过渡/收尾。"""
        st = self._cur()
        unit = self._cur_unit()
        if st and unit and st["status"] == "failed" and not st.get("h4_used"):
            await self._do_teach(st, unit, on_delta=on_delta, note=note)
        if self.store and unit and st:
            self.store.record_unit_result(
                self.file_key, unit, st["status"],
                max(st["levels"], key=lambda l: LEVELS.index(l)) if st["levels"] else "L0",
                self.session_id, st["reason"])
        self.idx += 1
        if self.idx < len(self.units):
            nxt = self._cur_unit()
            flavor = {"passed": "掌握得不错", "half": "还不太稳",
                      "failed": "还不太稳"}.get((st or {}).get("status"), "")
            text = await self._say(
                "transition",
                {"prev": unit["title"] if unit else "", "flavor": flavor, "title": nxt["title"]},
                _transition_fallback(unit["title"] if unit else "", nxt["title"]))
            self._push("guide", text, "question")
        else:
            self.done = True
            self.ended_at = time.time()
            self._push("system", await self._wrap_text(), "report")
            if self.store:
                self.store.record_session_summary(self.file_key, self.session_summary())
        self._persist()

    # ── 判定辅助 ──

    def _open_ctx(self, unit: dict) -> dict:
        types = {e.get("type") for e in (unit.get("exam_points") or []) if e.get("type")}
        tip = ""
        if "名词解释" in types:
            tip = "这是名词解释的常客。"
        elif types & {"简答", "论述"}:
            tip = "这道题爱考简答论述。"
        return {"title": unit["title"], "path": unit.get("path") or [],
                "summary": unit.get("summary") or "",
                "points": unit.get("reference_points") or [], "exam_tip": tip}

    def _check_stop(self, st: dict, judged: dict) -> str | None:
        if st["turn_count"] >= MAX_TURNS:
            return "S3_turns"
        recent = st["levels"][-STUCK_ROUNDS:]
        if len(recent) >= STUCK_ROUNDS and all(l in STUCK_LEVELS for l in recent):
            if st["hint_count"] >= MAX_HINTS:
                return "S1_hints"
            return "S2_stuck"
        return None

    # ── 报表 ──

    def _counts(self) -> dict:
        c = {"passed": 0, "half": 0, "failed": 0}
        for s in self.states:
            if s["status"] in c:
                c[s["status"]] += 1
        return c

    async def _wrap_text(self, on_delta=None) -> str:
        shaky = [self.units[i]["title"] for i, s in enumerate(self.states)
                 if s["status"] in ("half", "failed")]
        return await self._say("wrap", {"shaky": "、".join(shaky[:4])},
                               _wrap_fallback(shaky), on_delta=on_delta)

    def session_summary(self) -> dict:
        c = self._counts()
        return {
            "session_id": self.session_id, "file_key": self.file_key,
            "started_at": self.started_at, "ended_at": self.ended_at or time.time(),
            **c, "units": [{
                "unit_id": u["unit_id"], "title": u["title"], "status": s["status"],
                "reason": s["reason"], "turns": s["turn_count"], "hints": s["hint_count"],
                "best_level": max(s["levels"], key=lambda l: LEVELS.index(l)) if s["levels"] else "",
            } for u, s in zip(self.units, self.states)],
        }

    # ── 快照与持久化 ──

    def snapshot(self) -> dict:
        st = self._cur()
        if st is None and self.states:
            st = self.states[-1]
        unit = self._cur_unit()
        cur = None
        if unit and st and not self.done:
            cur = {
                "unit_id": unit["unit_id"], "title": unit["title"],
                "group": unit.get("group", ""), "path": unit["path"],
                "reference_count": len(unit.get("reference_points") or []),
                "turn_count": st["turn_count"], "hint_count": st["hint_count"],
                "last_level": st["levels"][-1] if st["levels"] else None,
                "awaiting": st["awaiting"], "exam_points": unit.get("exam_points") or [],
                "h4_used": st["h4_used"],
            }
        return {
            "session_id": self.session_id,
            "file_key": self.file_key,
            "tree_title": self.tree_title,
            "done": self.done,
            "current": cur,
            "counters": {"turn": (st or {}).get("turn_count", 0), "turn_max": MAX_TURNS,
                         "hint": (st or {}).get("hint_count", 0), "hint_max": MAX_HINTS},
            "units": [{
                "unit_id": u["unit_id"], "title": u["title"], "group": u.get("group", ""),
                "status": s["status"],
                "stuck_level": s["stuck_level"] or (
                    max(s["levels"], key=lambda l: LEVELS.index(l)) if s["levels"] else ""),
                "turns": s["turn_count"], "hints": s["hint_count"],
            } for u, s in zip(self.units, self.states)],
            "messages": self.messages[-60:],
            "counts": self._counts(),
            "report": self.messages[-1]["text"] if self.done and self.messages
                      and self.messages[-1].get("kind") == "report" else None,
        }

    def to_state(self) -> dict:
        return {
            "session_id": self.session_id, "file_key": self.file_key,
            "tree_title": self.tree_title, "session_no": self.session_no,
            "units": self.units, "states": self.states, "idx": self.idx,
            "done": self.done, "messages": self.messages[-200:],
            "started_at": self.started_at, "ended_at": self.ended_at,
            "prefs": self.prefs, "intro_label": self.intro_label,
        }

    @classmethod
    def from_state(cls, state: dict, judge=None, sayer=None, store=None) -> "ReviewSession":
        s = cls(state["session_id"], state["file_key"], state["units"],
                judge=judge, sayer=sayer, tree_title=state.get("tree_title", ""),
                store=store, session_no=state.get("session_no", 0),
                intro_label=state.get("intro_label", ""),
                prefs=state.get("prefs"))
        s.states = state["states"]
        s.idx = state["idx"]
        s.done = state["done"]
        s.messages = state.get("messages", [])
        s.started_at = state.get("started_at", time.time())
        s.ended_at = state.get("ended_at")
        return s

    def _persist(self) -> None:
        if self.store:
            self.store.save_session(self.to_state())


def new_session_id() -> str:
    return "rs_" + uuid.uuid4().hex[:10]
