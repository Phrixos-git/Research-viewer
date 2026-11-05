import unittest
from html.parser import HTMLParser
import viewer


class Layout(HTMLParser):
    def __init__(self):
        super().__init__()
        self.styles = {'viewers': [], 'bar-track': [], 'viewer-number': []}
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for cls in attrs.get('class', '').split():
            if cls in self.styles:
                self.styles[cls].append(dict(
                    part.split(':', 1) for part in attrs.get('style', '').split(';') if ':' in part))


class BarAlignmentTests(unittest.TestCase):
    def test_inline_bar_tracks_share_left_origin_despite_digit_counts(self):
        page = viewer.render_ranking([
            dict(id=str(i), name=f'Game {i}', viewers=v)
            for i,v in enumerate((1000000,999,1))])
        parser = Layout()
        parser.feed(page)
        self.assertEqual(len(parser.styles['viewers']), 3)
        for style in parser.styles['viewers']:
            self.assertEqual(style['justify-content'], 'flex-start')
        self.assertEqual(len(parser.styles['bar-track']), 3)
        for style in parser.styles['bar-track']:
            self.assertEqual(style['flex'], '0 0 140px')
            self.assertEqual(style['width'], '140px')
            self.assertEqual(style['max-width'], '45%')
        self.assertEqual(len(parser.styles['viewer-number']), 3)
        for style in parser.styles['viewer-number']:
            self.assertEqual(style['flex'], '1')
            self.assertEqual(style['min-width'], '0')
            self.assertEqual(style['text-align'], 'right')
