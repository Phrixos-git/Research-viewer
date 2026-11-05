import json
import tempfile
import unittest
from pathlib import Path
import viewer


def sample():
    stamp = '2026-11-05T23:00:00+09:00'
    return dict(scheduled_at=stamp,started_at=stamp,finished_at=stamp,stream_count=2,included_stream_count=2,
                games=[dict(id='g',name='Game',viewers=100,broadcasters={'a':40,'b':60})])


class AudienceStorageTests(unittest.TestCase):
    def test_normalization_preserves_and_copies_counts(self):
        s = sample()
        normalized = viewer.validate_snapshot(s)
        self.assertEqual(normalized['games'][0]['broadcasters'],{'a':40,'b':60})
        s['games'][0]['broadcasters']['a']=999
        self.assertEqual(normalized['games'][0]['broadcasters']['a'],40)

    def test_bad_counts_are_rejected(self):
        for counts in ({'a':99},{'a':True},{'a':-1},None,[],{'':100}):
            s=sample();s['games'][0]['broadcasters']=counts
            with self.subTest(counts=counts),self.assertRaises(viewer.ViewerError):
                viewer.validate_snapshot(s)

    def test_db_reopen_export_restore_and_missing_observations(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as restored:
            root=Path(td);s=sample()
            with viewer.Store(root) as store:store.save(s)
            with viewer.Store(root) as store:
                loaded=store.history()
                self.assertEqual(loaded,[s])
                viewer.export_reports(root,loaded,viewer.scheduled_time(s))
            exported=json.loads((root/'data/2026-11-05/23.json').read_text())
            self.assertEqual(exported,s)
            with viewer.Store(Path(restored)) as store:
                store.restore([exported])
                self.assertEqual(store.history(),[s])
        with tempfile.TemporaryDirectory() as td:
            s=sample();del s['games'][0]['broadcasters']
            with viewer.Store(Path(td)) as store:
                store.save(s)
                self.assertNotIn('broadcasters',store.history()[0]['games'][0])

    def test_failed_save_is_atomic(self):
        with tempfile.TemporaryDirectory() as td,viewer.Store(Path(td)) as store:
            s=sample();s['games'][0]['broadcasters']={'a':99}
            with self.assertRaises(viewer.ViewerError):store.save(s)
            self.assertEqual(store.history(),[])
