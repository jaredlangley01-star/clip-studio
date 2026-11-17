import asyncio
import io
import os
import tempfile
import unittest
from unittest.mock import patch
from starlette.datastructures import UploadFile
from cryptography.fernet import Fernet

TMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TMP.name
os.environ['APP_PASSWORD']='test-only'
os.environ['OAUTH_ENCRYPTION_KEY']=Fernet.generate_key().decode()
from app import main

class IngestionTests(unittest.TestCase):
    def setUp(self):
        main.execute('DELETE FROM ingest_items')
        main.execute('DELETE FROM platform_accounts')
        main.save_platform_account('twitch',{'access_token':'test','refresh_token':'test','expires_in':3600},
            {'id':'testuser','name':'Test channel'})
    def tearDown(self):
        main.execute('DELETE FROM ingest_items')
        main.execute('DELETE FROM platform_accounts')

    def test_scan_deduplicates_and_keeps_import_link(self):
        items={'platform':'twitch','videos':[{'id':'abc','title':'My recorded stream','type':'recording',
             'url':'https://www.twitch.tv/videos/abc','published':None,'thumbnail':None}]}
        with patch.object(main,'platform_videos',return_value=items):
            first=main.scan_ingest(None)
            self.assertEqual(first['seen'],1)
            main.execute("UPDATE ingest_items SET status='imported',video_id='linked123' WHERE platform='twitch' AND external_id='abc'")
            second=main.scan_ingest(None)
        self.assertEqual(len(second['items']),1)
        self.assertEqual(second['items'][0]['video_id'],'linked123')
        self.assertEqual(second['items'][0]['status'],'imported')

    def test_attach_queued_file_to_discovered_recording(self):
        with patch.object(main,'platform_videos',return_value={'platform':'twitch','videos':[
            {'id':'clip1','title':'Stream','type':'recording','url':'https://www.twitch.tv/videos/clip1'}]}):
            main.scan_ingest(None)
        file=UploadFile(file=io.BytesIO(b'test file'),filename='owned.mp4')
        with patch.object(main,'process_video',return_value=None):
            response=asyncio.run(main.upload(file=file,highlight_count=4,clip_length=35,
                aspect_ratio='9:16',has_facecam=False,facecam_region='',
                platform_source='twitch',platform_item_id='clip1',_=None))
        item=main.execute("SELECT * FROM ingest_items WHERE platform='twitch' AND external_id='clip1'").fetchone()
        self.assertEqual(item['status'],'imported')
        self.assertEqual(item['video_id'],response['id'])
        row=main.execute('SELECT * FROM videos WHERE id=?',(response['id'],)).fetchone()
        self.assertEqual(row['source'],'twitch')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            asyncio.run(main.upload(file=UploadFile(file=io.BytesIO(b'other'),filename='other.mp4'),
                highlight_count=4,clip_length=35,aspect_ratio='9:16',
                has_facecam=False,facecam_region='',platform_source='twitch',platform_item_id='clip1',_=None))
        from pathlib import Path
        Path(row['path']).unlink(missing_ok=True)
        main.execute('DELETE FROM videos WHERE id=?',(response['id'],))

    def test_rejects_platform_forgery_and_live_recording_link(self):
        from fastapi import HTTPException
        main.execute("INSERT INTO ingest_items(platform,external_id,title,item_type,status,discovered) VALUES(?,?,?,?,?,?)",
            ('twitch','liveid','Live','live','discovered',main.utc()))
        for platform,id in [('invalid','notreal'),('twitch','liveid'),('youtube','unknown')]:
            with self.assertRaises(HTTPException):
                asyncio.run(main.upload(file=UploadFile(file=io.BytesIO(b'video'),filename='test.mp4'),
                    highlight_count=4,clip_length=35,aspect_ratio='9:16',has_facecam=False,facecam_region='',
                    platform_source=platform,platform_item_id=id,_=None))

if __name__=='__main__':
    unittest.main()
