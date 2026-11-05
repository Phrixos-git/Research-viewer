"""Classify observed game growth independently of collection and rendering."""


def validate_counts(counts, total):
    if not isinstance(counts, dict):
        raise ValueError('配信者別内訳は辞書で指定してください。')
    for bid, count in counts.items():
        if not isinstance(bid, str) or not bid or type(count) is not int or count < 0:
            raise ValueError('配信者IDまたは人数が不正です。')
    if sum(counts.values()) != total:
        raise ValueError('配信者別人数の合計がゲーム別合計と一致しません。')


def classify_game(before, after):
    if before['viewers'] <= 0 or after['viewers'] * 4 < before['viewers'] * 5:
        return dict(category='none')
    b, a = before.get('broadcasters'), after.get('broadcasters')
    if b is None or a is None:
        return dict(category='unavailable', reason='配信者別内訳が未取得')
    validate_counts(b, before['viewers'])
    validate_counts(a, after['viewers'])
    bid = min(b.keys() | a.keys(), key=lambda uid: (-(a.get(uid, 0) - b.get(uid, 0)), uid))
    contribution = a.get(bid, 0) - b.get(bid, 0)
    remaining_before = before['viewers'] - b.get(bid, 0)
    remaining_after = after['viewers'] - a.get(bid, 0)
    result = dict(category='single', excluded_broadcaster=bid, contribution=contribution,
                  contribution_percent=contribution / (after['viewers'] - before['viewers']) * 100,
                  remaining_before=remaining_before, remaining_after=remaining_after,
                  remaining_percent=(remaining_after / remaining_before - 1) * 100 if remaining_before else None)
    if remaining_before > 0 and remaining_after * 4 >= remaining_before * 5:
        result['category'] = 'normal'
    elif remaining_before == 0 and remaining_after > 0:
        result.update(category='unavailable', reason='最多寄与配信者の除外後の比較元が0人')
    return result
