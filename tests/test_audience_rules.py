import unittest
import audience


def game(counts):
    return dict(id='g', name='Game', viewers=sum(counts.values()), broadcasters=counts)


class AudienceRulesTests(unittest.TestCase):
    def test_exact_25_both_and_below_remaining(self):
        before = game({'a':100,'b':100,'c':100})
        normal = audience.classify_game(before,game({'a':150,'b':125,'c':125}))
        self.assertEqual(normal['category'],'normal')
        self.assertEqual(normal['excluded_broadcaster'],'a')
        self.assertEqual(normal['remaining_percent'],25)
        self.assertEqual(audience.classify_game(before,game({'a':150,'b':124,'c':125}))['category'],'single')

    def test_full_threshold_zero_and_missing_details(self):
        self.assertEqual(audience.classify_game(game({'a':200,'b':200}),game({'a':250,'b':249}))['category'],'none')
        self.assertEqual(audience.classify_game(game({'a':200,'b':200}),game({'a':250,'b':250}))['category'],'normal')
        self.assertEqual(audience.classify_game(game({'a':0}),game({'a':100}))['category'],'none')
        self.assertEqual(audience.classify_game(dict(viewers=100),dict(viewers=200))['category'],'unavailable')

    def test_delta_not_current_size_same_broadcaster_both_times(self):
        before = game({'large':1000,'new':0})
        result = audience.classify_game(before,game({'large':1000,'new':300}))
        self.assertEqual(result['excluded_broadcaster'],'new')
        self.assertEqual(result['category'],'single')
        self.assertEqual(result['remaining_before'],1000)
        self.assertEqual(result['remaining_after'],1000)
        self.assertEqual(result['contribution'],300)

    def test_tie_and_contribution_can_exceed_net_growth(self):
        result = audience.classify_game(game({'b':100,'a':100}),game({'b':150,'a':150}))
        self.assertEqual(result['excluded_broadcaster'],'a')
        result = audience.classify_game(game({'a':100,'b':100}),game({'a':300,'b':50}))
        self.assertGreater(result['contribution_percent'],100)
        self.assertEqual(result['category'],'single')

    def test_new_missing_channel_and_zero_remaining(self):
        self.assertEqual(audience.classify_game(game({'a':100}),game({'a':100,'b':100}))['category'],'single')
        self.assertEqual(audience.classify_game(game({'a':100}),game({'a':200}))['category'],'single')
        self.assertEqual(audience.classify_game(game({'a':100}),game({'a':200,'b':25}))['category'],'unavailable')

    def test_count_validation(self):
        audience.validate_counts({'a':50,'b':50},100)
        for counts in (None,[],{'':100},{'a':True},{'a':-1},{'a':99}):
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                audience.validate_counts(counts,100)
