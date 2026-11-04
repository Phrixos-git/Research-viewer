from contextlib import closing
import json
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import viewer


def game(gid, viewers, name=None):
    return {'id': gid, 'name': name or gid, 'viewers': viewers}


def sample(day, hour, *games):
    stamp = f'{day}T{hour:02d}:00:00+09:00'
    return dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp, **collection(list(games)))


def collection(games):
    return dict(games=games, stream_count=len(games), included_stream_count=len(games))


class FakeClient(viewer.Client):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, path, **params):
        self.calls.append((path, params))
        try:
            return next(self.responses)
        except StopIteration:
            raise viewer.ViewerError('simulated page failure') from None


def stream(sid, gid='a', count=10):
    return dict(id=str(sid), game_id=gid, game_name=gid, viewer_count=count)


def metadata(*ids):
    return dict(data=[dict(id=gid, name=gid.upper(), igdb_id=gid) for gid in ids])


class CollectionTests(unittest.TestCase):
    def test_1000_streams_are_collected_and_1001st_is_not_included(self):
        pages = [dict(data=[stream(i, count=2000-i) for i in range(n, n+100)],
                      pagination=dict(cursor=str(n+100))) for n in range(0, 1000, 100)]
        c = FakeClient(pages + [metadata('a')])
        result = c.collect()
        self.assertEqual(result['stream_count'], 1000)
        self.assertEqual(result['games'], [game('a', sum(range(1001, 2001)), 'A')])
        self.assertEqual(sum(path == '/streams' for path, _ in c.calls), 10)
        self.assertTrue(all('game_id' not in params and params['type'] == 'live' for path, params in c.calls if path == '/streams'))

    def test_fewer_than_1000_and_duplicate_streams_across_pages(self):
        c = FakeClient([
            dict(data=[stream('s1', count=20)], pagination=dict(cursor='next')),
            dict(data=[stream('s1', count=999), stream('s2', 'b', 40), stream('s3', count=10)], pagination={}),
            metadata('a', 'b'),
        ])
        result = c.collect()
        self.assertEqual(result, dict(stream_count=3, included_stream_count=3,
                                     games=[game('b', 40, 'B'), game('a', 30, 'A')]))
        self.assertEqual(c.calls[1][1]['after'], 'next')

    def test_non_game_streams_count_toward_limit_and_are_not_replaced(self):
        c = FakeClient([
            dict(data=[stream(1, 'chat', 100), stream(2, '', 90), stream(3, 'a', 80), stream(4, 'b', 70)], pagination={}),
            dict(data=[dict(id='chat', name='Chat', igdb_id=''), dict(id='a', name='A', igdb_id='1')]),
        ])
        self.assertEqual(c.collect(3), dict(stream_count=3, included_stream_count=1, games=[game('a', 80, 'A')]))

    def test_no_streams_and_no_eligible_games_are_successful_empty_results(self):
        c = FakeClient([dict(data=[], pagination={})])
        self.assertEqual(c.collect(), collection([]))
        c = FakeClient([dict(data=[stream(1, 'chat')], pagination={}), dict(data=[])])
        self.assertEqual(c.collect(), dict(stream_count=1, included_stream_count=0, games=[]))

    def test_more_than_100_titles_are_not_truncated_and_metadata_is_batched(self):
        rows = [stream(i, str(i), i) for i in range(120)]
        c = FakeClient([dict(data=rows[:100], pagination=dict(cursor='next')),
                        dict(data=rows[100:], pagination={}),
                        metadata(*[str(i) for i in range(100)]), metadata(*[str(i) for i in range(100, 120)])])
        result = c.collect()
        self.assertEqual(len(result['games']), 120)
        self.assertEqual([len(params['id']) for path, params in c.calls if path == '/games'], [100, 20])

    def test_invalid_count_or_failed_page_does_not_become_a_partial_success(self):
        for count in [-1, True, '10']:
            c = FakeClient([dict(data=[stream(1, count=count)], pagination={})])
            with self.subTest(count=count), self.assertRaises(viewer.ViewerError):
                c.collect()
        c = FakeClient([dict(data=[stream(1)], pagination=dict(cursor='next'))])
        with self.assertRaises(viewer.ViewerError):
            c.collect()

    def test_game_metadata_ids_are_encoded_as_repeated_query_parameters(self):
        c = object.__new__(viewer.Client)
        c.client_id, c.token = 'id', 'token'
        with patch.object(c, 'request', return_value={}) as request:
            c.get('/games', id=['a', 'b'])
            self.assertIn('id=a&id=b', request.call_args.args[1])

    def test_repeated_cursor_is_an_error(self):
        c = FakeClient([dict(data=[], pagination=dict(cursor='same')), dict(data=[], pagination=dict(cursor='same'))])
        with self.assertRaises(viewer.ViewerError):
            c.collect()


class ComparisonTests(unittest.TestCase):
    def test_period_uses_matched_hours_and_game_ids_not_positions(self):
        h = [sample('2026-11-03', 19, game('a', 100), game('b', 500)),
             sample('2026-11-03', 20, game('a', 200)),
             sample('2026-11-04', 19, game('b', 700), game('a', 200)),
             sample('2026-11-04', 20, game('b', 900), game('a', 400)),
             sample('2026-11-04', 21, game('a', 999), game('new', 9000))]
        rows = viewer.compare_period(h, date(2026, 11, 4), 21)
        by_id = {r['id']: r for r in rows}
        self.assertEqual(set(by_id), {'a', 'b'})
        self.assertEqual(by_id['a']['before'], 150)
        self.assertEqual(by_id['a']['after'], 300)
        self.assertEqual(by_id['a']['percent'], 100)
        self.assertEqual(by_id['a']['samples'], 2)
        self.assertEqual(by_id['b']['samples'], 1)
        self.assertTrue(all(r['expected'] == 3 for r in rows))

    def test_zero_baseline_and_no_previous_data(self):
        h = [sample('2026-11-03', 19, game('a', 0)), sample('2026-11-04', 19, game('a', 10))]
        row = viewer.compare_period(h, date(2026, 11, 4), 21)[0]
        self.assertIsNone(row['percent'])
        self.assertEqual(viewer.compare_period(h, date(2026, 11, 5), 21), [])

    def test_23_does_not_use_future_or_other_dates(self):
        h = [sample('2026-11-03', h, game('a', 100)) for h in range(19, 24)]
        h += [sample('2026-11-04', h, game('a', 200)) for h in range(19, 24)]
        row = viewer.compare_period(h, date(2026, 11, 4), 23)[0]
        self.assertEqual((row['samples'], row['expected'], row['change']), (5, 5, 100))

    def test_normal_rise_needs_immediately_previous_hour(self):
        s = sample('2026-11-04', 22, game('a', 30))
        self.assertEqual(viewer.report_rows(s, [sample('2026-11-04', 20, game('a', 1)), s])[0], [])
        rows, label = viewer.report_rows(s, [sample('2026-11-04', 21, game('a', 10)), s])
        self.assertEqual(rows[0]['change'], 20)
        self.assertIn('21', label)

    def test_html_escapes_names_and_does_not_invent_three_risers(self):
        s = sample('2026-11-04', 20, game('a', 100, '<script>alert(1)</script>'))
        html = viewer.render_html(s, [sample('2026-11-04', 19, game('a', 50)), s])
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(html.count('class="rise"'), 1)
        self.assertIn('100', html)


class SchedulingTests(unittest.TestCase):
    def test_slots_use_jst_and_reject_late_or_off_hours(self):
        now = datetime.fromisoformat('2026-11-04T12:04:00+00:00')
        self.assertEqual(viewer.slot_for(now).hour, 21)
        for stamp in ['2026-11-04T18:00:00+09:00', '2026-11-04T23:11:00+09:00']:
            self.assertIsNone(viewer.slot_for(datetime.fromisoformat(stamp)))

    def test_working_directory_is_unquoted_but_exec_arguments_are_quoted(self):
        root = Path('/tmp/project space%name')
        service, _ = viewer.timer_units(root)
        self.assertIn('WorkingDirectory=/tmp/project space%%name\n', service)
        self.assertIn('"/tmp/project space%%name/viewer.py" collect', service)

    def test_install_failure_reports_actual_systemctl_command_and_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '.local').mkdir()
            (root / '.local/config.json').write_text('{}')
            with patch('viewer.check_repository'), patch('viewer.git'), patch('viewer.Path.home', return_value=root), patch('viewer.subprocess.run') as run:
                run.return_value = subprocess.CompletedProcess([], 1, stdout='', stderr='WorkingDirectory path is not absolute')
                with self.assertRaises(viewer.ViewerError) as error:
                    viewer.install_timer(root)
                self.assertIn('systemctl --user daemon-reload', str(error.exception))
                self.assertIn('WorkingDirectory path is not absolute', str(error.exception))
                self.assertNotIn('接続できません', str(error.exception))

    def test_timer_contains_exactly_five_hours_and_no_catch_up(self):
        unit, timer = viewer.timer_units(Path('/tmp/a space'))
        self.assertIn('Asia/Tokyo', timer)
        self.assertIn('19,20,21,22,23:00:00', timer)
        self.assertIn('Persistent=false', timer)
        self.assertIn('viewer.py', unit)
        self.assertIn(' collect\n', unit)


class StorageTests(unittest.TestCase):
    def test_failed_collection_does_not_write_success(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as factory:
            factory.return_value.collect.side_effect = viewer.ViewerError('failed')
            with self.assertRaises(viewer.ViewerError):
                viewer.run(Path(td), datetime.fromisoformat('2026-11-04T21:00:00+09:00'), publish_result=False)
            self.assertEqual(list(Path(td).glob('data/**/*.json')), [])
            with viewer.Store(Path(td)) as store:
                self.assertEqual(store.history(), [])

    def test_same_slot_is_not_recollected_and_preserves_comparison_file(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as factory:
            factory.return_value.collect.return_value = collection([game('a', 20)])
            stamp = datetime.fromisoformat('2026-11-04T21:00:00+09:00')
            viewer.run(Path(td), stamp, publish_result=False)
            viewer.run(Path(td), stamp, publish_result=False)
            self.assertEqual(factory.return_value.collect.call_count, 1)
            self.assertTrue((Path(td) / 'output/2026-11-04/21.html').exists())
            self.assertTrue((Path(td) / 'output/index.html').exists())


class SQLiteTests(unittest.TestCase):
    def test_persistence_and_title_history_with_stable_game_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = sample('2026-11-03', 21, game('a', 100, '旧タイトル'))
            new = sample('2026-11-04', 21, game('a', 200, '新タイトル'))
            with viewer.Store(root) as store:
                store.save(old)
                store.save(new)
            self.assertTrue((root / '.local/viewers.sqlite3').is_file())
            with viewer.Store(root) as store:
                history = store.history()
                self.assertEqual(history, [old, new])
                row = viewer.compare_period(history, date(2026, 11, 4), 21)[0]
                self.assertEqual((row['id'], row['name'], row['change']), ('a', '新タイトル', 100))

    def test_old_database_is_rejected_without_migrating_or_deleting_records(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '.local').mkdir()
            path = root / '.local/viewers.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE collection_runs (id INTEGER PRIMARY KEY, scheduled_at TEXT UNIQUE, started_at TEXT, finished_at TEXT)')
                db.execute("INSERT INTO collection_runs VALUES (1, 'old', 'start', 'finish')")
                db.commit()
            with self.assertRaises(viewer.ViewerError):
                viewer.Store(root)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT scheduled_at FROM collection_runs').fetchall(), [('old',)])
                self.assertNotIn('stream_count', [r[1] for r in db.execute('PRAGMA table_info(collection_runs)')])

    def test_empty_collection_is_saved_exported_and_rendered(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as factory:
            root = Path(td)
            factory.return_value.collect.return_value = collection([])
            viewer.run(root, datetime.fromisoformat('2026-11-04T21:00:00+09:00'), publish_result=False)
            with viewer.Store(root) as store:
                snapshot = store.history()[0]
                self.assertEqual(snapshot['stream_count'], 0)
                self.assertEqual(snapshot['games'], [])
            self.assertIn('取得配信数：0', (root / 'output/index.html').read_text())
            self.assertEqual(json.loads((root / 'data/2026-11-04/21.json').read_text())['stream_count'], 0)

    def test_duplicate_slot_is_rejected_without_overwriting(self):
        with tempfile.TemporaryDirectory() as td, viewer.Store(Path(td)) as store:
            s = sample('2026-11-04', 21, game('a', 30))
            store.save(s)
            with self.assertRaises(viewer.ViewerError):
                store.save(sample('2026-11-04', 21, game('a', 999)))
            self.assertEqual(store.history(), [s])

    def test_game_uniqueness_failure_rolls_back_entire_run(self):
        with tempfile.TemporaryDirectory() as td, viewer.Store(Path(td)) as store:
            with self.assertRaises(viewer.ViewerError):
                store.save(sample('2026-11-04', 21, game('a', 20), game('a', 30)))
            self.assertEqual(store.history(), [])
            store.save(sample('2026-11-04', 21, game('a', 40)))
            self.assertEqual(len(store.history()), 1)

    def test_invalid_counts_and_timestamps_are_not_saved(self):
        with tempfile.TemporaryDirectory() as td, viewer.Store(Path(td)) as store:
            for count in [-1, 1.5, True, '10']:
                with self.subTest(count=count), self.assertRaises(viewer.ViewerError):
                    store.save(sample('2026-11-04', 21, game('a', count)))
            s = sample('2026-11-04', 21, game('a', 1))
            for stamp in ['2026-11-04T21:00:00', '2026-11-04T21:30:00+09:00']:
                with self.subTest(stamp=stamp), self.assertRaises(viewer.ViewerError):
                    store.save(dict(s, scheduled_at=stamp))
            self.assertEqual(store.history(), [])

    def test_database_enforces_foreign_keys_and_nonnegative_integer_counts(self):
        with tempfile.TemporaryDirectory() as td, viewer.Store(Path(td)) as store:
            store.save(sample('2026-11-04', 21, game('a', 30)))
            with closing(sqlite3.connect(Path(td) / '.local/viewers.sqlite3')) as db:
                db.execute('PRAGMA foreign_keys = ON')
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO viewer_counts VALUES (999, 'orphan', '孤児', 10)")
                for value in [-1, 1.5, 'invalid']:
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute("INSERT INTO viewer_counts VALUES (1, 'invalid', '不正', ?)", (value,))

    def test_json_is_output_and_normal_collection_uses_database(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as factory:
            root = Path(td)
            factory.return_value.collect.return_value = collection([game('a', 20)])
            stamp = datetime.fromisoformat('2026-11-04T21:00:00+09:00')
            viewer.run(root, stamp, publish_result=False)
            path = root / 'data/2026-11-04/21.json'
            original = json.loads(path.read_text())
            path.write_text('corrupted export')
            viewer.run(root, stamp, publish_result=False)
            self.assertEqual(json.loads(path.read_text()), original)
            self.assertEqual(factory.return_value.collect.call_count, 1)

    def test_export_failure_retains_db_and_next_run_repairs_missing_exports(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as factory:
            root = Path(td)
            factory.return_value.collect.return_value = collection([game('a', 20)])
            with patch('viewer.atomic_write', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    viewer.run(root, datetime.fromisoformat('2026-11-04T21:00:00+09:00'), publish_result=False)
            with viewer.Store(root) as store:
                self.assertEqual(len(store.history()), 1)
            viewer.run(root, datetime.fromisoformat('2026-11-04T22:00:00+09:00'), publish_result=False)
            self.assertTrue((root / 'data/2026-11-04/21.json').is_file())
            self.assertTrue((root / 'output/2026-11-04/21.html').is_file())
            self.assertTrue((root / 'data/2026-11-04/22.json').is_file())

    def test_restore_is_explicit_and_rebuilds_comparable_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for day, count in [('2026-11-03', 100), ('2026-11-04', 200)]:
                path = root / 'data' / day / '21.json'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(sample(day, 21, game('a', count))))
            with patch('viewer.client_from_config') as factory:
                with self.assertRaises(viewer.ViewerError):
                    viewer.run(root, datetime.fromisoformat('2026-11-04T22:00:00+09:00'), publish_result=False)
                factory.assert_not_called()
            viewer.restore(root)
            with viewer.Store(root) as store:
                row = viewer.compare_period(store.history(), date(2026, 11, 4), 21)[0]
                self.assertEqual(row['change'], 100)
            with self.assertRaises(viewer.ViewerError):
                viewer.restore(root)

    def test_restore_with_corrupt_export_or_duplicate_game_is_atomic(self):
        for content in ['broken', json.dumps(sample('2026-11-04', 21, game('a', 1), game('a', 2)))]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                directory = root / 'data/2026-11-04'
                directory.mkdir(parents=True)
                (directory / '20.json').write_text(json.dumps(sample('2026-11-04', 20, game('a', 10))))
                (directory / '21.json').write_text(content)
                with self.assertRaises(viewer.ViewerError):
                    viewer.restore(root)
                with viewer.Store(root) as store:
                    self.assertEqual(store.history(), [])

    def test_restore_rejects_wrong_filename_and_empty_exports(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(viewer.ViewerError):
                viewer.restore(root)
            path = root / 'data/2026-11-04/20.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(sample('2026-11-04', 21, game('a', 10))))
            with self.assertRaises(viewer.ViewerError):
                viewer.restore(root)


class PublicationTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout

    def test_scoped_commit_push_and_refusal_of_unrelated_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'work'
            root.mkdir()
            remote = Path(td) / 'remote.git'
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            self.git(root, 'init', '-b', 'main')
            self.git(root, 'config', 'user.name', 'Test')
            self.git(root, 'config', 'user.email', 'test@example.invalid')
            self.git(root, 'remote', 'add', 'origin', str(remote))
            (root / 'README.md').write_text('initial')
            (root / '.gitignore').write_text('.local/\n')
            self.git(root, 'add', 'README.md', '.gitignore')
            self.git(root, 'commit', '-m', 'initial')
            (root / 'data').mkdir()
            (root / 'output').mkdir()
            (root / 'data/a.json').write_text('{}')
            (root / 'output/index.html').write_text('report')
            with viewer.Store(root) as store:
                store.save(sample('2026-11-04', 21, game('a', 10)))
            viewer.publish(root, expected_remote=str(remote))
            self.assertEqual(self.git(root, 'status', '--porcelain'), '')
            self.assertNotIn('sqlite', self.git(root, 'ls-tree', '-r', '--name-only', 'HEAD'))
            self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), self.git(remote, 'rev-parse', 'refs/heads/main'))
            (root / 'data/a.json').write_text('{"second":2}')
            viewer.publish(root, expected_remote=str(remote))
            self.assertEqual(self.git(root, 'status', '--porcelain'), '')
            (root / 'README.md').write_text('unrelated')
            (root / 'data/a.json').write_text('{"new":1}')
            with self.assertRaises(viewer.ViewerError):
                viewer.publish(root, expected_remote=str(remote))
            self.assertIn('README.md', self.git(root, 'diff', '--name-only'))

    def test_two_days_of_100_games_flow_from_sqlite_to_html_json_and_git(self):
        with tempfile.TemporaryDirectory() as td, patch('viewer.client_from_config') as factory:
            root, remote = Path(td) / 'work', Path(td) / 'remote.git'
            root.mkdir()
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            self.git(root, 'init', '-b', 'main')
            self.git(root, 'config', 'user.name', 'Test')
            self.git(root, 'config', 'user.email', 'test@example.invalid')
            self.git(root, 'remote', 'add', 'origin', str(remote))
            (root / '.gitignore').write_text('.local/\n')
            self.git(root, 'add', '.gitignore')
            self.git(root, 'commit', '-m', 'initial')
            publish = viewer.publish
            with patch('viewer.publish', side_effect=lambda r: publish(r, expected_remote=str(remote))):
                for day, multiplier in [('2026-11-03', 1), ('2026-11-04', 2)]:
                    for hour in range(19, 24):
                        factory.return_value.collect.return_value = collection([game(str(i), (100 + i + hour) * multiplier) for i in range(100)])
                        stamp = datetime.fromisoformat(f'{day}T{hour:02d}:00:00+09:00')
                        viewer.run(root, stamp)
            with viewer.Store(root) as store:
                history = store.history()
                self.assertEqual(len(history), 10)
                self.assertEqual(sum(len(s['games']) for s in history), 1000)
                rows = viewer.compare_period(history, date(2026, 11, 4), 23)
                self.assertEqual(len(rows), 100)
                self.assertTrue(all(r['samples'] == 5 and r['percent'] == 100 for r in rows))
            self.assertEqual(factory.return_value.collect.call_count, 10)
            self.assertEqual(len(list((root / 'data').glob('*/*.json'))), 10)
            for hour in [21, 23]:
                report = (root / f'output/2026-11-04/{hour}.html').read_text()
                self.assertIn('前日比較', report)
                self.assertEqual(report.count('class="rise"'), 3)
            self.assertNotIn('sqlite', self.git(remote, 'ls-tree', '-r', '--name-only', 'refs/heads/main'))
            self.assertEqual(self.git(root, 'status', '--porcelain'), '')
            self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), self.git(remote, 'rev-parse', 'refs/heads/main'))

    def test_failed_push_keeps_commit_and_retries_without_new_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root, remote = Path(td) / 'work', Path(td) / 'later.git'
            root.mkdir()
            self.git(root, 'init', '-b', 'main')
            self.git(root, 'config', 'user.name', 'Test')
            self.git(root, 'config', 'user.email', 'test@example.invalid')
            self.git(root, 'remote', 'add', 'origin', str(remote))
            (root / 'data').mkdir()
            (root / 'data/a.json').write_text('{}')
            with self.assertRaises(viewer.ViewerError):
                viewer.publish(root, expected_remote=str(remote))
            saved_commit = self.git(root, 'rev-parse', 'HEAD')
            self.assertTrue((root / 'data/a.json').exists())
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            viewer.publish(root, expected_remote=str(remote))
            self.assertEqual(saved_commit, self.git(remote, 'rev-parse', 'refs/heads/main'))

    def test_staged_secrets_are_never_committed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.git(root, 'init', '-b', 'main')
            self.git(root, 'remote', 'add', 'origin', viewer.REMOTE)
            (root / 'secret.txt').write_text('private')
            self.git(root, 'add', 'secret.txt')
            with self.assertRaises(viewer.ViewerError):
                viewer.publish(root)
            self.assertIn('secret.txt', self.git(root, 'diff', '--cached', '--name-only'))


if __name__ == '__main__':
    unittest.main()
