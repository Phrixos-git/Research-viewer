#!/usr/bin/env python3
"""Twitch collection, paired comparisons and local scheduling (stdlib only)."""
import argparse
import audience
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import html
import json
import mailer
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import webbrowser
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
REMOTE = 'https://github.com/Phrixos-git/Research-viewer.git'
HOURS = range(19, 24)
DISPLAY_LIMIT = 100
ROOT = Path(__file__).resolve().parent


class ViewerError(Exception):
    """Safe to display; never includes credentials or upstream response bodies."""


class Client:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = self._token()

    @staticmethod
    def request(method, url, headers=None, body=None):
        for attempt in range(4):
            try:
                req = Request(url, data=body, headers=headers or {}, method=method)
                with urlopen(req, timeout=30) as response:
                    return json.load(response)
            except HTTPError as exc:
                status = exc.code
                reset = exc.headers.get('Ratelimit-Reset', '')
                retry_after = exc.headers.get('Retry-After', '')
                exc.close()
                if attempt < 3 and (status == 429 or status >= 500):
                    try:
                        delay = max(1, float(retry_after)) if retry_after else max(1, float(reset) - time.time() + 1) if reset else 2 ** attempt
                    except ValueError:
                        delay = 2 ** attempt
                    if delay > 60:
                        raise ViewerError('APIの待機時間が長いため、この回の取得を中止しました。') from None
                    time.sleep(delay)
                    continue
                raise ViewerError(f'Twitch APIがHTTP {status}を返しました。認証・制限・稼働状況を確認してください。') from None
            except (URLError, TimeoutError, OSError):
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise ViewerError('Twitchに接続できません。ネットワークを確認してください。') from None
            except (ValueError, TypeError):
                raise ViewerError('Twitchから有効なJSONを取得できませんでした。') from None
        raise ViewerError('Twitchの取得に失敗しました。')

    def _token(self):
        body = urlencode(dict(client_id=self.client_id, client_secret=self.client_secret,
                              grant_type='client_credentials')).encode()
        response = self.request('POST', 'https://id.twitch.tv/oauth2/token',
                                {'Content-Type': 'application/x-www-form-urlencoded'}, body)
        token = response.get('access_token') if isinstance(response, dict) else None
        if not isinstance(token, str) or not token:
            raise ViewerError('Twitchのアクセストークンを取得できませんでした。')
        return token

    def get(self, path, **params):
        return self.request('GET', 'https://api.twitch.tv/helix' + path + '?' + urlencode(params, doseq=True),
                            {'Client-Id': self.client_id, 'Authorization': 'Bearer ' + self.token})

    def pages(self, path, **params):
        seen = set()
        while True:
            result = self.get(path, first=100, **params)
            if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                raise ViewerError('Twitchの一覧データ形式が不正です。')
            yield from result['data']
            cursor = result.get('pagination', {}).get('cursor')
            if not cursor:
                return
            if not isinstance(cursor, str) or cursor in seen:
                raise ViewerError('ページ送りが循環したため取得を中止しました。')
            seen.add(cursor)
            params['after'] = cursor

    def collect(self, limit=1000):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ViewerError('取得上限は1〜1000件で指定してください。')
        streams, seen = [], set()
        for row in self.pages('/streams', type='live'):
            if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']
                    or not isinstance(row.get('game_id'), str)
                    or not isinstance(row.get('user_id'), str) or not row['user_id']
                    or type(row.get('viewer_count')) is not int or row['viewer_count'] < 0):
                raise ViewerError('配信情報の形式が不正です。')
            if row['id'] in seen:
                continue
            seen.add(row['id'])
            streams.append(row)
            if len(streams) == limit:
                break
        ids = list(dict.fromkeys(row['game_id'] for row in streams if row['game_id']))
        games = {}
        for start in range(0, len(ids), 100):
            response = self.get('/games', id=ids[start:start + 100])
            if not isinstance(response, dict) or not isinstance(response.get('data'), list):
                raise ViewerError('ゲーム情報の形式が不正です。')
            for row in response['data']:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not isinstance(row.get('name'), str):
                    raise ViewerError('ゲーム情報の形式が不正です。')
                if row.get('igdb_id'):
                    games[row['id']] = dict(id=row['id'], name=row['name'], viewers=0, broadcasters={})
        included = 0
        active = set()
        for stream in streams:
            if stream['game_id'] in games:
                games[stream['game_id']]['viewers'] += stream['viewer_count']
                counts = games[stream['game_id']]['broadcasters']
                counts[stream['user_id']] = counts.get(stream['user_id'], 0) + stream['viewer_count']
                active.add(stream['game_id'])
                included += 1
        return dict(stream_count=len(streams), included_stream_count=included,
                    games=sorted((games[gid] for gid in active), key=lambda g: (-g['viewers'], g['id'])))


def client_from_config(root):
    try:
        config = json.loads((root / '.local/config.json').read_text())
        values = [config[key].strip() for key in ('client_id', 'client_secret')]
        if not all(values):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ViewerError('認証情報が未設定です。python3 viewer.py setup で設定してください。') from None
    return Client(*values)


def slot_for(now):
    local = now.astimezone(JST)
    if local.hour not in HOURS or local.minute >= 10:
        return None
    return local.replace(minute=0, second=0, microsecond=0)


def scheduled_time(snapshot):
    return datetime.fromisoformat(snapshot['scheduled_at']).astimezone(JST)


def compare_period(history, day, end_hour):
    indexed = {(scheduled_time(s).date(), scheduled_time(s).hour): s for s in history}
    pairs = defaultdict(list)
    names = {}
    for hour in range(19, end_hour + 1):
        today = indexed.get((day, hour))
        yesterday = indexed.get((day - timedelta(days=1), hour))
        if today is None or yesterday is None:
            continue
        before = {g['id']: g for g in yesterday['games']}
        for game in today['games']:
            if game['id'] in before:
                pairs[game['id']].append((before[game['id']]['viewers'], game['viewers']))
                names[game['id']] = game['name']
    rows = []
    for gid, values in pairs.items():
        before = sum(v[0] for v in values) / len(values)
        after = sum(v[1] for v in values) / len(values)
        rows.append(comparison_row(gid, names[gid], before, after, len(values), end_hour - 18))
    return sorted(rows, key=lambda r: (-r['change'], r['id']))


def comparison_row(gid, name, before, after, samples, expected):
    return dict(id=gid, name=name, before=before, after=after, change=after - before,
                percent=(after / before - 1) * 100 if before else None,
                samples=samples, expected=expected)


def report_rows(snapshot, history):
    slot = scheduled_time(snapshot)
    if slot.hour in (21, 23):
        return compare_period(history, slot.date(), slot.hour), f'前日比較：両日の19〜{slot.hour}時の平均同時視聴者数'
    previous = next((s for s in history if scheduled_time(s) == slot - timedelta(hours=1)), None)
    label = f'直前比較：{slot.hour - 1}時 → {slot.hour}時'
    if previous is None or slot.hour == 19:
        return [], label
    before = {g['id']: g for g in previous['games']}
    rows = [comparison_row(g['id'], g['name'], before[g['id']]['viewers'], g['viewers'], 1, 1)
            for g in snapshot['games'] if g['id'] in before]
    return sorted(rows, key=lambda r: (-r['change'], r['id'])), label


def rising_titles(rows, category='normal'):
    eligible = [r for r in rows if r['category'] == category]
    return sorted(eligible, key=lambda r: (-r['change'], r['id']))[:3]


def rising_groups(snapshot, history):
    slot = scheduled_time(snapshot)
    daily = slot.hour in (21, 23)
    indexed = {scheduled_time(s): s for s in history}
    indexed[slot] = snapshot
    groups = []
    for hour in (range(19, slot.hour + 1) if daily else [slot.hour]):
        current_slot = slot.replace(hour=hour)
        previous_slot = current_slot - (timedelta(days=1) if daily else timedelta(hours=1))
        today = indexed.get(current_slot)
        previous = indexed.get(previous_slot) if daily or hour != 19 else None
        label = f'前日比較：{hour}時' if daily else f'直前比較：{hour - 1}時 → {hour}時'
        rows = []
        if today is not None and previous is not None:
            before = {g['id']: g for g in previous['games']}
            for game in today['games']:
                if game['id'] in before:
                    old = before[game['id']]
                    row = comparison_row(game['id'], game['name'], old['viewers'], game['viewers'], 1, 1)
                    row.update(audience.classify_game(old, game))
                    rows.append(row)
        groups.append((label, rows))
    return groups


def rise_detail(row):
    remaining = f"{row['remaining_percent']:+.1f}%" if row['remaining_percent'] is not None else '算出不可'
    return (f"最多寄与配信者ID {row['excluded_broadcaster']}：+{row['contribution']:,}人・寄与率{row['contribution_percent']:.1f}% / "
            f"除外後 {row['remaining_before']:,} → {row['remaining_after']:,}人（{remaining}）")


def ranking_changes(snapshot, history):
    slot = scheduled_time(snapshot)
    if slot.hour != 23:
        return None
    prev_rank = {}
    prev_slot = slot - timedelta(days=1)
    for s in history:
        if scheduled_time(s) == prev_slot:
            prev_rank = {g["id"]: i for i, g in enumerate(s["games"], start=1)}
            break
    result = {}
    for i, g in enumerate(snapshot["games"][:DISPLAY_LIMIT], start=1):
        gid = g["id"]
        if gid not in prev_rank:
            result[gid] = "★"
        elif prev_rank[gid] == i:
            result[gid] = "-"
        elif prev_rank[gid] > i:
            result[gid] = "↑"
        else:
            result[gid] = "↓"
    return result


def render_ranking(games, changes=None):
    """Render a game viewers ranking table as an HTML string.

    games: list of dicts with "id", "name", "viewers"; order is preserved.
    """
    games = games[:DISPLAY_LIMIT]
    max_viewers = max((g['viewers'] for g in games), default=0)

    cell = (
        "padding:8px 6px;line-height:1.4;white-space:normal;"
        "word-wrap:break-word;overflow-wrap:anywhere;"
        "border-bottom:1px solid #30354a;vertical-align:middle;"
    )
    change_style = cell + 'text-align:center;'
    change_header = (f'<th scope="col" style="{change_style}border-bottom:0;">前日比</th>'
                     if changes is not None else '')
    rank_style = ('width:4.5em;min-width:4.5em;text-align:center;'
                  'white-space:nowrap;word-wrap:normal;overflow-wrap:normal;')
    badge = {
        1: "background-color:#3d476b;color:#e3e8f7;",
        2: "background-color:#39415c;color:#dfe4f0;",
        3: "background-color:#463f5e;color:#e6e0f2;",
    }
    rows = []
    for idx, g in enumerate(games, start=1):
        v = g['viewers']
        if max_viewers > 0 and v > 0:
            pct = max(2, min(100, round((v / max_viewers) ** 0.5 * 100)))
        else:
            pct = 0
        name = html.escape(g['name'])
        badge_style = badge.get(idx, "background-color:#2b3049;color:#c7cde0;")
        number = f"{v:,}"
        row_color = {1: '#24243a', 2: '#222739', 3: '#29243a'}.get(idx)
        row_style = f'background-color:{row_color};' if row_color else ''
        change_cell = (f'<td class="rank-change" style="{change_style}">{html.escape(changes[g["id"]])}</td>'
                       if changes is not None else '')
        rows.append(
            f'<tr style="{row_style}">'
            f'<td style="{cell}{rank_style}font-variant-numeric:tabular-nums;">'
            f'<span class="rank-badge rank-{idx}" style="display:inline-block;white-space:nowrap;padding:2px 10px;border-radius:999px;font-weight:600;{badge_style}">{idx}</span>'
            "</td>"
            f'<th scope="row" style="{cell}min-width:0;text-align:left;">{name}</th>'
            f'<td style="{cell}text-align:right;font-variant-numeric:tabular-nums;">'
            '<div class="viewers" style="display:flex;align-items:center;justify-content:flex-start;gap:10px;">'
            f'<span class="viewer-number" style="flex:1;min-width:0;text-align:right;">{number}</span>'
            '<span class="bar-track" aria-hidden="true" style="display:inline-block;width:140px;max-width:45%;height:8px;flex:0 0 140px;">'
            f'<span class="viewer-bar" aria-hidden="true" style="display:block;height:100%;width:{pct}%;background-color:#5b4a9e;background-image:linear-gradient(to right,#5b4a9e,#90e0bc33);border-radius:4px;"></span>'
            "</span>"
            "</div>"
            "</td>"
            f'{change_cell}'
            "</tr>"
        )
    return (
        '<table class="report-table ranking" style="max-width:100%;table-layout:auto;border-collapse:collapse;">'
        '<thead style="position:sticky;top:0;background-color:#202337;box-shadow:0 1px 0 #46415e;"><tr>'
        f'<th scope="col" style="{cell}border-bottom:0;{rank_style}">順位</th>'
        f'<th scope="col" style="{cell}border-bottom:0;text-align:left;">ゲームタイトル</th>'
        f'<th scope="col" style="{cell}border-bottom:0;text-align:right;">同時視聴者数（人）</th>'
        f'{change_header}'
        "</tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )


def render_html(snapshot, history):
    esc = html.escape
    rows, label = report_rows(snapshot, history)
    slot = scheduled_time(snapshot)
    def number(value):
        return f'{value:,.1f}'.removesuffix('.0')
    def percent(row):
        return f"{row['percent']:+.1f}%" if row['percent'] is not None else '算出不可（比較元0）'
    def table(headings, widths, records, row_header, kind):
        cell_style = ('padding:8px 6px;line-height:1.4;text-align:right;vertical-align:top;border-bottom:1px solid #30354a;'
                      'font-variant-numeric:tabular-nums;'
                      'white-space:normal;word-wrap:break-word;overflow-wrap:anywhere;')
        header = ''.join(f'<th scope="col" width="{width}%" style="{cell_style}border-bottom:0;width:{width}%;'
                         f'{"text-align:left;" if i == row_header else ""}">{esc(label)}</th>'
                         for i, (label, width) in enumerate(zip(headings, widths)))
        body = []
        for record in records:
            cells = []
            for i, (label, value) in enumerate(zip(headings, record)):
                tag = 'th' if i == row_header else 'td'
                scope = ' scope="row"' if i == row_header else ''
                style = cell_style + ('text-align:left;' if i == row_header else '')
                mobile_label = (f'<span class="mobile-label" style="display:none;">{esc(label)}：</span>'
                                if kind == 'comparison' and i != row_header else '')
                cells.append(f'<{tag}{scope} style="{style}">{mobile_label}{esc(str(value))}</{tag}>')
            body.append('<tr>' + ''.join(cells) + '</tr>')
        return (f'<table class="report-table {kind}" width="100%" style="width:100%;table-layout:fixed;border-collapse:collapse;">'
                f'<thead style="position:sticky;top:0;background-color:#202337;box-shadow:0 1px 0 #46415e;"><tr>{header}</tr></thead><tbody>{"".join(body)}</tbody></table>')
    def render_cards(selected, category):
        card_class = 'rise' if category == 'normal' else 'single-rise rise'
        return ''.join(f'<article class="{card_class}" style="background-color:#202337;border:1px solid #46415e;border-radius:14px;padding:20px;">'
                       f'<h3>{esc(r["name"])}</h3><strong>+{number(r["change"])} 人</strong>'
                       f'<p>{percent(r)}</p><small>{esc(rise_detail(r))}</small></article>' for r in selected)
    sections = []
    for group_label, group_rows in rising_groups(snapshot, history):
        normal = rising_titles(group_rows)
        single = rising_titles(group_rows, 'single')
        normal_cards = render_cards(normal, 'normal')
        if not normal_cards:
            normal_cards = '<p>該当タイトルなし</p>' if group_rows else '<p>比較データなし</p>'
        single_section = ('<h4>単一配信者による急増</h4><div class="cards">'
                          + render_cards(single, 'single') + '</div>') if single else ''
        unknown_count = sum(r['category'] == 'unavailable' for r in group_rows)
        unknown_note = (f'<p class="note" style="color:#b5bfd2;">判定不可：{unknown_count}タイトル（配信者別内訳が未取得、または除外後の増加率が算出不可）</p>'
                        if unknown_count else '')
        sections.append(f'<section><h3>{esc(group_label)}</h3><h4>通常の急上昇3選</h4>'
                        f'<div class="cards">{normal_cards}</div>{single_section}{unknown_note}</section>')
    cards = ''.join(sections)
    ranking = render_ranking(snapshot['games'], ranking_changes(snapshot, history))
    ranking_note = ('<p class="note" style="color:#b5bfd2;font-size:13px;">前日23時からの順位変動：- 同順位／↑ 上昇／↓ 下降／★ 新規（前日の記録がない場合も含む）</p>'
                    if slot.hour == 23 else '')
    comparison = table(['ゲーム', '比較元', '今回', '増減人数', '増減率', '比較件数／予定件数'],
                       [30, 14, 14, 14, 14, 14],
                       [(r['name'], number(r['before']), number(r['after']), f'{r["change"]:+,.1f}',
                         percent(r), f'{r["samples"]}/{r["expected"]}') for r in rows], 0, 'comparison') if rows else '<p>比較データなし</p>'
    awaiting_comparison = slot.hour in (21, 23) and not rows
    comparison_note = ('<p class="note" style="color:#b5bfd2;margin:12px 0;">前日比較データは蓄積中です</p>'
                       if awaiting_comparison else '')
    comparison_sections = '' if awaiting_comparison else (
        '<h2>急上昇タイトル3選</h2><p>各枠最大3タイトル · 全体・最多寄与配信者の除外後とも25％以上・増加人数順</p>'
        f'{cards}<h2>{esc(label)}</h2>'
        '<p class="note">両方に記録がある同じ時刻だけで比較。取得範囲外・欠測は0で補完しません。件数不足の比較は部分集計です。</p>'
        f'{comparison}')
    chip_style = ('display:inline-block;border:1px solid #46415e;border-radius:999px;padding:4px 12px;'
                  'margin:0 8px 8px 0;background-color:#202337;color:#e9edf5;font-size:13px;line-height:1.4;')
    chips = [f"表示 {min(len(snapshot['games']), DISPLAY_LIMIT):,} / 集計 {len(snapshot['games']):,} タイトル",
             f"{snapshot['included_stream_count']:,}配信（ゲーム集計対象）",
             f'{slot:%Y年%m月%d日 %H:%M} JST']
    metadata = ''.join(f'<span class="meta-chip" style="{chip_style}">{esc(text)}</span>' for text in chips)
    font_family = '"Hiragino Sans","Noto Sans JP","Yu Gothic UI",system-ui,sans-serif'
    inline_font = esc(font_family)
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Twitchゲーム視聴者数 | {slot:%Y-%m-%d %H:%M}</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#10121b;color:#e9edf5;font:16px/1.7 {font_family}}}
main{{max-width:1100px;margin:auto;padding:32px 20px}}h1{{font-size:clamp(1.6rem,4vw,2.7rem)}}h2{{margin-top:40px}}
small,.note{{color:#b5bfd2}}.badge{{color:#c8b7ff;letter-spacing:.08em}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:16px}}
article{{background:#202337;border:1px solid #46415e;border-radius:14px;padding:20px}}article h3{{margin-top:0}}strong{{font-size:1.5rem;color:#90e0bc}}
.report-table thead{{background:#202337}}.report-table tbody tr:hover{{background:#1d2232}}footer{{margin-top:40px;color:#b5bfd2}}
@media screen and (max-width:600px){{
main{{padding:20px 12px!important}}h1{{font-size:24px}}.cards{{display:block}}.rise{{margin-bottom:12px}}
.ranking .viewers{{flex-direction:column;align-items:stretch!important;gap:4px!important}}
.ranking .bar-track{{order:2;width:100%!important;max-width:100%!important;height:4px!important;flex:none!important}}
.ranking .viewer-number{{text-align:right}}
.comparison,.comparison tbody,.comparison tr,.comparison th[scope=row],.comparison td{{display:block;width:100%!important;box-sizing:border-box}}
.comparison thead{{display:none}}.comparison tr{{margin-bottom:16px;border:1px solid #30354a}}
.comparison td{{text-align:left!important}}.comparison .mobile-label{{display:inline!important;font-weight:bold}}
}}
</style></head><body bgcolor="#10121b" style="margin:0;padding:0;background-color:#10121b;color:#e9edf5;font:16px/1.7 {inline_font};">
<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#10121b" style="width:100%;table-layout:fixed;border-collapse:collapse;background-color:#10121b;color:#e9edf5;white-space:normal;">
<tr><td bgcolor="#10121b" align="left" style="padding:0;border:0;text-align:left;background-color:#10121b;color:#e9edf5;font:16px/1.7 {inline_font};">
<main style="max-width:1100px;box-sizing:border-box;margin:0 auto;padding:32px 20px;background-color:#10121b;color:#e9edf5;word-wrap:break-word;overflow-wrap:anywhere;">
<p class="badge">TWITCH / GAME VIEWERS</p><h1>ゲーム別 視聴者数ランキング（上位配信から集計）</h1>
<div class="metadata" style="margin:16px 0 0;">{metadata}</div>
<p class="note" style="font-size:13px;color:#b5bfd2;margin:0;">取得配信数：{snapshot["stream_count"]:,} / 最大1,000件</p>
{comparison_note}
{comparison_sections}
<h2>タイトルランキング（上位100件）</h2>{ranking_note}{ranking}
<footer style="margin-top:40px;color:#b5bfd2;font-size:13px;">Twitch公式APIの取得期間中の集計値です。配信別視聴者数の合計で、ユニーク人数ではありません。<br>
Twitch全体の上位最大1,000配信のうちIGDB ID登録済みゲームが対象です。ゲームの全配信の合計ではありません。ページ取得中の変動により順位・人数に誤差が生じ得ます。
<p class="note" style="color:#b5bfd2;">収集開始：{esc(snapshot['started_at'])}<br>収集終了：{esc(snapshot['finished_at'])}</p></footer>
</main></td></tr></table></body></html>'''


def atomic_write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_snapshot(snapshot):
    try:
        times = {}
        for key in ('scheduled_at', 'started_at', 'finished_at'):
            stamp = datetime.fromisoformat(snapshot[key])
            if stamp.utcoffset() is None:
                raise ValueError
            times[key] = stamp.astimezone(JST)
        slot = times['scheduled_at']
        if slot.hour not in HOURS or slot.minute or slot.second or slot.microsecond:
            raise ValueError
        if times['finished_at'] < times['started_at']:
            raise ValueError
        games = snapshot['games']
        total, included = snapshot['stream_count'], snapshot['included_stream_count']
        if type(total) is not int or type(included) is not int or not 0 <= included <= total <= 1000:
            raise ValueError
        if not isinstance(games, list) or len(games) > included or (not games and included):
            raise ValueError
        normalized_games = []
        for game in games:
            if not isinstance(game['id'], str) or not game['id'] or not isinstance(game['name'], str):
                raise ValueError
            if type(game['viewers']) is not int or game['viewers'] < 0:
                raise ValueError
            item = dict(id=game['id'], name=game['name'], viewers=game['viewers'])
            if 'broadcasters' in game:
                audience.validate_counts(game['broadcasters'], game['viewers'])
                item['broadcasters'] = dict(game['broadcasters'])
            normalized_games.append(item)
        return dict(**{key: stamp.isoformat() for key, stamp in times.items()},
                    games=normalized_games, stream_count=total, included_stream_count=included)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ViewerError('取得記録の日時・ゲーム・視聴者数が不正です。保存を中断します。') from None


class Store:
    """The local SQLite database is the source of collection history."""

    def __init__(self, root):
        local = root / '.local'
        local.mkdir(exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(local / 'viewers.sqlite3')
        try:
            columns = {r[1] for r in self.db.execute('PRAGMA table_info(collection_runs)')}
            if columns and not {'stream_count', 'included_stream_count'} <= columns:
                raise ViewerError('旧仕様のSQLiteです。既存DB・JSONを別の場所に保管してから、新方式の履歴を新規に記録してください。自動移行は行いません。')
            self.db.execute('PRAGMA foreign_keys = ON')
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY,
                    scheduled_at TEXT NOT NULL UNIQUE,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    stream_count INTEGER NOT NULL CHECK (typeof(stream_count) = 'integer' AND stream_count BETWEEN 0 AND 1000),
                    included_stream_count INTEGER NOT NULL CHECK (typeof(included_stream_count) = 'integer' AND included_stream_count BETWEEN 0 AND stream_count)
                );
                CREATE TABLE IF NOT EXISTS viewer_counts (
                    run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL,
                    game_title TEXT NOT NULL,
                    viewer_count INTEGER NOT NULL
                        CHECK (typeof(viewer_count) = 'integer' AND viewer_count >= 0),
                    PRIMARY KEY (run_id, game_id)
                );
                CREATE TABLE IF NOT EXISTS game_audiences (
                    run_id INTEGER NOT NULL,
                    game_id TEXT NOT NULL,
                    counts_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, game_id),
                    FOREIGN KEY (run_id, game_id) REFERENCES viewer_counts(run_id, game_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS mail_deliveries (
                    run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')),
                    attempted_at TEXT,
                    sent_at TEXT,
                    PRIMARY KEY (run_id, recipient)
                );
            """)
        except (sqlite3.Error, ViewerError) as exc:
            self.db.close()
            if isinstance(exc, ViewerError):
                raise
            raise ViewerError('SQLiteを開けません。データベースの状態・権限を確認してください。') from None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.close()

    def _insert(self, snapshot):
        snapshot = validate_snapshot(snapshot)
        cursor = self.db.execute(
            'INSERT INTO collection_runs (scheduled_at, started_at, finished_at, stream_count, included_stream_count) VALUES (?, ?, ?, ?, ?)',
            tuple(snapshot[key] for key in ('scheduled_at', 'started_at', 'finished_at', 'stream_count', 'included_stream_count')))
        self.db.executemany(
            'INSERT INTO viewer_counts (run_id, game_id, game_title, viewer_count) VALUES (?, ?, ?, ?)',
            [(cursor.lastrowid, g['id'], g['name'], g['viewers']) for g in snapshot['games']])
        self.db.executemany(
            'INSERT INTO game_audiences (run_id, game_id, counts_json) VALUES (?, ?, ?)',
            [(cursor.lastrowid, g['id'], json.dumps(g['broadcasters'], ensure_ascii=False, sort_keys=True))
             for g in snapshot['games'] if 'broadcasters' in g])

    def save(self, snapshot):
        try:
            with self.db:
                self._insert(snapshot)
        except sqlite3.Error:
            raise ViewerError('SQLiteへの保存に失敗しました。重複やデータベースの状態を確認してください。この回は保存していません。') from None

    def history(self):
        try:
            snapshots = {}
            for rid, scheduled, started, finished, total, included in self.db.execute(
                    'SELECT id, scheduled_at, started_at, finished_at, stream_count, included_stream_count FROM collection_runs ORDER BY scheduled_at'):
                snapshots[rid] = dict(scheduled_at=scheduled, started_at=started, finished_at=finished, games=[], stream_count=total, included_stream_count=included)
            for rid, gid, title, count in self.db.execute(
                    'SELECT run_id, game_id, game_title, viewer_count FROM viewer_counts '
                    'ORDER BY run_id, viewer_count DESC, game_id'):
                snapshots[rid]['games'].append(dict(id=gid, name=title, viewers=count))
            indexed_games = {(rid, g['id']): g for rid, s in snapshots.items() for g in s['games']}
            for rid, gid, counts in self.db.execute('SELECT run_id, game_id, counts_json FROM game_audiences'):
                game = indexed_games[(rid, gid)]
                decoded = json.loads(counts)
                audience.validate_counts(decoded, game['viewers'])
                game['broadcasters'] = decoded
            return list(snapshots.values())
        except (sqlite3.Error, KeyError, ValueError, TypeError):
            raise ViewerError('SQLiteの履歴を読み取れません。データベースの状態を確認してください。') from None

    def restore(self, snapshots):
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if self.db.execute('SELECT 1 FROM collection_runs LIMIT 1').fetchone():
                    raise ViewerError('既存の記録があるため復元できません。データベースは上書きしません。')
                for snapshot in snapshots:
                    self._insert(snapshot)
        except sqlite3.Error:
            raise ViewerError('復元データが重複または不正です。復元全体を取り消しました。') from None


    def queue_mail(self, scheduled_at, recipient):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO mail_deliveries (run_id, recipient, status) "
                "SELECT id, ?, 'pending' FROM collection_runs WHERE scheduled_at = ?",
                (recipient, scheduled_at))

    def set_mail_status(self, scheduled_at, recipient, status):
        now = datetime.now(JST).isoformat()
        with self.db:
            self.db.execute(
                'UPDATE mail_deliveries SET status = ?, '
                "attempted_at = CASE WHEN ? = 'sending' THEN ? ELSE attempted_at END, "
                "sent_at = CASE WHEN ? = 'sent' THEN ? ELSE NULL END "
                'WHERE run_id = (SELECT id FROM collection_runs WHERE scheduled_at = ?) AND recipient = ?',
                (status, status, now, status, now, scheduled_at, recipient))

    def mail_status(self):
        return [dict(scheduled_at=slot, recipient=recipient, status=status, attempted_at=attempt, sent_at=sent)
                for slot, recipient, status, attempt, sent in self.db.execute(
                    'SELECT r.scheduled_at, m.recipient, m.status, m.attempted_at, m.sent_at '
                    'FROM mail_deliveries m JOIN collection_runs r ON r.id = m.run_id '
                    'ORDER BY r.scheduled_at, m.recipient')]


@contextmanager
def collection_lock(root):
    local = root / '.local'
    local.mkdir(exist_ok=True, mode=0o700)
    with (local / 'collection.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ViewerError('取得または復元の処理はすでに実行中です。') from None
        yield


def restore(root):
    paths = sorted((root / 'data').glob('*/*.json'))
    if not paths:
        raise ViewerError('復元するJSONがありません。GitHubから取得記録を用意してください。')
    def snapshots():
        for path in paths:
            try:
                snapshot = validate_snapshot(json.loads(path.read_text(encoding='utf-8')))
                slot = scheduled_time(snapshot)
                if path != root / 'data' / f'{slot:%Y-%m-%d}' / f'{slot:%H}.json':
                    raise ValueError
            except (OSError, ValueError):
                raise ViewerError('復元JSONの内容またはファイル名が不正です。復元全体を取り消しました。') from None
            yield snapshot
    with collection_lock(root), Store(root) as store:
        store.restore(snapshots())
    print(f'{len(paths)}回分の取得記録をSQLiteに復元しました。')


def export_reports(root, history, slot):
    for snapshot in history:
        stamp = scheduled_time(snapshot)
        path = root / 'data' / f'{stamp:%Y-%m-%d}' / f'{stamp:%H}.json'
        content = json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n'
        if not path.exists() or path.read_text(encoding='utf-8', errors='replace') != content:
            atomic_write(path, content)
        if stamp.hour in (21, 23):
            report = root / 'output' / f'{stamp:%Y-%m-%d}' / f'{stamp:%H}.html'
            if not report.exists() or stamp == slot:
                atomic_write(report, render_html(snapshot, history))
    latest = max(history, key=scheduled_time)
    atomic_write(root / 'output/index.html', render_html(latest, history))


def send_reports(root, store, history, slot):
    if slot.hour != 23:
        return
    try:
        config = mailer.load_settings(root)
    except mailer.MailError as exc:
        raise ViewerError(str(exc)) from None
    if config is None:
        return
    for recipient in config['recipients']:
        store.queue_mail(slot.isoformat(), recipient)
    indexed = {s['scheduled_at']: s for s in history}
    failures = 0
    for delivery in store.mail_status():
        if delivery['recipient'] not in config['recipients'] or delivery['status'] not in ('pending', 'failed'):
            continue
        snapshot = indexed[delivery['scheduled_at']]
        lines = [f"Twitchゲーム視聴者数 {delivery['scheduled_at']}",
                 f"上位最大1,000配信から集計。取得 {snapshot['stream_count']}件、ゲーム集計対象 {snapshot['included_stream_count']}件",
                 'ゲーム全体の視聴者数ではありません。',
                 '急上昇タイトル3選（各枠最大3タイトル・全体と最多寄与配信者の除外後とも25％以上、増加人数順）']
        for group_label, group_rows in rising_groups(snapshot, history):
            lines.append(group_label)
            normal = rising_titles(group_rows)
            single = rising_titles(group_rows, 'single')
            lines.append('通常の急上昇3選')
            lines.extend(f"{r['name']}: +{r['change']:,.1f}人 / {rise_detail(r)}" for r in normal)
            if not normal:
                lines.append('該当タイトルなし' if group_rows else '比較データなし')
            if single:
                lines.append('単一配信者による急増')
                lines.extend(f"{r['name']}: +{r['change']:,.1f}人 / {rise_detail(r)}" for r in single)
            unknown_count = sum(r['category'] == 'unavailable' for r in group_rows)
            if unknown_count:
                lines.append(f'判定不可：{unknown_count}タイトル（配信者別内訳が未取得、または除外後の増加率が算出不可）')
        lines.append('タイトルランキング（上位100件）')
        lines.extend(f"{i}. {g['name']}: {g['viewers']:,}人" for i, g in enumerate(snapshot['games'][:DISPLAY_LIMIT], 1))
        lines.append('全体の前日比較と取得日時はHTML本文・添付HTMLをご覧ください。')
        message = mailer.make_message(config['username'], delivery['recipient'], delivery['scheduled_at'],
                                      '\n'.join(lines), render_html(snapshot, history))
        store.set_mail_status(delivery['scheduled_at'], delivery['recipient'], 'sending')
        try:
            mailer.send(config, message)
        except mailer.MailError as exc:
            store.set_mail_status(delivery['scheduled_at'], delivery['recipient'], 'uncertain' if exc.uncertain else 'failed')
            failures += 1
        else:
            store.set_mail_status(delivery['scheduled_at'], delivery['recipient'], 'sent')
    if failures:
        raise ViewerError(f'{failures}件のメール送信を完了できませんでした。mail-statusで状態を確認してください。')


def git(root, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True, env=env, timeout=120)
    if result.returncode:
        raise ViewerError('Git操作に失敗しました。Repository・認証・接続・競合を確認してください。取得済みのデータは保持します。')
    return result.stdout.rstrip('\n')


def check_repository(root, expected_remote=REMOTE):
    if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root.resolve():
        raise ViewerError('プログラムのフォルダー自体をGit Repositoryにしてください。')
    remote = git(root, 'remote', 'get-url', '--push', 'origin')
    allowed = {expected_remote}
    if expected_remote == REMOTE:
        allowed.add('git@github.com:Phrixos-git/Research-viewer.git')
    if remote not in allowed:
        raise ViewerError('originのpush先が指定のResearch-viewer Repositoryと一致しません。')
    if git(root, 'symbolic-ref', '--short', 'HEAD') != 'main':
        raise ViewerError('自動保存にはmainブランチを使用してください。')


def publish(root, expected_remote=REMOTE):
    check_repository(root, expected_remote)
    if git(root, 'diff', '--cached', '--name-only'):
        raise ViewerError('ステージ済みの変更があります。無関係な変更のcommitを防ぐため中断しました。')
    status = git(root, 'status', '--porcelain', '--untracked-files=all', '-z')
    for item in status.split('\0'):
        if not item:
            continue
        name = item[3:]
        if not name.startswith(('data/', 'output/')):
            raise ViewerError('data/output以外に未commitの変更があります。先に開発作業を完了してください。')
    existing = [p for p in ('data', 'output') if (root / p).is_dir()]
    if existing:
        git(root, 'add', '--', *existing)
        if git(root, 'diff', '--cached', '--name-only'):
            git(root, 'commit', '-m', 'Record Twitch viewers and update HTML reports')
    git(root, 'push', 'origin', 'HEAD:refs/heads/main')


def run(root, now=None, publish_result=True):
    now = now or datetime.now(JST)
    slot = slot_for(now)
    if slot is None:
        print('対象時刻外または10分以上の開始遅延のため、収集を見送ります。')
        return False
    with collection_lock(root), Store(root) as store:
        history = store.history()
        if not history and any((root / 'data').glob('*/*.json')):
            raise ViewerError('SQLiteが空ですがJSON履歴があります。restoreで復元してから収集してください。')
        if not any(scheduled_time(s) == slot for s in history):
            started = datetime.now(JST).isoformat()
            result = client_from_config(root).collect()
            store.save(dict(scheduled_at=slot.isoformat(), started_at=started,
                            finished_at=datetime.now(JST).isoformat(), **result))
            history = store.history()
        export_reports(root, history, slot)
        errors = []
        if publish_result:
            try:
                publish(root)
            except (ViewerError, OSError, subprocess.TimeoutExpired):
                errors.append('GitHubへのpushに失敗しました。')
        try:
            send_reports(root, store, history, slot)
        except (ViewerError, sqlite3.Error):
            errors.append('メール通知を完了できませんでした。mail-statusとメール設定を確認してください。')
        if errors:
            raise ViewerError(' '.join(errors) + ' SQLiteの取得記録は保持しています。')
        print(f'{slot:%Y-%m-%d %H:%M} の記録をSQLiteへ保存し、JSONとHTMLを出力しました。')
        return True


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def timer_units(root):
    service = f'''[Unit]
Description=Twitch viewer collection and GitHub publication
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={str(root).replace('%', '%%')}
ExecStart={unit_quote(sys.executable)} {unit_quote(root / 'viewer.py')} collect
Environment=PYTHONUNBUFFERED=1
TimeoutStartSec=45min
UMask=0077
'''
    timer = '''[Unit]
Description=Collect Twitch viewers at 19 to 23 JST

[Timer]
OnCalendar=*-*-* 19,20,21,22,23:00:00 Asia/Tokyo
AccuracySec=1s
Persistent=false
Unit=research-viewer.service

[Install]
WantedBy=timers.target
'''
    return service, timer


def install_timer(root):
    check_repository(root)
    config = root / '.local/config.json'
    if not config.is_file():
        raise ViewerError('先にsetupで認証情報を設定してください。')
    git(root, 'push', '--dry-run', 'origin', 'HEAD:refs/heads/main')
    destination = Path.home() / '.config/systemd/user'
    service, timer = timer_units(root)
    for name, content in [('research-viewer.service', service), ('research-viewer.timer', timer)]:
        atomic_write(destination / name, content)
    for args in [('daemon-reload',), ('enable', '--now', 'research-viewer.timer')]:
        result = subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=30)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or f'終了コード {result.returncode}'
            raise ViewerError(f"systemctl --user {' '.join(args)} に失敗しました。\n{detail}")
    print('毎日19・20・21・22・23時（日本時間）の自動取得を登録しました。')


def setup(root):
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        raise ViewerError('設定画面にはTkinterが必要です。Ubuntuではpython3-tkをインストールしてください。') from None
    window = tk.Tk()
    window.title('Research Viewer 初回設定')
    window.geometry('560x240')
    tk.Label(window, text='TwitchのClient IDとClient Secretを入力してください。', pady=12).pack()
    entries = []
    for label, secret in [('Client ID', False), ('Client Secret', True)]:
        frame = tk.Frame(window)
        frame.pack(fill='x', padx=20, pady=5)
        tk.Label(frame, text=label, width=14, anchor='w').pack(side='left')
        entry = tk.Entry(frame, show='*' if secret else '', width=45)
        entry.pack(side='left', fill='x', expand=True)
        entries.append(entry)
    def save():
        values = [entry.get().strip() for entry in entries]
        if not all(values):
            messagebox.showerror('未入力', '両方を入力してください。')
            return
        try:
            local = root / '.local'
            local.mkdir(exist_ok=True, mode=0o700)
            os.chmod(local, 0o700)
            atomic_write(local / 'config.json', json.dumps(dict(zip(('client_id', 'client_secret'), values))), 0o600)
        except OSError:
            messagebox.showerror('保存失敗', '設定ファイルを書き込めませんでした。')
            return
        messagebox.showinfo('保存完了', '認証情報をこのPCだけに保存しました。次にverifyで接続を確認してください。')
        window.destroy()
    tk.Button(window, text='保存', command=save, width=20).pack(pady=12)
    tk.Label(window, text='SecretはGitHubやチャットへ貼り付けないでください。').pack()
    window.mainloop()


def setup_mail(root):
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        raise ViewerError('メール設定画面にはTkinterが必要です。') from None
    try:
        existing = mailer.load_settings(root) or {}
    except mailer.MailError:
        existing = {}
    window = tk.Tk()
    window.title('Research Viewer Gmail通知設定')
    window.geometry('640x430')
    enabled = tk.BooleanVar(value=existing.get('enabled', False))
    tk.Checkbutton(window, text='23時のメール通知を有効にする', variable=enabled).pack(pady=10)
    entries = []
    for label, key, secret in [('送信用Gmail', 'username', False), ('アプリパスワード', 'app_password', True)]:
        frame = tk.Frame(window)
        frame.pack(fill='x', padx=20, pady=8)
        tk.Label(frame, text=label, width=18, anchor='w').pack(side='left')
        entry = tk.Entry(frame, show='*' if secret else '', width=45)
        entry.insert(0, existing.get(key, ''))
        entry.pack(side='left', fill='x', expand=True)
        entries.append(entry)
    tk.Label(window, text='宛先（複数可・1行1件またはカンマ区切り）').pack()
    recipients = tk.Text(window, height=6, width=65)
    recipients.insert('1.0', '\n'.join(existing.get('recipients', [])))
    recipients.pack(padx=20, pady=8)
    def save():
        try:
            config = mailer.validate_settings(dict(enabled=enabled.get(), username=entries[0].get(),
                                                   app_password=entries[1].get(), recipients=recipients.get('1.0', 'end')))
            local = root / '.local'
            local.mkdir(exist_ok=True, mode=0o700)
            os.chmod(local, 0o700)
            atomic_write(local / 'mail.json', json.dumps(config, ensure_ascii=False, indent=2), 0o600)
        except (mailer.MailError, OSError) as exc:
            messagebox.showerror('保存失敗', str(exc) if isinstance(exc, mailer.MailError) else '設定を書き込めません。')
            return
        messagebox.showinfo('保存完了', 'メール設定をこのPCだけに保存しました。設定保存ではメールを送信しません。')
        window.destroy()
    tk.Button(window, text='設定を保存', command=save, width=20).pack(pady=10)
    tk.Label(window, text='通常のGoogleログイン用パスワードは入力しないでください。').pack()
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description='Twitch視聴者数の記録・HTML出力')
    parser.add_argument('command', choices=['setup', 'verify', 'collect', 'restore', 'install-timer', 'open', 'setup-mail', 'mail-status'])
    args = parser.parse_args()
    try:
        if args.command == 'setup':
            setup(ROOT)
        elif args.command == 'setup-mail':
            setup_mail(ROOT)
        elif args.command == 'mail-status':
            with Store(ROOT) as store:
                statuses = store.mail_status()
                for row in statuses:
                    print(row['scheduled_at'], row['recipient'], row['status'])
                if not statuses:
                    print('メール送信履歴はありません。')
        elif args.command == 'verify':
            client = client_from_config(ROOT)
            result = client.collect(1)
            print('認証と配信情報の取得に成功しました。取得配信数:', result['stream_count'])
            if result['games']:
                game = result['games'][0]
                print(f"{game['name']}: {game['viewers']:,}人（配信1件だけの確認）")
            else:
                print('取得範囲にゲーム配信はありませんでした。')
        elif args.command == 'collect':
            run(ROOT)
        elif args.command == 'restore':
            restore(ROOT)
        elif args.command == 'install-timer':
            install_timer(ROOT)
        elif args.command == 'open':
            path = ROOT / 'output/index.html'
            if not path.is_file():
                raise ViewerError('まだHTMLがありません。最初の取得完了後に開いてください。')
            webbrowser.open(path.as_uri())
    except (ViewerError, mailer.MailError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        print(str(exc) if isinstance(exc, (ViewerError, mailer.MailError)) else 'ファイル・コマンド操作に失敗しました。権限や実行環境を確認してください。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
