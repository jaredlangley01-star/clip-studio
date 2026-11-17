import os
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from fastapi import HTTPException

TEMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TEMP.name
os.environ['APP_PASSWORD']='test-only'
from app import main

class TwitchLiveIntelligenceTests(unittest.TestCase):
    def setUp(self):
        main.execute('DELETE FROM twitch_highlight_signals')
        main.execute('DELETE FROM twitch_monitor_sessions')
        main.execute('DELETE FROM twitch_watchlist')
        main.execute('INSERT INTO twitch_watchlist(broadcaster_id,login,display_name,added_at) VALUES(?,?,?,?)',
            ('123','streamer','Test Streamer',main.utc()))
    def tearDown(self):
        main.execute('DELETE FROM twitch_highlight_signals')
        main.execute('DELETE FROM twitch_monitor_sessions')
        main.execute('DELETE FROM twitch_watchlist')

    def test_monitor_requires_watched_broadcaster(self):
        with self.assertRaises(HTTPException):
            main.twitch_monitor_set(main.TwitchMonitorChoice(broadcaster_id='234',enabled=True),None)
        enabled=main.twitch_monitor_set(main.TwitchMonitorChoice(broadcaster_id='123',enabled=True),None)
        self.assertFalse(enabled['publishes'])
        self.assertFalse(enabled['media_capture'])
        self.assertTrue(main.twitch_monitor_status(None)[0]['enabled'])
        main.twitch_monitor_set(main.TwitchMonitorChoice(broadcaster_id='123',enabled=False),None)
        self.assertFalse(main.twitch_monitor_status(None)[0]['enabled'])

    def test_live_scan_discovers_existing_clips_only_and_keeps_review_status(self):
        main.twitch_monitor_set(main.TwitchMonitorChoice(broadcaster_id='123',enabled=True),None)
        start=(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat().replace('+00:00','Z')
        cliptime=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat().replace('+00:00','Z')
        calls=[]
        def fake(path,params=None,method='GET'):
            calls.append((path,params,method))
            if path=='/streams':return {'data':[{'user_id':'123','started_at':start}]}
            if path=='/clips':return {'data':[{'id':'PublicClip1','title':'Funny moment',
                 'url':'https://clips.twitch.tv/PublicClip1','created_at':cliptime,
                 'view_count':180,'thumbnail_url':'https://example.com/thumb.jpg'}]}
            raise AssertionError(path)
        with patch.object(main,'twitch_helix',side_effect=fake):
            scan=main.scan_twitch_monitors()
        self.assertEqual(scan['new'],1)
        self.assertEqual(scan['scanned'],1)
        self.assertFalse(scan['publishes'])
        self.assertFalse(scan['captures_media'])
        self.assertTrue(all(method=='GET' for _,_,method in calls))
        queue=main.twitch_highlight_signals(None)
        self.assertEqual(len(queue),1)
        self.assertIn('Public community signal',queue[0]['reason'])
        self.assertIn('no source-video AI analysis',queue[0]['reason'])
        main.twitch_highlight_review('PublicClip1',main.TwitchSignalReview(status='saved'),None)
        with patch.object(main,'twitch_helix',side_effect=fake):
            again=main.scan_twitch_monitors()
        self.assertEqual(again['new'],0)
        self.assertEqual(main.twitch_highlight_signals(None)[0]['review_status'],'saved')

    def test_offline_scan_never_creates_clips(self):
        main.twitch_monitor_set(main.TwitchMonitorChoice(broadcaster_id='123',enabled=True),None)
        with patch.object(main,'twitch_helix',return_value={'data':[]}) as api:
            scan=main.scan_twitch_monitors()
        self.assertEqual(scan['new'],0)
        self.assertEqual(scan['scanned'],1)
        self.assertEqual(api.call_count,1)
        self.assertEqual(api.call_args.args[0],'/streams')

    def test_review_validation(self):
        with self.assertRaises(HTTPException):
            main.twitch_highlight_review('missing',main.TwitchSignalReview(status='published'),None)
        with self.assertRaises(HTTPException):
            main.twitch_highlight_review('missing',main.TwitchSignalReview(status='saved'),None)

if __name__=='__main__':unittest.main()
