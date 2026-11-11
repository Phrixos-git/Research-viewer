import json
import re
from html.parser import HTMLParser
import smtplib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import mailer
import viewer


def settings():
    return dict(enabled=True, username='sender@gmail.com', app_password='private-app-password',
                recipients=['one@example.com', 'two@example.com'])


def configure(root):
    path = root / '.local/mail.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings()))


def sample(hour=23):
    stamp = f'2026-11-11T{hour:02d}:00:00+09:00'
    return dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp,
                games=[dict(id='a', name='ゲームA', viewers=100, broadcasters={'left': 50, 'right': 50})],
                stream_count=2, included_stream_count=2)


class MailSettingsTests(unittest.TestCase):
    def test_missing_or_disabled_settings_do_not_connect(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertIsNone(mailer.load_settings(root))
            configure(root)
            path = root / '.local/mail.json'
            path.write_text(json.dumps(dict(enabled=False)))
            self.assertIsNone(mailer.load_settings(root))

    def test_multiple_recipients_are_normalized_and_deduplicated(self):
        config = mailer.validate_settings(dict(settings(), recipients='one@example.com, two@example.com\none@example.com'))
        self.assertEqual(config['recipients'], settings()['recipients'])

    def test_invalid_addresses_and_header_injection_are_rejected(self):
        for recipients in [[], ['bad'], ['a@example.com\r\nBcc: spy@example.com']]:
            with self.subTest(recipients=recipients), self.assertRaises(mailer.MailError):
                mailer.validate_settings(dict(settings(), recipients=recipients))


class MessageTests(unittest.TestCase):
    def test_html_is_inline_and_attached_without_exposing_other_recipients(self):
        message = mailer.make_message('sender@gmail.com', 'one@example.com', sample()['scheduled_at'],
                                      '比較データなし', '<html>ゲームA</html>')
        self.assertEqual(message['To'], 'one@example.com')
        self.assertNotIn('two@example.com', message.as_string())
        self.assertIsNone(message['Bcc'])
        self.assertEqual(message.get_body(preferencelist=('html',)).get_content().strip(), '<html>ゲームA</html>')
        files = list(message.iter_attachments())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].get_content().strip(), '<html>ゲームA</html>')
        self.assertEqual(files[0].get_filename(), 'twitch-2026-11-11-23.html')

    def test_report_remains_dark_when_mail_client_removes_head_styles(self):
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = []
            def handle_starttag(self, tag, attrs):
                self.elements.append((tag, dict(attrs)))
        snapshot = sample()
        report = viewer.render_html(snapshot, [snapshot])
        msg = mailer.make_message('sender@gmail.com', 'one@example.com', snapshot['scheduled_at'], 'text', report)
        parts = [msg.get_body(preferencelist=('html',)), *msg.iter_attachments()]
        for part in parts:
            parser = Tags()
            parser.feed(re.sub(r'<style[^>]*>.*?</style>', '', part.get_content(), flags=re.S))
            for tag in ['body', 'table', 'td']:
                attributes = next(attrs for name, attrs in parser.elements if name == tag)
                self.assertEqual(attributes.get('bgcolor'), viewer.BG)
                self.assertIn(f'background-color:{viewer.BG}', attributes.get('style', ''))
                self.assertIn(f'color:{viewer.INK}', attributes.get('style', ''))
            self.assertIn(('table', 'presentation'), [(tag, attrs.get('role')) for tag, attrs in parser.elements])

    def test_gmail_uses_verified_tls_and_single_recipient_envelope(self):
        msg = mailer.make_message('sender@gmail.com', 'one@example.com', sample()['scheduled_at'], 'text', '<p>HTML</p>')
        with patch('mailer.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.send_message.return_value = {}
            mailer.send(settings(), msg)
            args, kwargs = smtp.call_args
            self.assertEqual(args, ('smtp.gmail.com', 465))
            self.assertTrue(kwargs['context'].check_hostname)
            smtp.return_value.login.assert_called_once_with('sender@gmail.com', 'private-app-password')
            self.assertEqual(smtp.return_value.send_message.call_args.kwargs['to_addrs'], ['one@example.com'])

    def test_responsive_report_preserves_data_and_inline_widths_in_mail_parts(self):
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = []
                self.cells = []
                self.in_report = False
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                self.elements.append((tag, attrs))
                if tag == 'table':
                    self.in_report = 'report-table' in attrs.get('class', '').split()
                if self.in_report and tag in ('th', 'td') and attrs.get('scope') != 'col':
                    self.cells.append(attrs)
            def handle_endtag(self, tag):
                if tag == 'table':
                    self.in_report = False

        snapshot = sample()
        snapshot['games'][0]['name'] = 'LongGameTitle' * 20 + '<&>'
        previous = sample()
        previous['scheduled_at'] = '2026-11-10T23:00:00+09:00'
        previous['games'][0]['viewers'] = 50
        previous['games'][0]['broadcasters'] = {'left': 25, 'right': 25}
        report = viewer.render_html(snapshot, [previous, snapshot])
        message = mailer.make_message('sender@gmail.com', 'one@example.com', snapshot['scheduled_at'], 'text', report)
        for part in [message.get_body(preferencelist=('html',)), *message.iter_attachments()]:
            content = part.get_content()
            self.assertIn('@media screen and (max-width:600px)', content)
            self.assertIn('.comparison td', content)
            self.assertIn('.mobile-label', content)
            parser = Tags()
            parser.feed(re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.S))
            tables = [attrs for tag, attrs in parser.elements if tag == 'table']
            self.assertEqual(len(tables), 3)  # Outer layout, comparison, ranking.
            for attrs in tables:
                if 'ranking' in attrs.get('class', '').split():
                    self.assertNotIn('width', attrs)
                    self.assertIn('max-width:100%', attrs['style'])
                    self.assertIn('table-layout:auto', attrs['style'])
                else:
                    self.assertEqual(attrs['width'], '100%')
                    self.assertIn('table-layout:fixed', attrs['style'])
            self.assertEqual(len(parser.cells), 11)  # Six comparison cells and five ranking cells.
            for attrs in parser.cells:
                styles = dict(part.split(':', 1) for part in attrs['style'].split(';') if ':' in part)
                if attrs.get('scope') == 'row':
                    # Titles wrap anywhere so long names never widen the table.
                    self.assertEqual(styles['white-space'], 'normal')
                    self.assertEqual(styles['word-wrap'], 'break-word')
                    self.assertEqual(styles['overflow-wrap'], 'anywhere')
                else:
                    # Ranks and numbers stay on one line so digits are never split.
                    self.assertEqual(styles['white-space'], 'nowrap')
                    self.assertEqual(styles['overflow-wrap'], 'normal')
            for label in ['前回（11/10）', '今回（11/11）', '増減人数', '増減率', '比較件数／予定件数']:
                self.assertIn(label + '：', content)
            self.assertIn('LongGameTitle' * 20 + '&lt;&amp;&gt;', content)
            self.assertIn('+100.0%', content)

    def test_auth_failure_and_interrupted_send_are_distinguished_without_secret_logging(self):
        msg = mailer.make_message('sender@gmail.com', 'one@example.com', sample()['scheduled_at'], 'text', '<p>HTML</p>')
        for stage, uncertain in [('login', False), ('send_message', True)]:
            with self.subTest(stage=stage), patch('mailer.smtplib.SMTP_SSL') as smtp:
                getattr(smtp.return_value, stage).side_effect = smtplib.SMTPServerDisconnected('private-app-password')
                with self.assertRaises(mailer.MailError) as caught:
                    mailer.send(settings(), msg)
                self.assertEqual(caught.exception.uncertain, uncertain)
                self.assertNotIn('private-app-password', str(caught.exception))

    def test_quit_failure_after_acceptance_does_not_cause_resend(self):
        msg = mailer.make_message('sender@gmail.com', 'one@example.com', sample()['scheduled_at'], 'text', '<p>HTML</p>')
        for stage in ['quit', 'close']:
            with self.subTest(stage=stage), patch('mailer.smtplib.SMTP_SSL') as smtp:
                smtp.return_value.send_message.return_value = {}
                getattr(smtp.return_value, stage).side_effect = smtplib.SMTPServerDisconnected('closed')
                mailer.send(settings(), msg)


class DeliveryTests(unittest.TestCase):
    def run_at(self, root, hour):
        with patch('viewer.client_from_config') as factory:
            factory.return_value.collect.return_value = {k: sample()[k] for k in ('games', 'stream_count', 'included_stream_count')}
            viewer.run(root, datetime.fromisoformat(sample(hour)['scheduled_at']), publish_result=False)

    def test_only_23_report_is_sent_once_per_recipient_and_attaches_actual_output(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            configure(root)
            self.run_at(root, 21)
            send.assert_not_called()
            self.run_at(root, 23)
            self.run_at(root, 23)
            self.assertEqual(send.call_count, 2)
            report = (root / 'output/2026-11-11/23.html').read_text()
            for call in send.call_args_list:
                msg = call.args[1]
                self.assertEqual(list(msg.iter_attachments())[0].get_content().rstrip('\n'), report.rstrip('\n'))
                self.assertIn('比較データなし', msg.get_body(preferencelist=('plain',)).get_content())
            with viewer.Store(root) as store:
                self.assertEqual([row['status'] for row in store.mail_status()], ['sent', 'sent'])
            self.assertNotIn('example.com', (root / 'data/2026-11-11/23.json').read_text())

    def test_partial_failure_retries_only_the_failed_recipient(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            configure(root)
            send.side_effect = [None, mailer.MailError('failed', uncertain=False)]
            with self.assertRaises(viewer.ViewerError):
                self.run_at(root, 23)
            send.side_effect = None
            self.run_at(root, 23)
            self.assertEqual(send.call_count, 3)
            self.assertEqual(send.call_args.args[1]['To'], 'two@example.com')
            with viewer.Store(root) as store:
                self.assertEqual([row['status'] for row in store.mail_status()], ['sent', 'sent'])
                self.assertEqual(len(store.history()), 1)

    def test_uncertain_delivery_and_interrupted_delivery_are_not_automatically_retried(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            configure(root)
            send.side_effect = mailer.MailError('unknown', uncertain=True)
            with self.assertRaises(viewer.ViewerError):
                self.run_at(root, 23)
            with viewer.Store(root) as store:
                store.set_mail_status(sample()['scheduled_at'], 'two@example.com', 'sending')
            self.run_at(root, 23)
            self.assertEqual(send.call_count, 2)

    def test_push_failure_does_not_prevent_email_or_delete_data(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send, patch('viewer.publish', side_effect=viewer.ViewerError('push failed')), patch('viewer.client_from_config') as factory:
            root = Path(td)
            configure(root)
            factory.return_value.collect.return_value = {k: sample()[k] for k in ('games', 'stream_count', 'included_stream_count')}
            with self.assertRaises(viewer.ViewerError):
                viewer.run(root, datetime.fromisoformat(sample()['scheduled_at']))
            self.assertEqual(send.call_count, 2)
            with viewer.Store(root) as store:
                self.assertEqual(len(store.history()), 1)

    def test_missing_settings_send_nothing_and_old_reports_are_not_backfilled(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            self.run_at(root, 23)
            configure(root)
            with viewer.Store(root) as store:
                viewer.send_reports(root, store, store.history(), datetime.fromisoformat('2026-11-12T19:00:00+09:00'))
            send.assert_not_called()

    def test_failed_mail_is_not_retried_outside_23(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            configure(root)
            send.side_effect = mailer.MailError('failed')
            with self.assertRaises(viewer.ViewerError):
                self.run_at(root, 23)
            with viewer.Store(root) as store:
                viewer.send_reports(root, store, store.history(), datetime.fromisoformat('2026-11-12T19:00:00+09:00'))
            self.assertEqual(send.call_count, 2)

    def test_removed_recipient_is_not_retried(self):
        with tempfile.TemporaryDirectory() as td, patch('mailer.send') as send:
            root = Path(td)
            configure(root)
            send.side_effect = mailer.MailError('failed')
            with self.assertRaises(viewer.ViewerError):
                self.run_at(root, 23)
            path = root / '.local/mail.json'
            path.write_text(json.dumps(dict(settings(), recipients=['one@example.com'])))
            send.reset_mock(side_effect=True)
            self.run_at(root, 23)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[1]['To'], 'one@example.com')


if __name__ == '__main__':
    unittest.main()
