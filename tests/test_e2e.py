import hashlib
import json
import re
from email import policy
from email.parser import Parser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import e2e
import mailer
import viewer


class SavedDataE2ETests(unittest.TestCase):
    def test_synthetic_rises_are_visible_in_normal_hour_reports(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            result = e2e.verify_supplemental(work)
            self.assertIn('hourly_rise_display', result['cases'])
            for hour in (20, 22):
                page = (work / f'supplemental/output/2026-11-06/{hour}.html').read_text()
                cards = re.findall(r'<article\b.*?</article>', page)
                self.assertEqual(len(cards), 2)
                self.assertIn('class="rise"', cards[0])
                self.assertIn('<h3>[合成] normal</h3><strong>+400 人</strong>', cards[0])
                self.assertIn('<p>+100.0%</p>', cards[0])
                self.assertIn('除外後 200 → 400人（+100.0%）', cards[0])
                self.assertIn('class="single-rise rise"', cards[1])
                self.assertIn('<h3>[合成] single</h3><strong>+300 人</strong>', cards[1])
                self.assertIn('<p>+60.0%</p>', cards[1])
                self.assertIn('除外後 100 → 100人（+0.0%）', cards[1])
            for hour in (21, 23):
                page = (work / f'supplemental/output/2026-11-06/{hour}.html').read_text()
                self.assertNotIn('<article', page)
            message = Parser(policy=policy.default).parsestr((work / 'supplemental/message.eml').read_text())
            plain = message.get_body(preferencelist=('plain',)).get_content()
            for hour in range(19, 24):
                section = plain.split(f'前日比較：{hour}時\n')[1].split('前日比較：')[0]
                self.assertIn('[合成] normal:', section)
                self.assertIn('[合成] single:', section)

    def test_supplemental_rejects_missing_rise_cards(self):
        render = viewer.render_html

        def without_cards(snapshot, history):
            return re.sub(r'<article\b.*?</article>', '', render(snapshot, history))

        with tempfile.TemporaryDirectory() as td, patch('viewer.render_html', side_effect=without_cards):
            with self.assertRaisesRegex(viewer.ViewerError, '急上昇カード'):
                e2e.verify_supplemental(Path(td))

    def test_saved_test_data_pipeline_does_not_send_or_touch_source(self):
        sources = list((viewer.ROOT / 'data').glob('*/*.json'))
        hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send, patch('viewer.Client') as client:
            result = e2e.run_e2e(viewer.ROOT, work_parent=Path(td))
            send.assert_not_called()
            client.assert_not_called()
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['mail_status'], 'not_sent')
            self.assertEqual(result['recipient'], 'kurahasb@gmail.com')
            self.assertGreaterEqual(result['snapshots'], 10)
            self.assertEqual(result['supplemental']['snapshots'], 10)
            self.assertIn('zero_baseline', result['supplemental']['cases'])
            self.assertIn('audience_categories', result['supplemental']['cases'])
            work = Path(result['workdir'])
            synthetic = json.loads((work / 'supplemental/data/2026-11-05/23.json').read_text())
            self.assertTrue(all(g['name'].startswith('[合成]') for g in synthetic['games']))
            self.assertEqual(work.stat().st_mode & 0o777, 0o700)
            self.assertEqual((work / 'message.eml').stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((work / 'result.json').read_text())['status'], 'passed')
            message = Parser(policy=policy.default).parsestr((work / 'message.eml').read_text())
            self.assertIn('[E2Eテスト]', str(message['Subject']))
        self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in hashes.items()))

    def test_live_mode_sends_once_to_fixed_recipient_after_validation(self):
        settings = dict(username='sender@example.com', app_password='do-not-log', recipients=['other@example.com'])
        with tempfile.TemporaryDirectory() as td, patch('mailer.load_settings', return_value=settings), patch('mailer.send') as send:
            result = e2e.run_e2e(viewer.ROOT, send_email=True, work_parent=Path(td))
            self.assertEqual(result['mail_status'], 'sent')
            send.assert_called_once()
            config, message = send.call_args.args
            self.assertEqual(config['recipients'], ['kurahasb@gmail.com'])
            self.assertEqual(str(message['To']), 'kurahasb@gmail.com')
            self.assertIn('[E2Eテスト]', str(message['Subject']))
            self.assertIn('E2Eテスト', message.get_body(preferencelist=('plain',)).get_content())
            self.assertNotIn('do-not-log', (Path(result['workdir']) / 'result.json').read_text())
            self.assertEqual(settings['recipients'], ['other@example.com'])

    def test_missing_or_corrupt_test_data_never_sends(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            with self.assertRaises(viewer.ViewerError):
                e2e.run_e2e(root)
            path = root / 'data/2026-11-07/23.json'
            path.parent.mkdir(parents=True)
            path.write_text('broken')
            with self.assertRaises(viewer.ViewerError):
                e2e.run_e2e(root)
            send.assert_not_called()

    def test_uncertain_smtp_delivery_is_recorded_without_retry(self):
        settings = dict(username='sender@example.com', app_password='do-not-log', recipients=['other@example.com'])
        with tempfile.TemporaryDirectory() as td, patch('mailer.load_settings', return_value=settings), patch('mailer.send', side_effect=mailer.MailError('unknown', uncertain=True)) as send:
            with self.assertRaises(viewer.ViewerError):
                e2e.run_e2e(viewer.ROOT, send_email=True, work_parent=Path(td))
            send.assert_called_once()
            result = json.loads(next(Path(td).glob('e2e-*/result.json')).read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['mail_status'], 'uncertain')

    def test_future_formal_data_is_not_used(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            path = root / 'data/2026-11-08/23.json'
            path.parent.mkdir(parents=True)
            path.write_text('not valid JSON, but outside test range')
            with self.assertRaises(viewer.ViewerError):
                e2e.run_e2e(root)
            send.assert_not_called()
