import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import viewer
from test_viewer import sample, game, collection


class ProductionStartTests(unittest.TestCase):
    def test_before_start_skips_all_side_effects_and_uses_jst(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as client, patch('viewer.publish') as publish, patch('viewer.send_reports') as send:
            root = Path(td)
            self.assertFalse(viewer.run(root, datetime.fromisoformat('2026-11-07T23:00:00+09:00')))
            self.assertFalse(viewer.run(root, datetime.fromisoformat('2026-11-07T14:00:00+00:00')))
            client.assert_not_called()
            publish.assert_not_called()
            send.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_first_official_day_excludes_test_comparisons_and_marks(self):
        old = sample('2026-11-07', 23, game('a', 100))
        first = sample('2026-11-08', 23, game('a', 200))
        second = sample('2026-11-09', 23, game('a', 300))
        history = [old, first, second]
        self.assertEqual(viewer.compare_period(history, date(2026, 11, 8), 23), [])
        self.assertEqual(viewer.report_rows(first, history)[0], [])
        self.assertEqual(viewer.ranking_changes(first, history), {'a': '★'})
        self.assertTrue(all(not rows for _, rows in viewer.rising_groups(first, history)))
        self.assertEqual(viewer.compare_period(history, date(2026, 11, 9), 23)[0]['change'], 100)
        self.assertIn('テストデータ', viewer.render_html(old, history))
        official = viewer.render_html(first, history)
        self.assertNotIn('テストデータ', official)
        self.assertIn('日本語配信', official)
        self.assertNotIn('Twitch全体の上位', official)

    def test_formal_run_preserves_test_records_without_exporting_or_retrying_them(self):
        old = sample('2026-11-07', 23, game('a', 100))
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as client, patch('mailer.load_settings', return_value=dict(username='from@example.com', recipients=['to@example.com'])), patch('mailer.send') as send:
            root = Path(td)
            with viewer.Store(root) as store:
                store.save(old)
                store.queue_mail(old['scheduled_at'], 'to@example.com')
            client.return_value.collect.return_value = collection([game('a', 200)])
            self.assertTrue(viewer.run(root, datetime.fromisoformat('2026-11-08T23:00:00+09:00'), publish_result=False))
            self.assertFalse((root / 'data/2026-11-07').exists())
            self.assertFalse((root / 'output/2026-11-07').exists())
            self.assertTrue((root / 'data/2026-11-08/23.json').exists())
            self.assertNotIn('テストデータ', (root / 'output/index.html').read_text())
            self.assertEqual(send.call_count, 1)
            plain = send.call_args.args[1].get_body(preferencelist=('plain',)).get_content()
            self.assertIn('日本語配信', plain)
            with viewer.Store(root) as store:
                self.assertEqual(len(store.history()), 2)
                self.assertEqual([r['status'] for r in store.mail_status()], ['pending', 'sent'])

    def test_test_json_does_not_block_first_official_collection(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as client:
            root = Path(td)
            path = root / 'data/2026-11-07/23.json'
            path.parent.mkdir(parents=True)
            content = json.dumps(sample('2026-11-07', 23, game('a', 100)))
            path.write_text(content)
            client.return_value.collect.return_value = collection([])
            self.assertTrue(viewer.run(root, datetime.fromisoformat('2026-11-08T19:00:00+09:00'), publish_result=False))
            self.assertEqual(path.read_text(), content)
