import re
import unittest
import viewer


def snapshot(day, ids, hour=23):
    stamp = f'2026-11-{day:02d}T{hour}:00:00+09:00'
    games = [dict(id=gid, name=f'Title {gid}', viewers=1000-i) for i,gid in enumerate(ids)]
    return dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp, games=games,
                stream_count=len(games), included_stream_count=len(games))


class RankChangeTests(unittest.TestCase):
    def test_same_rise_fall_new_match_game_ids(self):
        before = snapshot(4, ['same','fall','rise','gone'])
        after = snapshot(5, ['same','rise','fall','new'])
        self.assertEqual(viewer.ranking_changes(after,[before,after]),
                         {'same':'-', 'rise':'↑', 'fall':'↓', 'new':'★'})

    def test_missing_or_empty_day_is_new_and_uses_23_only(self):
        after = snapshot(5,['a'])
        self.assertEqual(viewer.ranking_changes(after,[after]), {'a':'★'})
        self.assertEqual(viewer.ranking_changes(after,[snapshot(4,[]),after]), {'a':'★'})
        self.assertEqual(viewer.ranking_changes(after,[snapshot(4,['a'],21),after]), {'a':'★'})
        self.assertIsNone(viewer.ranking_changes(snapshot(5,['a'],21),[after]))

    def test_previous_full_ranking_and_current_display_limit(self):
        before = snapshot(4,[str(i) for i in range(101)])
        after = snapshot(5,['100']+[str(i) for i in range(100)])
        marks = viewer.ranking_changes(after,[before,after])
        self.assertEqual(len(marks),100)
        self.assertEqual(marks['100'],'↑')
        self.assertNotIn('99',marks)
        self.assertEqual(len(before['games']),101)
        self.assertEqual(len(after['games']),101)

    def test_rightmost_column_is_only_in_23_html(self):
        before = snapshot(4,['same','fall','rise','gone'])
        after = snapshot(5,['same','rise','fall','new'])
        page = viewer.render_html(after,[before,after])
        cells = re.findall(r'<td class="rank-change"[^>]*>(.*?)</td>',page,re.S)
        self.assertEqual(len(cells),4)
        for cell,mark in zip(cells,['-','↑','↓','★']):
            self.assertIn(mark,cell)
        table = re.search(r'<table class="report-table ranking".*?</table>',page,re.S).group()
        headings = re.search(r'<thead.*?</thead>',table,re.S).group()
        self.assertTrue(re.search(r'前日比</th></tr></thead>$',headings))
        self.assertNotIn('class="rank-change"',viewer.render_html(snapshot(5,['a'],21),[before]))
