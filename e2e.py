"""Replay saved test snapshots through storage, reports and optional real email."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch
import uuid

import mailer
import viewer

RECIPIENT = 'kurahasb@gmail.com'


def require(condition, description):
    if not condition:
        raise viewer.ViewerError(f'E2E検証失敗: {description}')


def table_rows(page, kind):
    match = re.search(rf'<table class="report-table {kind}".*?<tbody>(.*?)</tbody></table>', page, re.S)
    if not match:
        return []
    return [re.findall(r'<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>', row, re.S)
            for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', match[1], re.S)]


def text(cell):
    return html.unescape(re.sub(r'<[^>]+>', '', cell))


def verify_report(snapshot, history, page):
    """Compute expected period values independently from the stored observations."""
    slot = viewer.scheduled_time(snapshot)
    indexed = {viewer.scheduled_time(s): s for s in history}
    pairs = {}
    for hour in range(19, 24):
        current = indexed.get(slot.replace(hour=hour))
        previous = indexed.get(slot.replace(hour=hour) - viewer.timedelta(days=1))
        if current is None or previous is None:
            continue
        before = {g['id']: g for g in previous['games']}
        for game in current['games']:
            if game['id'] in before:
                pairs.setdefault(game['id'], []).append((before[game['id']]['viewers'], game['viewers'], game['name']))
    expected = {}
    for gid, values in pairs.items():
        before = sum(v[0] for v in values) / len(values)
        after = sum(v[1] for v in values) / len(values)
        expected[gid] = dict(name=values[-1][2], change=after - before,
                             percent=(after / before - 1) * 100 if before else None,
                             samples=len(values))
    top = sorted((gid for gid, r in expected.items() if r['percent'] is not None),
                 key=lambda gid: (-expected[gid]['percent'], gid))[:10]
    comparison = table_rows(page, 'comparison')
    require([text(r[0]) for r in comparison] == [expected[gid]['name'] for gid in top], '増加率Top10の選出・順序')
    for row, gid in zip(comparison, top):
        require(f'{expected[gid]["change"]:+,.1f}' in text(row[3]), '期間平均の増減人数')
        require(f'{expected[gid]["percent"]:+.1f}%' in text(row[4]), '期間平均の増減率')
    previous = indexed.get(slot - viewer.timedelta(days=1))
    ranks = {g['id']: rank for rank, g in enumerate(previous['games'], 1)} if previous else {}
    ranking = table_rows(page, 'ranking')
    require(len(ranking) == min(len(snapshot['games']), 100), 'ランキング表示件数')
    for rank, (row, game) in enumerate(zip(ranking, snapshot['games']), 1):
        require(len(row) == 5 and text(row[0]) == str(rank), 'ランキング列位置・順位')
        require(text(row[1]) == game['name'] and text(row[2]) == f'{game["viewers"]:,}', 'タイトル・視聴者数')
        average = expected.get(game['id'])
        require((f'{average["change"]:+,.1f}' in text(row[3]) and f'{average["samples"]}/5' in text(row[3]))
                if average else '比較データなし' in text(row[3]), 'ランキング平均増減')
        old_rank = ranks.get(game['id'])
        mark = '★' if old_rank is None else '-' if old_rank == rank else '↑' if old_rank > rank else '↓'
        require(text(row[4]) == mark, '順位前日比')
    for style, mark in re.findall(r'<td class="rank-change" style="([^"]*)">(.*?)</td>', page):
        colors = {'↑': viewer.UP, '↓': viewer.DOWN, '★': viewer.NEW}
        if mark in colors:
            require(f'color:{colors[mark]};' in style, '順位前日比の色')
    require('テストデータ' in page and '全言語配信' in page, 'テストデータ表示')
    require(not re.search(r'前日比較：\d+時', page), '時間帯別前日比較の非表示')


def verify_supplemental(work):
    """Keep invented edge cases separate from the recorded observations."""
    root = work / 'supplemental'
    root.mkdir(mode=0o700)
    cases = ['zero_baseline', 'zero_viewers', 'unchanged', 'decrease', 'tied_rates',
             'partial_hours', 'new_title', 'audience_categories', 'hourly_rise_display']
    history = []
    for day in (5, 6):
        for hour in range(19, 24):
            current = day == 6
            counts = {
                'normal': {'a': 200, 'b': 200} if current else {'a': 100, 'b': 100},
                'single': {'a': 400, 'b': 100} if current else {'a': 100, 'b': 100},
                'zero-up': {'a': 100, 'b': 100} if current else {'a': 0, 'b': 0},
                'zero-flat': {'a': 0, 'b': 0},
                'unchanged': {'a': 50, 'b': 50},
                'decrease': {'a': 25, 'b': 25} if current else {'a': 50, 'b': 50},
                'tie-a': {'a': 75, 'b': 75} if current else {'a': 50, 'b': 50},
                'tie-b': {'a': 75, 'b': 75} if current else {'a': 50, 'b': 50},
            }
            if hour in (19, 23):
                counts['partial'] = {'a': 6 + (hour == 23)} if current else {'a': 5}
            if current and hour in (20, 22):
                counts['normal'] = {'a': 400, 'b': 400}
                counts['single'] = {'a': 700, 'b': 100}
            if current:
                counts['new'] = {'a': 200}
            games = [dict(id=f'e2e-{gid}', name=f'[合成] {gid}', viewers=sum(value.values()), broadcasters=value)
                     for gid, value in counts.items()]
            games.append(dict(id='e2e-missing', name='[合成] missing', viewers=200 if current else 100))
            stamp = f'2026-11-{day:02d}T{hour}:00:00+09:00'
            snapshot = dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp,
                            stream_count=30, included_stream_count=30,
                            games=sorted(games, key=lambda g: (-g['viewers'], g['id'])))
            history.append(snapshot)
            viewer.atomic_write(root / 'data' / f'2026-11-{day:02d}' / f'{hour}.json', json.dumps(snapshot, ensure_ascii=False))
    viewer.atomic_write(root / 'README.txt', '不足ケース検証専用の合成データ。実際のTwitch取得記録ではありません。\n')
    viewer.restore(root)
    with viewer.Store(root) as store:
        restored = store.history()
        require(restored == history, '合成シナリオのSQLite復元')
        latest = restored[-1]
        slot = viewer.scheduled_time(latest)
        viewer.export_reports(root, restored, slot)
        for snapshot in restored:
            stamp = viewer.scheduled_time(snapshot)
            if stamp.day != 6:
                continue
            report = viewer.render_html(snapshot, restored)
            if stamp.hour in (20, 22):
                viewer.atomic_write(root / 'output' / f'{stamp:%Y-%m-%d/%H}.html', report)
                cards = re.findall(r'<article\b.*?</article>', report)
                require(len(cards) == 2, '合成の急上昇カード件数')
                for card, category, name, change, percent, remaining in [
                    (cards[0], 'rise', 'normal', 400, '100.0', '200 → 400人（+100.0%）'),
                    (cards[1], 'single-rise rise', 'single', 300, '60.0', '100 → 100人（+0.0%）'),
                ]:
                    require(f'class="{category}"' in card
                            and f'<h3>[合成] {name}</h3><strong>+{change} 人</strong>' in card
                            and f'<p>+{percent}%</p>' in card
                            and f'除外後 {remaining}' in card, '合成の急上昇カード分類・人数・率・内訳')
            elif stamp.hour in (21, 23):
                require('<article' not in report, '21時・23時の急上昇カード非表示')
        page = (root / 'output/index.html').read_text()
        verify_report(latest, restored, page)
        rows = {r['id']: r for r in viewer.report_rows(latest, restored)[0]}
        require(rows['e2e-zero-up']['percent'] is None and rows['e2e-zero-up']['change'] == 200, '合成0人基準')
        require(rows['e2e-zero-flat']['change'] == rows['e2e-unchanged']['change'] == 0, '合成横ばい')
        require(rows['e2e-partial']['samples'] == 2 and rows['e2e-partial']['change'] == 1.5, '合成部分集計')
        require('e2e-new' not in rows, '合成新規タイトルの比較除外')
        categories = {r['id']: r['category'] for r in viewer.rising_groups(latest, restored)[-1][1]}
        require(categories['e2e-normal'] == 'normal' and categories['e2e-single'] == 'single'
                and categories['e2e-missing'] == 'unavailable', '合成配信者分類')
        config = dict(username='e2e@example.invalid', recipients=[RECIPIENT])
        with patch('mailer.load_settings', return_value=config), patch('mailer.send') as send:
            viewer.send_reports(root, store, restored, slot)
            require(send.call_count == 1, '合成メールの模擬送信')
            message = send.call_args.args[1]
            message.replace_header('Subject', '[E2E合成テスト・模擬送信] ' + str(message['Subject']))
            plain = message.get_body(preferencelist=('plain',)).get_content()
            require('[合成] normal:' in plain and '[合成] single:' in plain and '判定不可' in plain, '合成メールの分類表示')
            for hour in range(19, 24):
                heading = f'前日比較：{hour}時\n'
                require(plain.count(heading) == 1, f'合成メールの{hour}時の見出し')
                section = plain.split(heading)[1].split('前日比較：')[0]
                require('[合成] normal:' in section and '[合成] single:' in section,
                        f'合成メールの{hour}時の急上昇表示')
            require(message.get_body(preferencelist=('html',)).get_content().rstrip('\n') == page.rstrip('\n')
                    and next(message.iter_attachments()).get_content().rstrip('\n') == page.rstrip('\n'), '合成メール本文・添付の一致')
            viewer.atomic_write(root / 'message.eml', message.as_string(), mode=0o600)
    return dict(synthetic=True, snapshots=len(history), cases=cases)


def run_e2e(root, send_email=False, work_parent=None):
    root = Path(root).resolve()
    sources = []
    for path in sorted((root / 'data').glob('*/*.json')):
        try:
            day = viewer.date.fromisoformat(path.parent.name)
            if day >= viewer.PRODUCTION_START:
                continue
            snapshot = viewer.validate_snapshot(json.loads(path.read_text(encoding='utf-8')))
            slot = viewer.scheduled_time(snapshot)
            require(path == root / 'data' / f'{slot:%Y-%m-%d}' / f'{slot:%H}.json', '入力ファイル名と日時')
        except (ValueError, OSError) as exc:
            raise viewer.ViewerError('E2E入力のテストJSONが不正です。') from exc
        sources.append((path, snapshot))
    require(sources, '保存済みテストJSONがありません')
    candidates = [s for _, s in sources if viewer.scheduled_time(s).hour == 23]
    require(candidates, '23時の保存済みテスト記録がありません')
    latest = max(candidates, key=viewer.scheduled_time)
    settings = mailer.load_settings(root) if send_email else dict(username='e2e@example.invalid')
    require(settings is not None, '実送信用のGmail設定が有効ではありません')
    config = dict(settings, recipients=[RECIPIENT])
    protected = [p for p, _ in sources] + list((root / 'output').rglob('*.html'))
    protected += [p for p in (root / '.local/viewers.sqlite3', root / '.local/mail.json') if p.exists()]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    parent = Path(work_parent) if work_parent is not None else root / '.local'
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = Path(tempfile.mkdtemp(prefix='e2e-', dir=parent))
    result = dict(status='failed', mail_status='not_sent', recipient=RECIPIENT,
                  snapshots=len(sources), workdir=str(work), scheduled_at=latest['scheduled_at'])
    make_message, send = mailer.make_message, mailer.send
    run_id = uuid.uuid4().hex
    captured = []
    try:
        result['supplemental'] = verify_supplemental(work)
        for _, snapshot in sources:
            slot = viewer.scheduled_time(snapshot)
            viewer.atomic_write(work / 'data' / f'{slot:%Y-%m-%d}' / f'{slot:%H}.json', json.dumps(snapshot, ensure_ascii=False))
        viewer.restore(work)
        with viewer.Store(work) as store:
            history = store.history()
            require(history == [s for _, s in sources], 'SQLite復元後の全履歴・人数・内訳')
            viewer.export_reports(work, history, viewer.scheduled_time(latest))
            for snapshot in history:
                slot = viewer.scheduled_time(snapshot)
                restored = json.loads((work / 'data' / f'{slot:%Y-%m-%d}' / f'{slot:%H}.json').read_text())
                require(restored == snapshot, 'JSON再出力の完全性')
                if slot.hour == 23:
                    verify_report(snapshot, history, (work / 'output' / f'{slot:%Y-%m-%d}' / '23.html').read_text())
            report = (work / 'output' / f'{viewer.scheduled_time(latest):%Y-%m-%d}' / '23.html').read_text()

            def test_message(sender, recipient, scheduled_at, plain, page):
                require(recipient == RECIPIENT, '送信先の固定')
                message = make_message(sender, recipient, scheduled_at,
                                       'E2Eテスト：保存済みの全言語テストデータを使用しています。正式データではありません。\n' + plain, page)
                message.replace_header('Subject', '[E2Eテスト] ' + str(message['Subject']))
                message.replace_header('Message-ID', f'<e2e-{run_id}@research-viewer.local>')
                require(message.get_body(preferencelist=('html',)).get_content().rstrip('\n') == report.rstrip('\n'), 'メールHTML本文と出力HTML')
                attachments = list(message.iter_attachments())
                require(len(attachments) == 1 and attachments[0].get_content().rstrip('\n') == report.rstrip('\n'), '添付HTMLと出力HTML')
                require(len(re.findall(r'^\d+\. ', plain, re.M)) == min(len(latest['games']), 100), 'テキスト本文のランキング件数')
                viewer.atomic_write(work / 'message.eml', message.as_string(), mode=0o600)
                return message

            def deliver(configured, message):
                require(not captured and configured['recipients'] == [RECIPIENT] and str(message['To']) == RECIPIENT, '実送信は指定宛先へ1通のみ')
                captured.append(message)
                require(all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in hashes.items()), '通常データ・設定の不変性')
                if send_email:
                    result['mail_status'] = 'sending'
                    try:
                        send(configured, message)
                    except mailer.MailError as exc:
                        result['mail_status'] = 'uncertain' if exc.uncertain else 'failed'
                        raise
                    result['mail_status'] = 'sent'

            with patch('mailer.load_settings', return_value=config), patch('mailer.make_message', side_effect=test_message), patch('mailer.send', side_effect=deliver):
                viewer.send_reports(work, store, history, viewer.scheduled_time(latest))
            require(len(captured) == 1, 'メールの生成・処理件数')
            require([s['status'] for s in store.mail_status()] == ['sent'], '隔離DBの送信処理完了（オフライン時は模擬）')
        result['status'] = 'passed'
    except mailer.MailError as exc:
        result['mail_status'] = 'uncertain' if exc.uncertain else 'failed'
        raise
    finally:
        if result['mail_status'] == 'sending':
            result['mail_status'] = 'failed_or_uncertain'
        viewer.atomic_write(work / 'result.json', json.dumps(result, ensure_ascii=False, indent=2) + '\n', mode=0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true', help=f'最後に{RECIPIENT}へ実メールを1通送信')
    args = parser.parse_args()
    try:
        print(json.dumps(run_e2e(viewer.ROOT, send_email=args.send), ensure_ascii=False, indent=2))
    except (viewer.ViewerError, mailer.MailError, OSError) as exc:
        print(f'E2E失敗: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
