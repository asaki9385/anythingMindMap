"""
统一配置中心测试（离线）

覆盖：URL 归一化 / 脱敏 / 读写与容错 / 服务商增删改与默认切换 /
      MinerU 与学科设置 / resolve_chat 四级优先级 / public_view 不泄漏明文
运行：python knowledge-compiler/test_app_settings.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app_settings as S

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


class Env:
    """临时环境变量上下文。"""

    def __init__(self, **kv):
        self.kv = kv
        self.saved = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def with_tmp_path():
    td = tempfile.TemporaryDirectory()
    old = S.PATH
    S.PATH = os.path.join(td.name, "settings.json")
    return td, old


def test_url():
    print("[1] URL 归一化")
    n = S.normalize_chat_url
    check("空值", n("") == "" and n(None) == "")
    check("裸域名补全", n("https://api.deepseek.com") == "https://api.deepseek.com/chat/completions")
    check("结尾斜杠", n("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1/chat/completions")
    check("/v1 补全", n("https://api.moonshot.cn/v1") == "https://api.moonshot.cn/v1/chat/completions")
    check("完整地址原样", n("https://api.deepseek.com/chat/completions") == "https://api.deepseek.com/chat/completions")
    check("幂等", n(n("https://a.com/v1")) == "https://a.com/v1/chat/completions")


def test_mask():
    print("[2] 密钥脱敏")
    check("空值", S.mask_secret("") == "")
    check("短值", S.mask_secret("sk-123") == "sk****")
    m = S.mask_secret("sk-abcdefghijklmnop")
    check("长值保留头尾", m.startswith("sk-abc") and m.endswith("mnop") and "****" in m, m)


def test_roundtrip():
    print("[3] 读写与容错")
    td, old = with_tmp_path()
    try:
        doc = S.load()
        check("无文件返回默认", doc["providers"] == [] and doc["subject"]["name"] == "教育学")
        doc["providers"].append({"id": "srv_1", "name": "A", "base_url": "https://a.com", "api_key": "k1", "model": "m"})
        doc["active_id"] = "srv_1"
        S.save(doc)
        check("保存后可读", S.load()["providers"][0]["name"] == "A")
        with open(S.PATH, "w", encoding="utf-8") as f:
            f.write("{ broken json")
        check("损坏文件容错", S.load()["providers"] == [])
    finally:
        S.PATH = old
        td.cleanup()


def test_provider_ops():
    print("[4] 服务商增删改与默认")
    td, old = with_tmp_path()
    try:
        with Env(OPENAI_API_KEY=None):
            pid = S.upsert_provider({"name": "DeepSeek", "base_url": "https://api.deepseek.com",
                                     "model": "deepseek-v4-flash", "api_key": "sk-aaa"})
            doc = S.load()
            check("创建成功", doc["providers"][0]["base_url"] == "https://api.deepseek.com/chat/completions")
            check("首个自动设为默认", doc["active_id"] == pid)

            pid2 = S.upsert_provider({"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                                      "model": "x", "api_key": "sk-bbb"})
            S.set_active(pid2)
            check("切换默认", S.load()["active_id"] == pid2)

            # 更新：不改 key，只改名字和模型
            S.upsert_provider({"id": pid, "name": "DeepSeek 改名", "base_url": "https://api.deepseek.com",
                               "model": "deepseek-chat"})
            p1 = S._find(S.load(), pid)
            check("更新保留旧 Key", p1["api_key"] == "sk-aaa")
            check("更新生效", p1["name"] == "DeepSeek 改名" and p1["model"] == "deepseek-chat")

            S.delete_provider(pid2)
            doc = S.load()
            check("删除生效且默认回退", len(doc["providers"]) == 1 and doc["active_id"] == pid,
                  json.dumps(doc, ensure_ascii=False))
            try:
                S.upsert_provider({"name": "x", "base_url": ""})
                check("空 URL 应报错", False)
            except ValueError:
                check("空 URL 报错", True)
    finally:
        S.PATH = old
        td.cleanup()


def test_enhance():
    print("[+] 增强范围档位")
    td, old = with_tmp_path()
    try:
        cfg = S.enhance_config()
        check("默认档位=标准（L1–L3 全量）",
              cfg["scope"] == "standard" and cfg["levels"] == [1, 2, 3] and cfg["max_nodes"] == 0,
              str(cfg))
        S.set_enhance("fast")
        cfg = S.enhance_config()
        check("快速档 → 30 个上限", cfg["scope"] == "fast" and cfg["max_nodes"] == 30, str(cfg))
        S.set_enhance("full")
        cfg = S.enhance_config()
        check("全量档 → L1–L4 无上限",
              cfg["levels"] == [1, 2, 3, 4] and cfg["max_nodes"] == 0, str(cfg))
        check("档位落盘", (S.load().get("enhance") or {}).get("scope") == "full")
        try:
            S.set_enhance("bogus")
            check("非法档位被拒绝", False)
        except ValueError:
            check("非法档位被拒绝", True)
        pv = S.public_view()
        check("public_view 暴露档位与三个选项",
              pv["enhance"]["scope"] == "full" and len(pv["enhance"]["options"]) == 3
              and all(set(o) == {"id", "label", "desc"} for o in pv["enhance"]["options"]),
              str(pv.get("enhance"))[:120])
        with open(S.PATH, "w", encoding="utf-8") as f:
            json.dump({"enhance": {"scope": "weird"}}, f)
        check("未知档位回退标准（容错）", S.enhance_config()["scope"] == "standard")
    finally:
        S.PATH = old
        td.cleanup()


def test_general():
    print("[5] MinerU 与学科设置")
    td, old = with_tmp_path()
    try:
        with Env(MINERU_TOKEN=None):
            S.set_general({"mineru_token": "jwt.aaa.bbb", "subject": {"name": "教育心理学", "exam": "333"}})
            check("MinerU 已存", S.mineru_token() == "jwt.aaa.bbb")
            check("学科生效", S.subject_config() == {"name": "教育心理学", "exam": "333"})
            S.set_general({"mineru_token": ""})       # 空且未标清除 → 不修改
            check("留空不修改", S.mineru_token() == "jwt.aaa.bbb")
            S.set_general({"mineru_token": "", "clear_mineru": True})
            check("显式清除后保持未配置",
                  S.mineru_token() == "" and not S.BUILTIN_MINERU_TOKEN.strip())
            S.set_general({"mineru_token": "saved-token"})
            check("设置页 Token 优先", S.mineru_token() == "saved-token")
            S.set_general({"mineru_token": "", "clear_mineru": True})
            S.set_general({"subject": {"name": "", "exam": ""}})
            check("学科回落默认", S.subject_config()["name"] == "教育学")
        with Env(MINERU_TOKEN="env-token"):
            check("环境变量 Token 可用", S.mineru_token() == "env-token")
    finally:
        S.PATH = old
        td.cleanup()


def test_resolve_chat():
    print("[6] resolve_chat 优先级")
    td, old = with_tmp_path()
    try:
        with Env(OPENAI_API_KEY=None, OPENAI_BASE_URL=None, MODEL=None):
            c = S.resolve_chat()
            check("未配置时明确返回 None", c is None, json.dumps(c or {}))

            pid = S.upsert_provider({"name": "DeepSeek", "base_url": "https://api.deepseek.com",
                                     "model": "deepseek-v4-flash", "api_key": "sk-settings"})
            c = S.resolve_chat()
            check("统一配置可用",
                  c["source"] == "settings" and c["api_key"] == "sk-settings"
                  and c["base_url"].endswith("/chat/completions"))

            c = S.resolve_chat(api_key="sk-request")
            check("显式参数优先且沿用配置端点", c["source"] == "request"
                  and c["api_key"] == "sk-request"
                  and c["base_url"] == "https://api.deepseek.com/chat/completions", json.dumps(c))

            S.delete_provider(pid)
        with Env(OPENAI_API_KEY="sk-env", OPENAI_BASE_URL="https://openrouter.ai/api/v1", MODEL="m-env"):
            c = S.resolve_chat()
            check("环境变量模型服务可用", c and c["source"] == "env" and c["api_key"] == "sk-env"
                  and c["base_url"] == "https://openrouter.ai/api/v1/chat/completions"
                  and c["model"] == "m-env", json.dumps(c or {}))
            c = S.resolve_chat(api_key="sk-request")
            check("显式参数 > 环境变量", c["source"] == "request" and c["api_key"] == "sk-request")
            check("显式参数沿用默认端点与模型",
                  c["base_url"] == S.DEFAULT_BASE_URL
                  and c["model"] == S.DEFAULT_MODEL, json.dumps(c))
    finally:
        S.PATH = old
        td.cleanup()


def test_public_view():
    print("[7] 对外视图脱敏")
    td, old = with_tmp_path()
    try:
        with Env(OPENAI_API_KEY="sk-env-should-not-leak", MINERU_TOKEN=None):
            S.upsert_provider({"name": "DeepSeek", "base_url": "https://api.deepseek.com",
                               "model": "m", "api_key": "sk-SUPER-SECRET-KEY-123456"})
            S.set_general({"mineru_token": "eyJ-SECRET-TOKEN"})
            view = S.public_view()
            dumped = json.dumps(view, ensure_ascii=False)
            check("不含服务商明文 Key", "SUPER-SECRET" not in dumped, dumped[:200])
            check("不含 MinerU 明文", "SECRET-TOKEN" not in dumped)
            check("不含环境变量明文", "sk-env-should-not-leak" not in dumped)
            check("has_key 标记", view["providers"][0]["has_key"] is True)
            check("key_masked 形态", "****" in view["providers"][0]["key_masked"])
            check("MinerU has_token", view["mineru"]["has_token"] is True)
            check("预设列表存在", len(view["presets"]) >= 5)
            check("env 状态可见", view["env"]["has_openai_key"] is True)
            check("仓库内置凭证保持为空",
                  view["builtin"]["chat"] is False
                  and view["builtin"]["mineru"] is False
                  and not S.BUILTIN_CHAT.get("api_key")
                  and not S.BUILTIN_MINERU_TOKEN, json.dumps(view["builtin"]))
            check("public_view 不含内置明文 Key",
                  not S.BUILTIN_CHAT.get("api_key")
                  and not S.BUILTIN_MINERU_TOKEN)
    finally:
        S.PATH = old
        td.cleanup()


def test_test_functions():
    print("[8] 连接测试函数（离线路径）")
    import asyncio
    r = asyncio.run(S.test_chat("", "", ""))
    check("空参数报错", r["ok"] is False)
    r = asyncio.run(S.test_chat("http://127.0.0.1:9", "sk-x", "m"))
    check("不可达时报错不抛异常", r["ok"] is False and "error" in r, json.dumps(r))
    r = S.test_mineru("")
    check("空 Token 报错", r["ok"] is False)
    r = S.test_mineru("not-a-jwt")
    check("明显粘贴不完整的短串仍被本地拦下",
          r["ok"] is False and r.get("format_ok") is False, json.dumps(r))

    # 探测响应 → 结论 的翻译逻辑（纯函数，离线可测）
    check("401 → 鉴权失败",
          S.judge_mineru_probe(401, {"code": "A0202", "msg": "User authentication failed"})["ok"] is False)
    check("500 → 服务端异常",
          S.judge_mineru_probe(502, {}, "Bad Gateway")["ok"] is False)
    check("200 + 空参数提示 → 鉴权通过",
          S.judge_mineru_probe(200, {"code": 400, "msg": "files 不能为空"})["ok"] is True)
    check("400 参数被拒 → 鉴权通过（不误判为 Token 问题）",
          S.judge_mineru_probe(400, {"code": 400, "msg": "files不能为空"})["ok"] is True)

    # sk- 开头的新版 Token 必须能走到联网探测这一步（回归：曾经被 JWT 硬校验挡下）
    class _FakeResp:
        status_code = 401
        text = '{"code":"A0202","msg":"User authentication failed"}'

        def json(self):
            return {"code": "A0202", "msg": "User authentication failed"}

    real_post = S.httpx.post
    S.httpx.post = lambda *a, **k: _FakeResp()
    try:
        r = S.test_mineru("sk-" + "a" * 48)
        check("sk- 新形态不再被本地格式检查拦下",
              r.get("format_ok") is True and r["ok"] is False, json.dumps(r))
        r = S.test_mineru("eyJhbGciOiJIUzI1NiJ9." + "x" * 60 + ".sig")
        check("JWT 老形态同样能走到联网探测",
              r.get("format_ok") is True, json.dumps(r))
    finally:
        S.httpx.post = real_post


def test_audit_fixed():
    print("[9] 结构审校已固化（不再对外提供开关）")
    td, old = with_tmp_path()
    try:
        check("不再暴露 audit_config", not hasattr(S, "audit_config"))
        check("不再暴露 set_audit", not hasattr(S, "set_audit"))
        doc = S.load()
        doc["audit"] = {"enable_llm": False}      # 老版本存下来的「仅规则」
        S.save(doc)
        check("settings.json 里的旧开关不再出现在对外视图",
              "audit" not in S.public_view())
    finally:
        S.PATH = old
        td.cleanup()


if __name__ == "__main__":
    test_url()
    test_mask()
    test_roundtrip()
    test_provider_ops()
    test_general()
    test_enhance()
    test_resolve_chat()
    test_public_view()
    test_test_functions()
    test_audit_fixed()
    print(f"\n{'=' * 44}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)
