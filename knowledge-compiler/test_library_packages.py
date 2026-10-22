"""
资料库管理回归测试：收藏、数据包导出/导入、删除。
使用临时目录，不触碰真实的 data/ 资源。
"""
import io
import json
import os
import tempfile
import types
import zipfile

from fastapi.testclient import TestClient

import server

pass_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print("  PASS " + name)
    else:
        fail_count += 1
        print("  FAIL " + name + (" | " + str(detail) if detail else ""))


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    with tempfile.TemporaryDirectory() as td:
        server.ENHANCED_DIR = os.path.join(td, "enhanced")
        server.TEMP_DIR = os.path.join(td, "tasks")
        server.REVIEW_DIR = os.path.join(td, "review")
        server.FAVORITES_PATH = os.path.join(td, "favorites.json")
        for path in (server.ENHANCED_DIR, server.TEMP_DIR, server.REVIEW_DIR):
            os.makedirs(path, exist_ok=True)
        server.tasks.clear()

        key = "demo_test.json"
        tree = {
            "title": "测试资料",
            "original_files": ["测试.pdf"],
            "children": [
                {"title": "第一章", "level": 1, "content": "若 $x>0$，则 $x^2>0$。", "children": []}
            ],
        }
        write_json(os.path.join(server.ENHANCED_DIR, key), tree)

        review_dir = os.path.join(server.REVIEW_DIR, server._review_storage_key(key))
        write_json(os.path.join(review_dir, "progress.json"), {
            "file_key": key,
            "units": {"u1": {"passes": 1, "lapses": 0, "next_due": 0}},
            "retest_queue": [],
            "weak_units": [],
            "sessions": [],
        })

        client = TestClient(server.app)

        print("[A] 收藏")
        res = client.get("/api/favorites")
        check("初始收藏为空", res.status_code == 200 and res.json().get("keys") == [])
        res = client.post("/api/favorites", json={"key": key, "favorite": True})
        check("收藏成功", res.status_code == 200 and key in res.json().get("keys", []), res.text)
        check("收藏持久化", key in client.get("/api/favorites").json().get("keys", []))
        res = client.post("/api/favorites", json={"key": key, "favorite": False})
        check("取消收藏", res.status_code == 200 and key not in res.json().get("keys", []))
        client.post("/api/favorites", json={"key": key, "favorite": True})

        print("[B] 数据包导出")
        res = client.get("/api/package/" + key)
        check("导出接口返回 ZIP", res.status_code == 200 and res.content[:2] == b"PK", res.status_code)
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            names = set(zf.namelist())
            manifest = json.loads(zf.read("manifest.json"))
            exported_tree = json.loads(zf.read("tree.json"))
        check("包含 manifest/tree/README",
            {"manifest.json", "tree.json", "README.txt"}.issubset(names), sorted(names))
        check("manifest 标识与元数据",
            manifest.get("format") == "anythingmindmap.package"
            and manifest.get("resource", {}).get("title") == "测试资料"
            and manifest.get("resource", {}).get("favorite") is True,
            manifest)
        check("导出树内容完整", exported_tree.get("title") == "测试资料"
            and exported_tree.get("original_files") == ["测试.pdf"])
        check("导出包含复习进度", "review_progress.json" in names, sorted(names))

        print("[C] 数据包导入")
        res = client.post("/api/import-package", files={"file": ("pkg.zip", res.content, "application/zip")})
        imported = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        check("导入接口成功", res.status_code == 200 and imported.get("ok") is True, res.text[:300])
        new_key = imported.get("key", "")
        check("生成新的安全资源 key", bool(new_key) and new_key.endswith(".json") and new_key != key, new_key)
        check("导入文件落盘", bool(new_key) and os.path.isfile(os.path.join(server.ENHANCED_DIR, new_key)))
        check("导入保留标题与节点数", imported.get("title") == "测试资料" and imported.get("node_count") == 2,
            imported)
        check("导入复习进度", imported.get("review_progress") is True, imported)
        check("导入保留收藏状态", new_key in client.get("/api/favorites").json().get("keys", []))

        raw_payload = b"\xef\xbb\xbf" + json.dumps(tree, ensure_ascii=False).encode("utf-8")
        raw_res = client.post("/api/import-package",
                              files={"file": ("tree.json", raw_payload, "application/json")})
        raw = raw_res.json() if raw_res.status_code == 200 else {}
        check("支持直接导入单棵 JSON 树", raw_res.status_code == 200 and raw.get("ok") is True, raw_res.text[:300])

        bad_res = client.post("/api/import-package",
                              files={"file": ("bad.zip", b"not a zip", "application/zip")})
        check("拒绝损坏的 ZIP", bad_res.status_code == 400, bad_res.text[:200])

        print("[D] 删除资料")
        res = client.post("/api/delete_resource", json={"key": key})
        check("删除已完成资料", res.status_code == 200 and res.json().get("ok") is True, res.text[:300])
        check("知识树文件已删除", not os.path.isfile(os.path.join(server.ENHANCED_DIR, key)))
        check("收藏同步移除", key not in client.get("/api/favorites").json().get("keys", []))
        check("复习进度同步删除", not os.path.isdir(review_dir))

        res = client.post("/api/delete_resource", json={"key": new_key})
        check("删除导入资料", res.status_code == 200)

        task_id = "live_task"
        task_tree_path = os.path.join(server.TEMP_DIR, task_id, "json", "knowledge_tree.json")
        write_json(task_tree_path, tree)
        write_json(os.path.join(server.TEMP_DIR, task_id, "report.json"), {"nodes": {"total": 2}})
        res = client.get("/api/package/task_" + task_id)
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            task_names = set(zf.namelist())
        check("任务数据包包含解析报告", res.status_code == 200 and "report.json" in task_names, sorted(task_names))
        server.tasks[task_id] = types.SimpleNamespace(status="processing")
        res = client.post("/api/delete_resource", json={"key": "task_" + task_id})
        check("处理中任务拒绝删除", res.status_code == 409, res.text[:200])
        server.tasks.clear()
        res = client.post("/api/delete_resource", json={"key": "task_" + task_id})
        check("中断后的任务可删除", res.status_code == 200
            and not os.path.isdir(os.path.join(server.TEMP_DIR, task_id)), res.text[:200])

        res = client.post("/api/delete_resource", json={"key": "missing.json"})
        check("不存在资料返回 404", res.status_code == 404, res.text[:200])

    print("\n" + "=" * 44)
    print(f"PASS: {pass_count}  FAIL: {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
