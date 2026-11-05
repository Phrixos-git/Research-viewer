import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import viewer


def snapshot(day, hour, games):
    stamp = f'2026-11-{day:02d}T{hour}:00:00+09:00'
    games = [dict(g, broadcasters=g.get('broadcasters',
                  {'left': g['viewers'] // 2, 'right': g['viewers'] - g['viewers'] // 2})) for g in games]
    return dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp,
                stream_count=len(games), included_stream_count=len(games), games=games)


def history():
    return [snapshot(day, hour, [
        dict(id=str(i), name=f'Game <{i}>', viewers=100 if day == 4 else 100+i*100)
        for i in range(1, 5)])
        for day in (4, 5) for hour in range(19, 24)]


class HourlyReportTests(unittest.TestCase):
    def test_html_has_three_risers_per_hour_and_keeps_average_table(self):
        records = history()
        for hour, count in ((21, 9), (23, 15)):
            page = viewer.render_html(records[5+hour-19], records)
            self.assertEqual(page.count('class="rise"'), count)
            for h in range(19, hour+1):
                self.assertIn(f'前日比較：{h}時', page)
            self.assertIn('平均同時視聴者数', page)
            self.assertIn('Game &lt;4&gt;', page)

    def test_mail_plain_html_and_attachment_share_hourly_risers(self):
        records = history()
        config = dict(username='sender@example.com', recipients=['to@example.com'])
        with tempfile.TemporaryDirectory() as td, patch('mailer.load_settings', return_value=config), patch('mailer.send') as send:
            with viewer.Store(Path(td)) as store:
                for record in records:
                    store.save(record)
                viewer.send_reports(Path(td), store, records, datetime.fromisoformat(records[-1]['scheduled_at']))
            message = send.call_args.args[1]
        plain = message.get_body(preferencelist=('plain',)).get_content()
        html = message.get_body(preferencelist=('html',)).get_content()
        attachment = next(message.iter_attachments()).get_content()
        for h in range(19, 24):
            self.assertIn(f'前日比較：{h}時', plain)
            self.assertIn(f'前日比較：{h}時', html)
        self.assertEqual(plain.count('Game <4>: +400.0人'), 5)
        self.assertEqual(html.count('class="rise"'), 15)
        self.assertEqual(html.rstrip(), attachment.rstrip())

    def test_missing_zero_threshold_tie_and_normal_hours(self):
        before = snapshot(4, 19, [dict(id=k, name=k, viewers=v) for k,v in [('zero',0),('low',100),('b',100),('a',100)]])
        after = snapshot(5, 19, [dict(id=k, name=k, viewers=v) for k,v in [('zero',999),('low',124),('b',150),('a',150),('new',999)]])
        end = snapshot(5, 21, [])
        groups = viewer.rising_groups(end, [before, after, end])
        self.assertEqual([r['id'] for r in viewer.rising_titles(groups[0][1])], ['a','b'])
        self.assertEqual(groups[1][1], [])
        page = viewer.render_html(end, [before, after, end])
        self.assertIn('比較データなし', page)
        valid_before = snapshot(4,21,[dict(id='x',name='x',viewers=100)])
        valid_after = snapshot(5,21,[dict(id='x',name='x',viewers=100)])
        self.assertIn('該当タイトルなし', viewer.render_html(valid_after,[valid_before,valid_after]))
        for hour in (19,20,22):
            s = snapshot(5,hour,[dict(id='x',name='x',viewers=200)])
            prev = snapshot(5,hour-1,[dict(id='x',name='x',viewers=100)])
            rows, label = viewer.report_rows(s, [prev, s])
            actual_label, actual_rows = viewer.rising_groups(s, [prev, s])[0]
            self.assertEqual(actual_label, label)
            self.assertEqual([(r['id'], r['before'], r['after']) for r in actual_rows],
                             [(r['id'], r['before'], r['after']) for r in rows])
