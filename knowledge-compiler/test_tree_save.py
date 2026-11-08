# /api/tree/{key} + /api/tree/save regression tests.
# Run: python test_tree_save.py
import json
import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

import server


class TreeSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old = {
            "enhanced": server.ENHANCED_DIR,
            "temp": server.TEMP_DIR,
            "cache": server.CACHE_DIR,
            "review": getattr(server, "REVIEW_DIR", None),
        }
        cls.root = tempfile.mkdtemp(prefix="kt-tree-save-")
        server.ENHANCED_DIR = os.path.join(cls.root, "enhanced")
        server.TEMP_DIR = os.path.join(cls.root, "temp")
        server.CACHE_DIR = os.path.join(cls.root, "cache")
        server.REVIEW_DIR = os.path.join(cls.root, "review")
        for path in (server.ENHANCED_DIR, server.TEMP_DIR, server.CACHE_DIR, server.REVIEW_DIR):
            os.makedirs(path, exist_ok=True)
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        server.ENHANCED_DIR = cls._old["enhanced"]
        server.TEMP_DIR = cls._old["temp"]
        server.CACHE_DIR = cls._old["cache"]
        if cls._old["review"] is not None:
            server.REVIEW_DIR = cls._old["review"]
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        for name in os.listdir(server.ENHANCED_DIR):
            path = os.path.join(server.ENHANCED_DIR, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)

    # ── helpers ──────────────────────────────────────────────
    def _tree(self, with_ids=False):
        node = {
            "title": "第一章",
            "summary": "摘要",
            "content": "<p>正文</p>",
            "custom_field": {"keep": True},
            "children": [
                {"title": "第一节", "children": []},
                {"title": "第二节", "children": []},
            ],
        }
        root = {"title": "测试书", "children": [node]}
        if with_ids:
            def add_ids(n, prefix):
                n["id"] = prefix
                for i, child in enumerate(n.get("children", [])):
                    add_ids(child, prefix + "_" + str(i))
            add_ids(root, "n_root")
        return root

    def _write(self, name, tree):
        path = os.path.join(server.ENHANCED_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False)
        return path

    def _get(self, key):
        return self.client.get("/api/tree/" + key)

    def _save(self, key, base_rev, tree):
        return self.client.post("/api/tree/save", json={"key": key, "base_rev": base_rev, "tree": tree})

    # ── tests ────────────────────────────────────────────────
    def test_read_tree_returns_rev_and_count(self):
        self._write("book.json", self._tree())
        res = self._get("book.json")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["key"], "book.json")
        self.assertEqual(body["title"], "测试书")
        self.assertEqual(body["node_count"], 4)
        self.assertTrue(body["rev"])
        self.assertFalse(body["readonly"])

    def test_first_save_adds_ids_preserves_unknown_root_fields(self):
        self._write("book.json", self._tree())
        body = self._get("book.json").json()
        incoming = self._tree(with_ids=True)
        res = self._save("book.json", body["rev"], incoming)
        self.assertEqual(res.status_code, 200, res.text)
        saved = res.json()
        self.assertTrue(saved["ok"])
        self.assertNotEqual(saved["rev"], body["rev"])
        self.assertEqual(saved["node_count"], 4)
        on_disk = json.load(open(os.path.join(server.ENHANCED_DIR, "book.json"), encoding="utf-8"))
        self.assertEqual(on_disk["title"], "测试书")
        self.assertEqual(on_disk["children"][0]["id"], "n_root_0")
        self.assertTrue(on_disk["children"][0]["custom_field"]["keep"])
        backups = os.listdir(os.path.join(server.ENHANCED_DIR, ".backups"))
        self.assertTrue(any(name.startswith("book_") for name in backups), backups)

    def test_conflict_writes_nothing(self):
        self._write("book.json", self._tree())
        body = self._get("book.json").json()
        before = open(os.path.join(server.ENHANCED_DIR, "book.json"), encoding="utf-8").read()
        incoming = self._tree(with_ids=True)
        res = self._save("book.json", "deadbeef", incoming)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "conflict")
        after = open(os.path.join(server.ENHANCED_DIR, "book.json"), encoding="utf-8").read()
        self.assertEqual(before, after)

    def test_root_title_change_is_rejected(self):
        self._write("book.json", self._tree())
        body = self._get("book.json").json()
        incoming = self._tree(with_ids=True)
        incoming["title"] = "另一个名字"
        res = self._save("book.json", body["rev"], incoming)
        self.assertEqual(res.status_code, 400)
        self.assertIn("根标题", res.json()["error"])

    def test_invalid_or_duplicate_id_is_rejected(self):
        self._write("book.json", self._tree())
        body = self._get("book.json").json()
        incoming = self._tree(with_ids=True)
        incoming["children"][0]["children"][0]["id"] = "bad id!"
        res = self._save("book.json", body["rev"], incoming)
        self.assertEqual(res.status_code, 400)
        self.assertIn("id", res.json()["error"])

        incoming = self._tree(with_ids=True)
        incoming["children"][0]["children"][1]["id"] = incoming["children"][0]["children"][0]["id"]
        res = self._save("book.json", body["rev"], incoming)
        self.assertEqual(res.status_code, 400)
        self.assertIn("重复", res.json()["error"])

    def test_merge_keeps_unknown_fields_and_applies_edits(self):
        self._write("book.json", self._tree(with_ids=True))
        body = self._get("book.json").json()
        incoming = self._tree(with_ids=True)
        incoming["children"][0]["title"] = "第一章 改名"
        incoming["children"][0]["children"] = [incoming["children"][0]["children"][0]]
        incoming["children"][0]["children"][0]["title"] = "第一节 改名"
        incoming["children"][0]["children"].append({
            "id": "n_new", "title": "新节点", "children": []
        })
        res = self._save("book.json", body["rev"], incoming)
        self.assertEqual(res.status_code, 200, res.text)
        saved = json.load(open(os.path.join(server.ENHANCED_DIR, "book.json"), encoding="utf-8"))
        chapter = saved["children"][0]
        self.assertEqual(chapter["title"], "第一章 改名")
        self.assertEqual(chapter["custom_field"]["keep"], True)
        self.assertEqual(chapter["content"], "<p>正文</p>")
        self.assertEqual(len(chapter["children"]), 2)
        self.assertEqual(chapter["children"][0]["title"], "第一节 改名")
        self.assertEqual(chapter["children"][1]["title"], "新节点")
        self.assertEqual(res.json()["changes"]["removed"], 1)
        self.assertEqual(res.json()["changes"]["added"], 1)

    def test_missing_base_rev_and_missing_key(self):
        self._write("book.json", self._tree())
        res = self.client.post("/api/tree/save", json={"key": "book.json", "tree": self._tree(with_ids=True)})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/tree/save", json={"key": "missing.json", "base_rev": "x", "tree": self._tree(with_ids=True)})
        self.assertEqual(res.status_code, 404)

    def test_task_tree_save_updates_state_and_backup(self):
        task_id = "testsave1"
        work = os.path.join(server.TEMP_DIR, task_id)
        os.makedirs(os.path.join(work, "json"), exist_ok=True)
        tree = self._tree(with_ids=True)
        with open(os.path.join(work, "json", "knowledge_tree.json"), "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False)
        with open(os.path.join(work, "task_state.json"), "w", encoding="utf-8") as f:
            json.dump({"task_id": task_id, "title": "测试书", "status": "done", "cache_key": ""}, f)
        body = self._get("task_" + task_id).json()
        incoming = self._tree(with_ids=True)
        incoming["children"][0]["title"] = "第一章 任务改名"
        res = self._save("task_" + task_id, body["rev"], incoming)
        self.assertEqual(res.status_code, 200, res.text)
        state = json.load(open(os.path.join(work, "task_state.json"), encoding="utf-8"))
        self.assertTrue(state.get("tree_edited"))
        self.assertEqual(state.get("tree_rev"), res.json()["rev"])
        self.assertTrue(os.path.isdir(os.path.join(work, "json", "backups")))
        saved = json.load(open(os.path.join(work, "json", "knowledge_tree.json"), encoding="utf-8"))
        self.assertEqual(saved["children"][0]["title"], "第一章 任务改名")

    def test_path_traversal_key_is_not_resolved(self):
        res = self._get("../secret.json")
        self.assertIn(res.status_code, (404, 400))


if __name__ == "__main__":
    unittest.main(verbosity=2)
