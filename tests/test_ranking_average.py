import re
import unittest

import viewer
from test_hourly_reports import snapshot


class RankingAverageTests(unittest.TestCase):
    def test_average_uses_matched_hours_and_ids_in_penultimate_column(self):
        records = []
        for hour in range(19, 24):
            for day in (4, 5):
                values = [('gain', 100 if day == 4 else 100 + (hour - 18) * 10),
                          ('loss', 100 if day == 4 else 50), ('zero', 0)]
                if hour in (19, 23):
                    values.append(('partial', 10 if day == 4 else 11 + (hour == 23)))
                if day == 5:
                    values.append(('new', 99))
                records.append(snapshot(day, hour, [dict(id=k, name='Same <name>', viewers=v)
                                                   for k, v in values]))
        page = viewer.render_html(records[-1], records)
        table = re.search(r'<table class="report-table ranking".*?</table>', page, re.S).group()
        headings = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', table, re.S)
        self.assertEqual(headings[-2:], ['19〜23時平均の前日増減（人）', '前日比'])
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S)[1:]
        for row, expected, samples in zip(rows, ['+30.0', '-50.0', '+0.0', '+1.5', '比較データなし'], [5, 5, 5, 2, None]):
            cells = re.findall(r'<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>', row, re.S)
            self.assertEqual(len(cells), 5)
            self.assertIn(expected, cells[-2])
            if samples is not None:
                self.assertIn(f'{samples}/5', cells[-2])
        self.assertIn('部分集計', page)
        self.assertIn('Same &lt;name&gt;', table)

    def test_missing_history_and_other_hours(self):
        for hour in (19, 20, 21, 22, 23):
            current = snapshot(5, hour, [dict(id='a', name='A', viewers=100)])
            page = viewer.render_html(current, [current])
            table = re.search(r'<table class="report-table ranking".*?</table>', page, re.S).group()
            if hour == 23:
                self.assertIn('19〜23時平均の前日増減（人）', table)
                self.assertIn('比較データなし', table)
            else:
                self.assertNotIn('19〜23時平均の前日増減（人）', table)
