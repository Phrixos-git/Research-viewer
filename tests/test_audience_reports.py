import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import viewer


def records(with_single=True, known=True):
    result=[]
    for day in (4,5):
        games=[]
        for i in range(4):
            counts={'a':100,'b':100,'c':100} if day==4 else {'a':150+i*3,'b':125+i,'c':125+i}
            games.append(dict(id=f'n{i}',name=f'Normal <{i}>',viewers=sum(counts.values()),**({'broadcasters':counts} if known else {})))
            if with_single:
                counts={'a':100,'b':100} if day==4 else {'a':200+i*20,'b':100}
                games.append(dict(id=f's{i}',name=f'Single <{i}>',viewers=sum(counts.values()),**({'broadcasters':counts} if known else {})))
        stamp=f'2026-11-{day:02d}T23:00:00+09:00'
        result.append(dict(scheduled_at=stamp,started_at=stamp,finished_at=stamp,games=games,stream_count=24,included_stream_count=24))
    return result


class AudienceReportTests(unittest.TestCase):
    def test_each_frame_has_3_without_overlap_and_evidence(self):
        before,after=records()
        before['scheduled_at'] = '2026-11-05T21:00:00+09:00'
        after['scheduled_at'] = '2026-11-05T22:00:00+09:00'
        page=viewer.render_html(after,[before,after])
        self.assertEqual(page.count('class="rise"'),3)
        self.assertEqual(page.count('class="single-rise rise"'),3)
        self.assertEqual(page.count('<h4>単一配信者による急増</h4>'),1)
        self.assertIn('除外後',page)
        self.assertIn('寄与率',page)
        self.assertIn('Normal &lt;3&gt;',page)
        self.assertIn('25％',page)
        normal=viewer.rising_titles(viewer.rising_groups(after,[before,after])[-1][1])
        single=viewer.rising_titles(viewer.rising_groups(after,[before,after])[-1][1],'single')
        self.assertFalse({r['id'] for r in normal}&{r['id'] for r in single})

    def test_no_single_frame_without_candidates_and_missing_is_not_selected(self):
        before,after=records(with_single=False)
        before['scheduled_at'] = '2026-11-05T21:00:00+09:00'
        after['scheduled_at'] = '2026-11-05T22:00:00+09:00'
        self.assertNotIn('<h4>単一配信者による急増</h4>',viewer.render_html(after,[before,after]))
        before,after=records(known=False)
        before['scheduled_at'] = '2026-11-05T21:00:00+09:00'
        after['scheduled_at'] = '2026-11-05T22:00:00+09:00'
        page=viewer.render_html(after,[before,after])
        self.assertIn('判定不可',page)
        self.assertNotIn('class="rise"',page)
        self.assertNotIn('class="single-rise rise"',page)

    def test_mail_uses_same_frames_and_data(self):
        before,after=records()
        config=dict(username='sender@example.com',recipients=['to@example.com'])
        with tempfile.TemporaryDirectory() as td,patch('mailer.load_settings',return_value=config),patch('mailer.send') as send:
            with viewer.Store(Path(td)) as store:
                store.save(before);store.save(after)
                viewer.send_reports(Path(td),store,[before,after],datetime.fromisoformat(after['scheduled_at']))
            message=send.call_args.args[1]
        plain=message.get_body(preferencelist=('plain',)).get_content()
        page=message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('単一配信者による急増',plain)
        self.assertIn('除外後',plain)
        self.assertIn('Normal <3>',plain)
        self.assertIn('Single <3>',plain)
        self.assertNotIn('class="rise"',page)
        self.assertNotIn('class="single-rise rise"',page)
        self.assertNotIn('判定不可',page)
        self.assertEqual(page.rstrip(),next(message.iter_attachments()).get_content().rstrip())
