import re
import unittest
import viewer


class NumberBarOrderTests(unittest.TestCase):
    def test_number_precedes_bar_in_each_row(self):
        games = [dict(id=str(i),name=f'Game {i}',viewers=v)
                 for i,v in enumerate((1000000,999,1))]
        page = viewer.render_ranking(games,changes={g['id']:'-' for g in games})
        cells = re.findall(r'<div class="viewers".*?</div>',page,re.S)
        self.assertEqual(len(cells),3)
        for cell in cells:
            self.assertLess(cell.index('class="viewer-number"'),cell.index('class="bar-track"'))
            self.assertIn('flex:0 0 140px;',cell)
            self.assertIn('flex:1;min-width:0;text-align:right;',cell)
        self.assertEqual(page.count('class="rank-change"'),3)
