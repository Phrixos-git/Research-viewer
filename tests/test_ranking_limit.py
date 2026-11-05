import unittest
import viewer


class RankingLimitTests(unittest.TestCase):
    def test_ranking_shows_top_100_without_mutating_input(self):
        games = [dict(id=str(i), name=f'Title {i}', viewers=200-i) for i in range(123)]
        original = [dict(g) for g in games]
        page = viewer.render_ranking(games)
        self.assertEqual(page.count('class="rank-badge '), 100)
        self.assertIn('class="rank-badge rank-100"', page)
        self.assertNotIn('class="rank-badge rank-101"', page)
        self.assertIn('>Title 99</th>', page)
        self.assertNotIn('>Title 100</th>', page)
        self.assertEqual(games, original)
