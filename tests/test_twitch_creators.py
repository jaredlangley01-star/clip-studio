import os
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from fastapi import HTTPException

TEMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TEMP.name
os.environ['APP_PASSWORD']='test-only'
os.environ['OAUTH_ENCRYPTION_KEY']=Fernet.generate_key().decode()
os.environ['TWITCH_CLIENT_ID']='test-client'
os.environ['TWITCH_CLIENT_SECRET']='test-secret'
from app import main

class TwitchCreatorTests(unittest.TestCase):
    def setUp(self):
        main.execute('DELETE FROM platform_accounts')
        main.execute('DELETE FROM twitch_watchlist')
        main.execute('DELETE FROM twitch_created_clips')
        main.save_platform_account('twitch',{'access_token':'access','refresh_token':'refresh','expires_in':3600,'scope':['clips:edit']},
            {'id':'999','name':'My Twitch'})
    def tearDown(self):
        main.execute('DELETE FROM platform_accounts')
        main.execute('DELETE FROM twitch_watchlist')
        main.execute('DELETE FROM twitch_created_clips')

    def test_twitch_connection_has_clips_permission(self):
        status={v['platform']:v for v in main.platform_status(None)}
        self.assertTrue(status['twitch']['can_create_twitch_clips'])
        self.assertFalse(status['youtube']['can_create_twitch_clips'])
        with patch.dict(os.environ,{'PUBLIC_BASE_URL':'https://clip-studio-t7r6.onrender.com'}):
            from urllib.parse import urlsplit,parse_qs
            url=main.platform_connect('twitch',None)['authorization_url']
            self.assertEqual(parse_qs(urlsplit(url).query)['scope'],['clips:edit'])

    def test_search_and_add_verified_broadcaster(self):
        def fake(path,params=None,method='GET'):
            if path=='/search/channels':return {'data':[{'id':'123','broadcaster_login':'streamer','display_name':'Streamer','is_live':True,'thumbnail_url':'https://example.com/a.jpg'}]}
            if path=='/users':return {'data':[{'id':'123','login':'streamer','display_name':'Streamer','profile_image_url':'https://example.com/a.jpg'}]}
            if path=='/streams':return {'data':[{'user_id':'123','title':'Live test','viewer_count':20}]}
            raise AssertionError(path)
        with patch.object(main,'twitch_helix',side_effect=fake):
            results=main.twitch_search('streamer',None)
            self.assertTrue(results[0]['live'])
            main.twitch_watch_add(main.WatchChannel(broadcaster_id='123'),None)
            watch=main.twitch_watchlist(None)
            self.assertEqual(watch[0]['login'],'streamer')
            self.assertEqual(watch[0]['viewers'],20)
            self.assertTrue(watch[0]['live'])
            main.twitch_watch_remove('123',None)
            self.assertEqual(main.twitch_watchlist(None),[])

    def test_create_native_clip_requires_live_and_permission(self):
        main.execute('INSERT INTO twitch_watchlist(broadcaster_id,login,display_name,added_at) VALUES(?,?,?,?)',
            ('123','streamer','Streamer',main.utc()))
        def fake(path,params=None,method='GET'):
            if path=='/streams':return {'data':[{'user_id':'123'}]}
            if path=='/clips' and method=='POST':
                self.assertEqual(params['broadcaster_id'],'123')
                self.assertEqual(params['duration'],30)
                return {'data':[{'id':'Clip123','edit_url':'https://www.twitch.tv/streamer/clip/Clip123'}]}
            raise AssertionError((path,method))
        with patch.object(main,'twitch_helix',side_effect=fake):
            result=main.create_twitch_clip(main.CreateTwitchClip(broadcaster_id='123'),None)
        self.assertEqual(result['id'],'Clip123')
        self.assertEqual(main.twitch_created_clips(None)[0]['id'],'Clip123')
        with patch.object(main,'twitch_helix',return_value={'data':[]}):
            with self.assertRaises(HTTPException) as cm:
                main.create_twitch_clip(main.CreateTwitchClip(broadcaster_id='123'),None)
        self.assertEqual(cm.exception.status_code,409)
        main.execute('UPDATE platform_accounts SET scopes=? WHERE platform=?',('','twitch'))
        with self.assertRaises(HTTPException):
            main.twitch_auth_headers(require_clip_scope=True)


    def test_created_clips_query_playable_metadata(self):
        main.execute('INSERT INTO twitch_created_clips(id,broadcaster_id,edit_url,created_at) VALUES(?,?,?,?)',
            ('Clip123','123','https://www.twitch.tv/edit/Clip123',main.utc()))
        with patch.object(main,'twitch_helix',return_value={'data':[{
            'id':'Clip123','url':'https://clips.twitch.tv/Clip123','title':'Funny moment',
            'thumbnail_url':'https://clips-media-assets2.twitch.tv/thumb.jpg','duration':30}]}):
            items=main.twitch_created_clips(None)
        self.assertTrue(items[0]['ready'])
        self.assertEqual(items[0]['title'],'Funny moment')
        self.assertEqual(items[0]['url'],'https://clips.twitch.tv/Clip123')
        self.assertEqual(items[0]['share_available'],'check_on_twitch')

    def test_created_clips_processing_does_not_claim_ready(self):
        main.execute('INSERT INTO twitch_created_clips(id,broadcaster_id,edit_url,created_at) VALUES(?,?,?,?)',
            ('NewClip','123','',main.utc()))
        with patch.object(main,'twitch_helix',return_value={'data':[]}):
            items=main.twitch_created_clips(None)
        self.assertFalse(items[0]['ready'])
        self.assertIsNone(items[0]['url'])
        with patch.object(main,'twitch_helix',side_effect=HTTPException(502,'Twitch unavailable')):
            items=main.twitch_created_clips(None)
        self.assertFalse(items[0]['ready'])
        self.assertIsNone(items[0]['url'])

    def test_get_broadcaster_public_clips(self):
        with patch.object(main,'twitch_helix',return_value={'data':[{'id':'clip1','title':'Moment','url':'https://clips.twitch.tv/clip1','view_count':10}]}):
            clips=main.twitch_broadcaster_clips('123',None)
        self.assertEqual(clips[0]['views'],10)
        self.assertNotIn('download',clips[0])

    def test_reject_forged_watch_ids(self):
        with self.assertRaises(HTTPException):
            main.twitch_watch_add(main.WatchChannel(broadcaster_id='not-numeric'),None)
        with self.assertRaises(HTTPException):
            main.create_twitch_clip(main.CreateTwitchClip(broadcaster_id='999'),None)

if __name__=='__main__':
    unittest.main()
