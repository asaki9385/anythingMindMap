"""
统一配置中心 —— 一次配置，全局生效。

包含三类配置：
1. 模型服务（任意 OpenAI chat-completions 兼容端点，可存多套，一套设为默认）：
   增强 / 导学 / 结构化 / 复习判分 四个调用点统一取用
2. MinerU Token（PDF OCR）
3. 学科设置（科目名 / 考试名，供 AI 考点标注）

存储：data/settings.json（本地文件，随 data/ 被 gitignore）
安全：密钥只落本地；GET /api/settings 一律脱敏，绝不回显明文。
解析优先级：本次请求显式参数 > 统一配置 > 环境变量 > 无（离线降级）
"""

import json
import os
import re
import time
import uuid

import httpx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("KT_SETTINGS_PATH") or os.path.join(BASE_DIR, "data", "settings.json")

DEFAULT_BASE_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_SUBJECT = {"name": "教育学", "exam": "333教育综合考研"}

# 增强范围档位：控制 AI 增强覆盖的层级与节点上限（max_nodes = 0 表示不设上限）。
# 档位化解决"覆盖率只有百分之几"的问题：默认标准档把 L1–L3 全部解读完，
# 需要更快时切"快速"，需要更深时切"全量"。
ENHANCE_OPTIONS = [
    {"id": "fast", "label": "快速", "levels": [1, 2, 3], "max_nodes": 30,
     "desc": "只解读层级最靠前的 30 个节点；适合试跑，快而省。"},
    {"id": "standard", "label": "标准", "levels": [1, 2, 3], "max_nodes": 0,
     "desc": "解读全部 L1–L3 节点；解读完整、成本可控（默认）。"},
    {"id": "full", "label": "全量", "levels": [1, 2, 3, 4], "max_nodes": 0,
     "desc": "连 L4 也一并解读；最全面，耗时与用量也最高。"},
]
DEFAULT_ENHANCE_SCOPE = "standard"
_ENHANCE_BY_ID = {o["id"]: o for o in ENHANCE_OPTIONS}

# ────────────────────────────────────────────
# 内置凭证占位 —— 默认留空，不随仓库携带任何 Key。
#
# 运行时请通过设置页或环境变量配置：
#   本次请求参数 > 统一配置 > 环境变量 > 无（优雅降级）
# 保留常量结构是为了兼容旧调用点和打包清理流程。
# ────────────────────────────────────────────
BUILTIN_CHAT = {
    "provider_name": "未配置",
    "base_url": "",
    "model": "",
    "api_key": "",
}

BUILTIN_MINERU_TOKEN = ""


def builtin_chat() -> dict | None:
    """内置模型服务；api_key 为空时返回 None。"""
    if not (BUILTIN_CHAT.get("api_key") or "").strip():
        return None
    return dict(BUILTIN_CHAT)


# 进程级会话标识。opencode 的 Go 网关要求客户端自报会话 ID 才肯路由
# （缺失时一律 400 MissingSessionID）；同一进程内保持不变，服务端才好做 prompt caching。
_SESSION_ID = "kt-" + uuid.uuid4().hex[:16]


def chat_headers(base_url: str, api_key: str) -> dict:
    """统一构造模型服务请求头 —— 所有 chat-completions 调用点都走这里。

    网关可能要求 OpenAI 之外的额外头，集中在这里加，别散到各调用点去。
    """
    h = {
        "Authorization": f"Bearer {(api_key or '').strip()}",
        "Content-Type": "application/json",
    }
    if "opencode.ai" in (base_url or ""):
        h["x-opencode-session"] = _SESSION_ID
        h["User-Agent"] = "knowledge-tree/1.0"
    return h


def chat_body_extras(base_url: str, reasoning_effort: str | None = None) -> dict:
    """统一构造请求体的附加参数 —— 每个 chat-completions 调用点都应 merge 一次。

    默认仍对 opencode 关闭"思考"，避免批量解析时推理 token 吃光输出预算。
    学习/追问的“深入”档可显式传 reasoning_effort="medium" 开启真实推理预算；
    只向已知支持该参数的 opencode 网关发送，其他 OpenAI 兼容端点保持原样，
    避免第三方服务因未知字段直接 400。
    同一个模型同一份提示词，有时思考 0 token 直接作答（~550 token、5s），
    有时思考 1000+ token 把 max_tokens 吃光 → JSON 被截断 → 解析失败 →
    整批节点静默走兜底摘要。实测加 reasoning_effort="none" 后 4/4 稳定，
    输出降到 ~550 token，max_tokens=1000 够用。

    注意这是"给不稳定的路由兜底"，不是"给推理模型专用"——
    非推理模型（glm-5.3-flash）在这个网关上同样会偶发进入思考模式。
    """
    if "opencode.ai" in (base_url or ""):
        effort = (reasoning_effort or "none").strip().lower()
        if effort not in {"none", "minimal", "low", "medium", "high"}:
            effort = "none"
        return {"reasoning_effort": effort}
    return {}

# 预设（base_url 填到 /v1 或完整 /chat/completions 均可，保存时自动归一化）
PRESETS = [
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat"},
    {"name": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    {"name": "硅基流动 SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    {"name": "通义/阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"name": "自定义（OpenAI 兼容）", "base_url": "", "model": ""},
]


# ────────────────────────────────────────────
# 基础工具
# ────────────────────────────────────────────

def normalize_chat_url(u: str) -> str:
    """接受 base（…/v1、…/ ) 或完整 chat/completions 地址，统一为完整端点。"""
    u = (u or "").strip().rstrip("/")
    if not u:
        return ""
    if u.endswith("/chat/completions"):
        return u
    return u + "/chat/completions"


def mask_secret(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 10:
        return v[:2] + "****"
    return v[:6] + "****" + v[-4:]


def _providers(doc: dict) -> list:
    ps = doc.get("providers")
    return ps if isinstance(ps, list) else []


def _find(doc: dict, pid: str):
    for p in _providers(doc):
        if p.get("id") == pid:
            return p
    return None


# ────────────────────────────────────────────
# 读写
# ────────────────────────────────────────────

def _defaults() -> dict:
    return {
        "version": 1,
        "providers": [],
        "active_id": "",
        "mineru_token": "",
        "subject": dict(DEFAULT_SUBJECT),
        "enhance": {"scope": DEFAULT_ENHANCE_SCOPE},
        "updated_at": 0,
    }


def load() -> dict:
    doc = _defaults()
    try:
        with open(PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            doc.update(raw)
    except Exception:
        pass
    if not isinstance(doc.get("subject"), dict):
        doc["subject"] = dict(DEFAULT_SUBJECT)
    doc["subject"].setdefault("name", DEFAULT_SUBJECT["name"])
    doc["subject"].setdefault("exam", DEFAULT_SUBJECT["exam"])
    enh = doc.get("enhance")
    if not isinstance(enh, dict) or enh.get("scope") not in _ENHANCE_BY_ID:
        doc["enhance"] = {"scope": DEFAULT_ENHANCE_SCOPE}
    return doc


def save(doc: dict) -> None:
    doc["updated_at"] = time.time()
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


# ────────────────────────────────────────────
# 对外视图（脱敏）与操作
# ────────────────────────────────────────────

def public_view() -> dict:
    doc = load()
    env_key = os.environ.get("OPENAI_API_KEY", "")
    saved_mineru = (doc.get("mineru_token") or "").strip()
    env_mineru = os.environ.get("MINERU_TOKEN", "").strip()
    builtin_mineru = BUILTIN_MINERU_TOKEN.strip()
    eff_mineru = saved_mineru or env_mineru or builtin_mineru
    bc = builtin_chat()
    providers = []
    for p in _providers(doc):
        providers.append({
            "id": p.get("id", ""),
            "name": p.get("name") or "未命名",
            "base_url": p.get("base_url", ""),
            "model": p.get("model", ""),
            "has_key": bool(p.get("api_key")),
            "key_masked": mask_secret(p.get("api_key", "")),
            "verified_at": p.get("verified_at", 0),
            "active": p.get("id") == doc.get("active_id"),
        })
    return {
        "providers": providers,
        "active_id": doc.get("active_id", ""),
        "mineru": {
            "has_token": bool(eff_mineru),
            "token_masked": mask_secret(eff_mineru),
            "from_env": not saved_mineru and bool(env_mineru),
            "from_builtin": not saved_mineru and not env_mineru and bool(builtin_mineru),
        },
        "subject": doc["subject"],
        "enhance": {
            "scope": ((doc.get("enhance") or {}).get("scope") or DEFAULT_ENHANCE_SCOPE),
            "options": [{"id": o["id"], "label": o["label"], "desc": o["desc"]}
                        for o in ENHANCE_OPTIONS],
        },
        "builtin": {
            "chat": bool(bc),
            "chat_name": (bc or {}).get("provider_name", ""),
            "chat_model": (bc or {}).get("model", ""),
            "chat_key_masked": mask_secret((bc or {}).get("api_key", "")),
            "mineru": bool(builtin_mineru),
            "mineru_token_masked": mask_secret(builtin_mineru),
        },
        "subject_fallback": {
            "name": DEFAULT_SUBJECT["name"],
            "exam": DEFAULT_SUBJECT["exam"],
        },
        "env": {
            "has_openai_key": bool(env_key),
            "key_masked": mask_secret(env_key),
        },
        "presets": PRESETS,
    }


def upsert_provider(payload: dict) -> str:
    """新增或更新服务商。api_key 缺省 = 不修改；显式空串 = 清除。"""
    doc = load()
    pid = (payload.get("id") or "").strip()
    name = (payload.get("name") or "").strip() or "未命名"
    base_url = normalize_chat_url(payload.get("base_url") or "")
    model = (payload.get("model") or "").strip() or DEFAULT_MODEL
    if not base_url:
        raise ValueError("Base URL 不能为空")

    prov = _find(doc, pid) if pid else None
    if prov is None:
        prov = {"id": "srv_" + uuid.uuid4().hex[:8], "api_key": ""}
        _providers(doc).append(prov)
    prov["name"] = name
    prov["base_url"] = base_url
    prov["model"] = model
    if "api_key" in payload and payload.get("api_key") is not None:
        key = (payload.get("api_key") or "").strip()
        if key:                       # 非空才覆盖
            prov["api_key"] = key
    if not prov.get("api_key"):
        # 首次创建且没给 key：仍保存（用户可能稍后补）
        pass
    # 第一个服务商自动设为默认
    if not doc.get("active_id"):
        doc["active_id"] = prov["id"]
    save(doc)
    return prov["id"]


def delete_provider(pid: str) -> None:
    doc = load()
    doc["providers"] = [p for p in _providers(doc) if p.get("id") != pid]
    if doc.get("active_id") == pid:
        doc["active_id"] = doc["providers"][0]["id"] if doc["providers"] else ""
    save(doc)


def set_active(pid: str) -> None:
    doc = load()
    if _find(doc, pid):
        doc["active_id"] = pid
        save(doc)


def set_general(payload: dict) -> None:
    """MinerU Token 与学科设置。token 缺省 = 不修改；显式空串 = 清除。"""
    doc = load()
    if "mineru_token" in payload and payload.get("mineru_token") is not None:
        tok = (payload.get("mineru_token") or "").strip()
        if tok:
            doc["mineru_token"] = tok
        elif payload.get("clear_mineru"):
            doc["mineru_token"] = ""
    sub = payload.get("subject")
    if isinstance(sub, dict):
        name = (sub.get("name") or "").strip()
        exam = (sub.get("exam") or "").strip()
        doc["subject"] = {
            "name": name or DEFAULT_SUBJECT["name"],
            "exam": exam or DEFAULT_SUBJECT["exam"],
        }
    save(doc)


# ────────────────────────────────────────────
# 解析（四个 LLM 调用点统一入口）
# ────────────────────────────────────────────

def resolve_chat(api_key: str = "", base_url: str = "", model: str = "") -> dict | None:
    """本次请求参数 > 统一配置（默认服务商）> 环境变量 > None。

    返回 {base_url, model, api_key, provider_name, source} 或 None（未配置）。
    """
    key = (api_key or "").strip()
    doc = load()
    prov = _find(doc, doc.get("active_id", "")) or None
    if prov is None:
        with_key = [p for p in _providers(doc) if p.get("api_key")]
        prov = with_key[0] if with_key else None

    if key:
        # 显式传入（上传/复习页覆盖，或任务级 Key）：沿用当前生效的端点与模型
        bc = builtin_chat() or {}
        base = normalize_chat_url(base_url or (prov or {}).get("base_url")
                                 or bc.get("base_url") or DEFAULT_BASE_URL)
        mdl = (model or (prov or {}).get("model")
               or bc.get("model") or DEFAULT_MODEL).strip()
        return {"base_url": base, "model": mdl, "api_key": key,
                "provider_name": (prov or {}).get("name", "") or "本次指定", "source": "request"}

    if prov and prov.get("api_key"):
        return {
            "base_url": normalize_chat_url(prov.get("base_url") or DEFAULT_BASE_URL),
            "model": (prov.get("model") or DEFAULT_MODEL).strip(),
            "api_key": prov["api_key"],
            "provider_name": prov.get("name") or "未命名",
            "source": "settings",
        }

    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return {
            "base_url": normalize_chat_url(os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL),
            "model": (os.environ.get("MODEL") or DEFAULT_MODEL).strip(),
            "api_key": env_key,
            "provider_name": "环境变量",
            "source": "env",
        }

    bc = builtin_chat()
    if bc:
        bc["source"] = "builtin"
        return bc
    return None


def mineru_token() -> str:
    """MinerU Token：统一配置 > 环境变量 > 内置占位（默认空）。"""
    doc = load()
    return ((doc.get("mineru_token") or "").strip()
            or os.environ.get("MINERU_TOKEN", "").strip()
            or BUILTIN_MINERU_TOKEN.strip())


def enhance_config() -> dict:
    """当前增强档位：{scope, label, levels, max_nodes, desc}。max_nodes=0 表示不设上限。"""
    doc = load()
    scope = (doc.get("enhance") or {}).get("scope") or DEFAULT_ENHANCE_SCOPE
    opt = _ENHANCE_BY_ID.get(scope) or _ENHANCE_BY_ID[DEFAULT_ENHANCE_SCOPE]
    return {"scope": opt["id"], "label": opt["label"], "levels": list(opt["levels"]),
            "max_nodes": int(opt["max_nodes"]), "desc": opt["desc"]}


def set_enhance(scope: str) -> dict:
    """切换增强档位（fast / standard / full）。"""
    scope = (scope or "").strip()
    if scope not in _ENHANCE_BY_ID:
        raise ValueError(f"未知增强档位: {scope}")
    doc = load()
    doc["enhance"] = {"scope": scope}
    save(doc)
    return enhance_config()


def subject_config() -> dict:
    doc = load()
    sub = doc.get("subject") or {}
    return {
        "name": (sub.get("name") or DEFAULT_SUBJECT["name"]).strip(),
        "exam": (sub.get("exam") or DEFAULT_SUBJECT["exam"]).strip(),
    }


def set_subject(name: str, exam: str) -> dict:
    doc = load()
    sub = doc.get("subject") or {}
    sub["name"] = (name or "").strip() or DEFAULT_SUBJECT["name"]
    sub["exam"] = (exam or "").strip() or DEFAULT_SUBJECT["exam"]
    doc["subject"] = sub
    save(doc)
    return dict(sub)


# 结构审校（structure_repair）已固化进 AI 增强环节，不再有对外开关：
# 增强阶段必定先审校一遍知识树（确定性规则按编号修顺序 → LLM 兜底编号重复/断档、
# 改写"坏标题"），无 API key 时自动退化为仅确定性审校。
# 原先的 audit_config() / set_audit() 与设置页开关已于 2026-10-15 移除；
# 若要恢复，需同时改回 server._enhance_tree 的调用点、/api/settings 的 op，
# 以及 ui/settings.html 的卡片。


# ────────────────────────────────────────────
# 连接测试
# ────────────────────────────────────────────

# 从网页/文档复制 Token 时容易带上的不可见字符（零宽空格 / BOM 等）
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff]")


async def test_chat(base_url: str, api_key: str, model: str) -> dict:
    """对 chat-completions 端点做一次最小真实调用。"""
    url = normalize_chat_url(base_url)
    if not url:
        return {"ok": False, "error": "Base URL 为空"}
    if not (api_key or "").strip():
        return {"ok": False, "error": "API Key 为空"}
    mdl = (model or DEFAULT_MODEL).strip()
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json={
                "model": mdl,
                "max_tokens": 64,
                "temperature": 0,
                "messages": [{"role": "user", "content": "回复OK两个字"}],
                **chat_body_extras(url),
            }, headers=chat_headers(url, api_key))
        ms = int((time.time() - t0) * 1000)
        if resp.status_code != 200:
            detail = ""
            try:
                detail = (resp.json().get("error") or {}).get("message", "")
            except Exception:
                detail = resp.text[:120]
            return {"ok": False, "status": resp.status_code, "latency_ms": ms,
                    "error": f"HTTP {resp.status_code}" + (f"：{detail}" if detail else "")}
        reply = ""
        try:
            reply = resp.json()["choices"][0]["message"]["content"][:40]
        except Exception:
            pass
        return {"ok": True, "latency_ms": ms, "model": mdl,
                "url": url, "reply": reply}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# 只在响应里出现这些「明确指向身份」的措辞时才判定为鉴权失败，
# 避免把「参数错误」误判成 Token 有问题。
_AUTH_HINTS = (
    "unauthorized", "forbidden", "authentication", "invalid token", "token invalid",
    "token expired", "invalid api key", "invalid apikey", "api key is invalid",
    "认证", "鉴权", "未授权", "未登录", "登录过期", "登录已过期", "授权失败",
    "token 无效", "token无效", "token 已过期", "token已过期",
)


def _looks_like_auth_error(status_code: int, msg: str) -> bool:
    if status_code in (401, 403):
        return True
    text = (msg or "").lower()
    return any(h in text for h in _AUTH_HINTS)


def judge_mineru_probe(status_code: int, body: dict | None, raw: str = "") -> dict:
    """把探测响应翻译成结论（纯函数，便于离线测试）。

    探测请求带的是空 files 列表：MinerU 的鉴权中间件先于参数校验执行，
    所以 Token 无效 → 401/403（或 body 里带鉴权类错误码）；
    Token 有效 → 只是参数被拒（4xx / HTTP 200 但 code≠0）。
    """
    body = body if isinstance(body, dict) else {}
    msg = str(body.get("msg") or body.get("message") or body.get("error") or "").strip()
    detail = msg or (raw or "").strip()[:120]

    if _looks_like_auth_error(status_code, msg):
        return {"ok": False, "status": status_code,
                "error": f"鉴权失败（HTTP {status_code}）：Token 无效、已过期或已被重置"
                         + (f" —— {detail}" if detail else "")}
    if status_code >= 500:
        return {"ok": False, "status": status_code,
                "error": f"MinerU 服务端异常（HTTP {status_code}），稍后重试"
                         + (f" —— {detail}" if detail else "")}
    # 走到这里说明服务器没有否认身份，其余都只是「空参数被拒」→ 视为鉴权已通过。
    # 但空参数探测无法 100% 排除「服务端先校验参数、后校验身份」，故如实说明。
    if detail:
        return {"ok": True, "status": status_code, "ambiguous": True,
                "note": f"鉴权探测通过（服务端仅返回参数提示：{detail}）"}
    return {"ok": True, "status": status_code, "note": "鉴权通过（未创建任务）"}


def test_mineru(token: str) -> dict:
    """MinerU Token 本地格式检查 + 在线鉴权探测（不创建任务）。

    MinerU 的 Token 有两代形态：旧版是 JWT（eyJ… 三段、以 . 分隔），
    新版是 sk- 开头的 API Key。本地只拦明显的粘贴错误，
    真正的判定交给在线鉴权探测——不要在本地猜 Token 形态。
    """
    tok = (token or "").strip()
    if not tok:
        return {"ok": False, "error": "Token 为空"}
    if any(c.isspace() for c in tok) or _ZERO_WIDTH.search(tok):
        return {"ok": False, "format_ok": False,
                "error": "Token 里混进了空格、换行或零宽字符，请只粘贴 Token 本身"}
    if len(tok) < 12:
        return {"ok": False, "format_ok": False,
                "error": f"Token 只有 {len(tok)} 个字符，疑似粘贴不完整"}

    try:
        resp = httpx.post(
            "https://mineru.net/api/v4/file-urls/batch",
            json={"files": []},
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            timeout=15)
    except Exception as e:
        return {"ok": False, "format_ok": True,
                "error": f"格式检查已通过，但连不上 MinerU（{type(e).__name__}），"
                         f"请检查本机网络或代理设置"}

    try:
        body = resp.json()
    except Exception:
        body = None
    out = judge_mineru_probe(resp.status_code, body, getattr(resp, "text", "") or "")
    out["format_ok"] = True
    return out
