import unittest
import viewer


class Source(viewer.Client):
    def __init__(self, rows):self.rows=rows
    def pages(self,path,**params):yield from self.rows
    def get(self,path,**params):return dict(data=[dict(id='g',name='Game',igdb_id='1')])


def stream(sid,uid,count,gid='g'):
    return dict(id=sid,user_id=uid,game_id=gid,viewer_count=count)


class AudienceCollectionTests(unittest.TestCase):
    def test_user_id_counts_duplicate_streams_and_excluded_games(self):
        result=Source([stream('1','a',10),stream('1','a',999),stream('2','a',20),
                       stream('3','b',40),stream('4','c',99,'chat')]).collect()
        self.assertEqual(result['stream_count'],4)
        self.assertEqual(result['included_stream_count'],3)
        self.assertEqual(result['games'],[dict(id='g',name='Game',viewers=70,broadcasters={'a':30,'b':40})])

    def test_missing_or_invalid_user_id_rejects_collection(self):
        for uid in (None,'',123):
            row=stream('1',uid,100)
            with self.subTest(uid=uid),self.assertRaises(viewer.ViewerError):
                Source([row]).collect()
        row=stream('1','a',100);del row['user_id']
        with self.assertRaises(viewer.ViewerError):Source([row]).collect()
