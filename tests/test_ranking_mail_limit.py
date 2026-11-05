import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import viewer


class RankingMailLimitTests(unittest.TestCase):
    def test_mail_and_html_limit_display_but_store_keeps_all_games(self):
        stamp = '2026-11-05T23:00:00+09:00'
        games = [dict(id=str(i), name=f'Game {i}', viewers=1000-i) for i in range(123)]
        snapshot = dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp,
                        games=games, stream_count=123, included_stream_count=123)
        settings = dict(username='sender@example.com',recipients=['to@example.com'])
        with tempfile.TemporaryDirectory() as td, patch('mailer.load_settings',return_value=settings), patch('mailer.send') as send:
            with viewer.Store(Path(td)) as store:
                store.save(snapshot)
                viewer.send_reports(Path(td),store,[snapshot],datetime.fromisoformat(stamp))
                self.assertEqual(len(store.history()[0]['games']),123)
            message = send.call_args.args[1]
        plain = message.get_body(preferencelist=('plain',)).get_content()
        page = message.get_body(preferencelist=('html',)).get_content()
        self.assertEqual(len(re.findall(r'^\d+\. ',plain,re.M)),100)
        self.assertNotIn('101. Game 100:',plain)
        self.assertEqual(page.count('class="rank-badge '),100)
        self.assertEqual(page.count('class="rank-change"'),100)
        self.assertIn('表示 100 / 集計 123 タイトル',page)
        self.assertEqual(next(message.iter_attachments()).get_content().rstrip(),page.rstrip())
