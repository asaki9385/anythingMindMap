# Offline map export: embedded tree, asset list, read-only wiring.
# Run: python test_offline_map.py
import json
import os
import re
import unittest

import server


class OfflineMapTests(unittest.TestCase):
    def _tree(self):
        return {
            "title": "离线测试",
            "children": [{
                "title": "第一章",
                "id": "n_root",
                "children": [
                    {"title": "含 </script> 的节点", "id": "n_a", "content": "<p>x</p>"}
                ]
            }]
        }

    def test_offline_html_embeds_tree_and_flags(self):
        html = server._generate_offline_html(self._tree())
        self.assertIn("<title>AnythingMindmap - Offline Mind Map</title>", html)
        self.assertIn("window.KT_OFFLINE=true", html)
        self.assertIn('id="kt-offline-tree"', html)
        self.assertNotIn("review_widget.js", html)
        self.assertNotIn("loadAllParts", html)
        self.assertIn("knowledge_map.js", html)
        self.assertIn("map/map_adapter.js", html)
        self.assertIn("vendor/mind-elixir/mind-elixir.iife.js", html)
        self.assertIn("vendor/export-mindmap/export-mindmap.umd.js", html)
        # The tree must be parseable from the embedded script, including a
        # literal </script> inside a node field (escaped as <\/script>).
        match = re.search(r'<script type="application/json" id="kt-offline-tree">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        tree = json.loads(match.group(1))
        self.assertEqual(tree["title"], "离线测试")
        self.assertEqual(tree["children"][0]["children"][0]["title"], "含 </script> 的节点")

    def test_offline_assets_exist(self):
        missing = []
        for asset in server._offline_asset_list():
            path = os.path.join(server.UI_DIR, asset)
            if not os.path.isfile(path):
                missing.append(asset)
        self.assertEqual(missing, [])

    def test_offline_html_hides_server_only_links(self):
        html = server._generate_offline_html(self._tree())
        self.assertIn('id="btnOverview" style="display:none"', html)
        self.assertIn('href="#" class="ktm-link-btn" style="display:none"', html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
