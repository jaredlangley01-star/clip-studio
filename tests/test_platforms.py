import os
import json
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet

TEMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TEMP.name
os.environ['APP_PASSWORD']='test-only'
os.environ['PUBLIC_BASE_URL']='https://clip-studio-t7r6.onrender.com'
os.environ['YOUTUBE_CLIENT_ID']='youtube-test-client'
os.environ['YOUTUBE_CLIENT_SECRET']='youtube-test-secret'
os.environ['TWITCH_CLIENT_ID']='twitch-test-client'
os.environ['TWITCH_CLIENT_SECRET']='twitch-test-secret'
os.environ['OAUTH_ENCRYPTION_KEY']=Fernet.generate_key().decode()
from app import main

class PlatformTests(unittest.TestCase):
    def tearDown(self):
        main.execute('DELETE FROM platform_accounts')
        main.execute('DELETE FROM oauth_pending')

    def test_status_does_not_expose_tokens(self):
        status=main.platform_status(None)
        self.assertEqual({x['platform'] for x in status},{'youtube','twitch'})
        self.assertTrue(all(x['configured'] for x in status))
        self.assertFalse(any('token' in x for x in status))

    def test_oauth_connect_uses_valid_state_and_redirect(self):
        from urllib.parse import urlparse,parse_qs
        for platform in ('youtube','twitch'):
            data=main.platform_connect(platform,None)
            qs=parse_qs(urlparse(data['authorization_url']).query,keep_blank_values=True)
            self.assertEqual(qs['redirect_uri'][0],
                'https://clip-studio-t7r6.onrender.com/api/platforms/'+platform+'/callback')
            state=qs['state'][0]
            self.assertGreaterEqual(len(state),32)
            row=main.execute('SELECT * FROM oauth_pending WHERE state=?',(state,)).fetchone()
            self.assertEqual(row['platform'],platform)

    def test_oauth_callback_consumes_state_and_encrypts_tokens(self):
        state=main.platform_connect('twitch',None)['authorization_url'].split('state=')[-1]
        def exchange(url,form=None,headers=None):
            if url.endswith('/token'):return {'access_token':'my-secret-token','refresh_token':'refresh-secret','expires_in':3000}
            if url.endswith('/users'):return {'data':[{'id':'99','display_name':'Streamer'}]}
            raise AssertionError(url)
        with patch.object(main,'exchange_json',side_effect=exchange):
            result=main.platform_callback('twitch',state=state,code='authorization-code')
        self.assertEqual(result.status_code,200)
        row=main.execute('SELECT * FROM platform_accounts WHERE platform=?',('twitch',)).fetchone()
        self.assertNotIn('my-secret-token',row['token_cipher'])
        self.assertNotIn('refresh-secret',row['refresh_cipher'])
        token,account=main.platform_access('twitch')
        self.assertEqual(token,'my-secret-token')
        self.assertEqual(account['account_name'],'Streamer')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            main.platform_callback('twitch',state=state,code='replay')

    def test_twitch_video_listing_uses_official_api(self):
        main.save_platform_account('twitch',{'access_token':'access','refresh_token':'refresh','expires_in':3600},
            {'id':'44','name':'Streamer'})
        def response(url,form=None,headers=None):
            self.assertIn('Authorization',headers)
            if 'helix/videos' in url:
                return {'data':[{'id':'1','title':'Past stream','created_at':'2026-11-01T08:00:00Z',
                    'thumbnail_url':'https://example.com/%{width}x%{height}.jpg',
                    'url':'https://www.twitch.tv/videos/1'}]}
            if 'helix/streams' in url:return {'data':[]}
            raise AssertionError(url)
        with patch.object(main,'exchange_json',side_effect=response):
            result=main.platform_videos('twitch',None)
        self.assertEqual(result['videos'][0]['type'],'recording')
        self.assertIn('320x180',result['videos'][0]['thumbnail'])

    def test_youtube_video_metadata_not_media_download(self):
        main.save_platform_account('youtube',{'access_token':'access','refresh_token':'refresh','expires_in':3600},
            {'id':'abc','name':'YT'})
        def response(url,form=None,headers=None):
            if '/channels?' in url:return {'items':[{'contentDetails':{'relatedPlaylists':{'uploads':'uploads-id'}}}]}
            if '/playlistItems?' in url:return {'items':[{'contentDetails':{'videoId':'123'},
                'snippet':{'title':'Uploaded Video','publishedAt':'2026-11-10','thumbnails':{}}}]}
            if '/liveBroadcasts?' in url:return {'items':[]}
            raise AssertionError(url)
        with patch.object(main,'exchange_json',side_effect=response):
            result=main.platform_videos('youtube',None)
        self.assertEqual(result['videos'][0]['url'],'https://www.youtube.com/watch?v=123')
        self.assertNotIn('file_url',result['videos'][0])

if __name__=='__main__':
    unittest.main()
