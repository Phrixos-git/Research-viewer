import re
import unittest

import viewer
from test_hourly_reports import snapshot


def comparison_names(page):
    table = re.search(r'<table class="report-table comparison".*?</table>', page, re.S).group()
    return re.findall(r'<th scope="row"[^>]*>(.*?)</th>', table)


class ComparisonTop10Tests(unittest.TestCase):
    def test_comparison_labels_include_actual_dates(self):
        games = [dict(id='a', name='Game', viewers=100)]
        for previous_stamp, current_stamp, before, after in [
            ('2026-11-06T21:00:00+09:00', '2026-11-07T21:00:00+09:00', '11/6', '11/7'),
            ('2026-11-06T23:00:00+09:00', '2026-11-07T23:00:00+09:00', '11/6', '11/7'),
            ('2026-12-31T23:00:00+09:00', '2027-01-01T23:00:00+09:00', '12/31', '1/1'),
            ('2026-11-07T19:00:00+09:00', '2026-11-07T20:00:00+09:00', '11/7', '11/7'),
        ]:
            with self.subTest(current=current_stamp):
                old, current = snapshot(6, 23, games), snapshot(7, 23, games)
                old['scheduled_at'], current['scheduled_at'] = previous_stamp, current_stamp
                page = viewer.render_html(current, [old, current])
                for label in [f'前回（{before}）', f'今回（{after}）']:
                    self.assertIn(f'>{label}</th>', page)
                    self.assertIn(f'>{label}：</span>', page)
                self.assertNotIn('>比較元</th>', page)

    def test_23_top10_by_percent_keeps_full_ranking_averages(self):
        before = [dict(id=str(i), name=f'Game {i}', viewers=100) for i in range(12)]
        after = [dict(g, viewers=100 + i * 10) for i, g in enumerate(before)]
        # Biggest increase in people is below the Top10 by percentage.
        before += [dict(id='large', name='Large', viewers=100000),
                   dict(id='zero', name='Zero', viewers=0)]
        after += [dict(id='large', name='Large', viewers=101000),
                  dict(id='zero', name='Zero', viewers=10000)]
        old, current = snapshot(4, 23, before), snapshot(5, 23, after)
        page = viewer.render_html(current, [old, current])
        self.assertEqual(comparison_names(page), [f'Game {i}' for i in range(11, 1, -1)])
        self.assertIn('増加率順 Top10', page)
        ranking = re.search(r'<table class="report-table ranking".*?</table>', page, re.S).group()
        self.assertEqual(ranking.count('class="average-change"'), 14)
        self.assertIn('+1,000.0', ranking)
        self.assertIn('+10,000.0', ranking)
        # The 21:00 table still includes all comparison rows.
        old['scheduled_at'] = '2026-11-04T21:00:00+09:00'
        current['scheduled_at'] = '2026-11-05T21:00:00+09:00'
        self.assertEqual(len(comparison_names(viewer.render_html(current, [old, current]))), 14)

    def test_ties_small_sets_and_undefined_rates(self):
        before = [dict(id=k, name=k, viewers=v) for k, v in [('b', 100), ('a', 100), ('down', 100), ('zero', 0)]]
        after = [dict(g, viewers=v) for g, v in zip(before, [150, 150, 50, 300])]
        old, current = snapshot(4, 23, before), snapshot(5, 23, after)
        self.assertEqual(comparison_names(viewer.render_html(current, [old, current])), ['a', 'b', 'down'])
        old, current = snapshot(4, 23, [before[-1]]), snapshot(5, 23, [after[-1]])
        page = viewer.render_html(current, [old, current])
        self.assertIn('増加率を算出できる比較データなし', page)
        self.assertIn('class="average-change"', page)
