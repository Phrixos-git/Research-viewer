import re
import unittest
from html.parser import HTMLParser
import viewer


def snapshot(games, hour=23):
    stamp = f'2026-11-05T{hour}:00:00+09:00'
    return dict(scheduled_at=stamp, started_at=stamp, finished_at=stamp,
                stream_count=len(games), included_stream_count=len(games), games=games)


class Bars(HTMLParser):
    def __init__(self):
        super().__init__()
        self.bars = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'viewer-bar' in attrs.get('class', '').split():
            self.bars.append(attrs)


class ReportDesignTests(unittest.TestCase):
    def test_sqrt_bars_keep_values_without_head_styles(self):
        games = [dict(id=str(i), name=f'Game <{i}>', viewers=v)
                 for i,v in enumerate((1000000,250000,1,0))]
        page = viewer.render_html(snapshot(games), [snapshot(games)])
        parser = Bars()
        parser.feed(re.sub(r'<style[^>]*>.*?</style>', '', page, flags=re.S))
        self.assertEqual(len(parser.bars), 4)
        for attrs,width in zip(parser.bars,(100,50,2,0)):
            self.assertEqual(attrs.get('aria-hidden'), 'true')
            self.assertIn(f'width:{width}%;', attrs['style'])
            self.assertIn(viewer.BAR, attrs['style'])
            self.assertIn(viewer.BAR_END, attrs['style'])
        self.assertIn('1,000,000', page)
        self.assertIn('Game &lt;0&gt;', page)

    def test_empty_and_zero_snapshots_are_safe(self):
        for games in ([],[dict(id='z',name='zero',viewers=0)]):
            page = viewer.render_html(snapshot(games), [snapshot(games)])
            self.assertIn('タイトルランキング（上位100件）', page)
            self.assertNotIn('nan', page.lower())

    def test_absent_daily_comparison_becomes_one_note(self):
        for hour in (21,23):
            s = snapshot([dict(id='a',name='A',viewers=100)],hour)
            page = viewer.render_html(s,[s])
            self.assertNotIn('<h2>急上昇タイトル3選</h2>',page)
            self.assertNotIn('<h2>前日比較',page)
            self.assertEqual(page.count('前日比較データは蓄積中です'),1)

    def test_metadata_timing_and_inline_header_styles(self):
        s = snapshot([dict(id='a',name='A',viewers=100)])
        page = viewer.render_html(s,[s])
        self.assertIn('class="meta-chip"',page)
        self.assertIn('class="rank-badge rank-1"',page)
        self.assertIn('line-height:1.4',page)
        self.assertIn('width:4.5em',page)
        self.assertIn('font-variant-numeric:tabular-nums',page)
        self.assertIn('position:sticky;top:0',page)
        self.assertIn('box-shadow:',page)
        self.assertIn('"Hiragino Sans","Noto Sans JP","Yu Gothic UI",system-ui,sans-serif',page)
        footer = page[page.index('<footer'):]
        self.assertIn('収集開始：',footer)
        self.assertIn('収集終了：',footer)
        self.assertNotIn('収集開始：',page[:page.index('<footer')])
        self.assertNotIn('overflow:auto',page)
