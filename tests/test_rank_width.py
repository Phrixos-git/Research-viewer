import unittest
from html.parser import HTMLParser
import viewer


class RankLayout(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ranks = []
        self.badges = []
        self.titles = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = dict(part.split(':', 1) for part in attrs.get('style', '').split(';') if ':' in part)
        if tag in ('td', 'th') and style.get('text-align') == 'center':
            self.ranks.append(style)
        if tag == 'th' and attrs.get('scope') == 'row':
            self.titles.append(style)
        if 'rank-badge' in attrs.get('class', '').split():
            self.badges.append(style)


class RankWidthTests(unittest.TestCase):
    def test_three_digit_ranks_do_not_wrap_and_titles_remain_flexible(self):
        games = [dict(id=str(i), name='長いゲームタイトル' * 5, viewers=123-i) for i in range(123)]
        page = viewer.render_ranking(games)
        parser = RankLayout()
        parser.feed(page)
        self.assertEqual(len(parser.ranks), 101)
        self.assertEqual(len(parser.badges), 100)
        self.assertEqual(len(parser.titles), 100)
        for style in parser.ranks:
            self.assertEqual(style['width'], '4.5em')
            self.assertEqual(style['min-width'], '4.5em')
            self.assertEqual(style['white-space'], 'nowrap')
        for style in parser.badges:
            self.assertEqual(style['white-space'], 'nowrap')
        for style in parser.titles:
            self.assertEqual(style['min-width'], '0')
            self.assertEqual(style['white-space'], 'normal')
            self.assertEqual(style['overflow-wrap'], 'anywhere')
        self.assertIn('class="rank-badge rank-100"', page)
        self.assertNotIn('class="rank-badge rank-101"', page)
        self.assertIn('max-width:100%;table-layout:auto;', page)
