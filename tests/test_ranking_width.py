import re
import unittest

import viewer


class TestRankingTableWidth(unittest.TestCase):
    def test_ranking_table_uses_auto_layout_and_escapes_names(self):
        snapshot = {
            "scheduled_at": "2026-11-05T23:00:00+09:00",
            "started_at": "2026-11-05T23:00:00+09:00",
            "finished_at": "2026-11-05T23:00:00+09:00",
            "stream_count": 2,
            "included_stream_count": 2,
            "games": [
                {"id": "a", "name": "短名", "viewers": 123},
                {"id": "b", "name": "長いタイトル<&>", "viewers": 4567},
            ],
        }
        page = viewer.render_html(snapshot, history=[snapshot])

        table_match = re.search(
            r'<table[^>]*class="report-table ranking"[^>]*>', page
        )
        self.assertIsNotNone(table_match)
        tag = table_match.group(0)

        self.assertIn("max-width:100%", tag)
        self.assertIn("table-layout:auto", tag)
        self.assertNotIn('width="100%"', tag)
        self.assertNotIn("table-layout:fixed", tag)

        self.assertIn("長いタイトル&lt;&amp;&gt;", page)
        self.assertIn("4,567", page)


if __name__ == "__main__":
    unittest.main()
