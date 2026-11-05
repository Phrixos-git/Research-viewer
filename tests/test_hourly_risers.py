import unittest
import viewer

class HourlyRisersTests(unittest.TestCase):
    def test_hours_are_separate(self):
        history = []
        for day in (4, 5):
            for hour in range(19, 24):
                stamp = f'2026-11-{day:02d}T{hour}:00:00+09:00'
                games = [dict(id=str(i), name=f'Game {i}', viewers=100 if day == 4 else 100 + i * 100) for i in range(1, 5)]
                for g in games:
                    g['broadcasters'] = {'left': g['viewers'] // 2, 'right': g['viewers'] - g['viewers'] // 2}
                history.append(dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp, stream_count=4, included_stream_count=4, games=games))
        for end_hour, expected in ((21, 3), (23, 5)):
            groups = viewer.rising_groups(history[5 + end_hour - 19], history)
            self.assertEqual(len(groups), expected)
            for hour, (label, rows) in zip(range(19, end_hour + 1), groups):
                self.assertEqual(label, f'前日比較：{hour}時')
                self.assertEqual([r['id'] for r in viewer.rising_titles(rows)], ['4', '3', '2'])
