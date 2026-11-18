import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from starlette.datastructures import UploadFile

TMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TMP.name
os.environ['APP_PASSWORD']='test-only'
from app import main

class SermonTests(unittest.TestCase):
    def setUp(self):
        main.execute('DELETE FROM sermon_clips')
        main.execute('DELETE FROM sermon_videos')
        main.execute('DELETE FROM sermon_branding')
    def tearDown(self):
        main.execute('DELETE FROM sermon_clips')
        main.execute('DELETE FROM sermon_videos')
        main.execute('DELETE FROM sermon_branding')

    def test_three_nonoverlapping_sermon_picks(self):
        with patch.object(main,'build_ranked_picks',return_value=[]):
            picks=main.sermon_picks(Path('test.mp4'),360,None)
        self.assertEqual(len(picks),3)
        self.assertTrue(all(a['end']<=b['start'] for a,b in zip(picks,picks[1:])))
        with patch.object(main,'build_ranked_picks',return_value=[]):
            short=main.sermon_picks(Path('short.mp4'),12,None)
        self.assertEqual(len(short),3)

    def test_connected_youtube_validation_and_exact_three_private_clips(self):
        metadata=[{'id':'abcdefghijk','title':'Sunday sermon','type':'recording'}]
        with patch.object(main,'sermon_youtube_catalog',return_value={'videos':metadata}),\
             patch.object(main,'process_sermon',return_value=None) as worker:
            file=UploadFile(file=io.BytesIO(b'pretend source'),filename='sermon.mp4')
            result=asyncio.run(main.sermon_upload(file=file,youtube_id='abcdefghijk',_=None))
        self.assertEqual(result['target_clips'],3)
        vid=result['id']
        with patch.object(main,'duration',return_value=360),\
             patch.object(main,'transcribe',return_value=(None,'not configured')),\
             patch.object(main,'build_ranked_picks',return_value=[]):
            main.process_sermon(vid)
        rows=main.execute('SELECT c.id,c.status,c.aspect_ratio FROM clips c WHERE c.video_id=?',(vid,)).fetchall()
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r['status']=='review' and r['aspect_ratio']=='9:16' for r in rows))
        self.assertEqual(len(main.sermon_listing(None)['clips']),3)
        with patch.object(main,'sermon_youtube_catalog',return_value={'videos':[]}):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(main.sermon_upload(file=UploadFile(file=io.BytesIO(b'x'),filename='s.mp4'),
                    youtube_id='abcdefghijk',_=None))
        self.assertEqual(cm.exception.status_code,403)
        path=Path(main.execute('SELECT path FROM videos WHERE id=?',(vid,)).fetchone()['path'])
        path.unlink(missing_ok=True)
        main.execute('DELETE FROM clips WHERE video_id=?',(vid,))
        main.execute('DELETE FROM videos WHERE id=?',(vid,))

    def test_full_bleed_center_crop_and_branded_fades(self):
        vid=main.uid();cid=main.uid()
        src=main.ROOT/'uploads'/(vid+'.mp4');src.write_bytes(b'fake')
        main.execute('INSERT INTO videos(id,name,source,status,path,duration,created) VALUES(?,?,?,?,?,?,?)',
                     (vid,'test.mp4','sermon','ready',str(src),180,main.utc()))
        main.execute("""INSERT INTO clips(id,video_id,title,start,end,status,created,caption_style,aspect_ratio,facecam_mode)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (cid,vid,'Test',10,70,'review',main.utc(),'none','9:16','off'))
        main.execute('INSERT INTO sermon_clips(clip_id,crop_x) VALUES(?,?)',(cid,0.75))
        fake_logo=main.ROOT/'sermons'/'church-logo.png';fake_logo.write_bytes(b'fake')
        main.execute('INSERT INTO sermon_branding(id,logo_path,fade_in,fade_out,outro_seconds) VALUES(1,?,?,?,?)',
                     (str(fake_logo),0.5,0.7,2.0))
        captured=[]
        def mock_ffmpeg(args,timeout=300):
            captured.append(args)
            Path(args[-1]).write_bytes(b'rendered')
        with patch.object(main,'cmd',side_effect=mock_ffmpeg),patch.object(main,'subtitle_file',return_value=None):
            main.render_job(cid)
        self.assertEqual(len(captured),1)
        args=captured[0]
        graph=args[args.index('-filter_complex')+1]
        self.assertIn('force_original_aspect_ratio=increase',graph)
        self.assertIn('crop=540:960:(in_w-out_w)*0.75',graph)
        self.assertNotIn('boxblur',graph)
        self.assertIn('fade=t=in:st=0:d=0.5',graph)
        self.assertIn('fade=t=out:st=59.3:d=0.7',graph)
        self.assertIn('overlay=(W-w)/2:(H-h)/2',graph)
        self.assertEqual(args[args.index('-t')+1],'62.0')
        self.assertEqual(main.get_clip(cid)['status'],'exported')
        main.execute('DELETE FROM clips WHERE id=?',(cid,))
        main.execute('DELETE FROM videos WHERE id=?',(vid,))
        src.unlink(missing_ok=True)
        fake_logo.unlink(missing_ok=True)
        (main.ROOT/'clips'/(cid+'.mp4')).unlink(missing_ok=True)

    def test_crop_change_only_before_approval(self):
        vid=main.uid();cid=main.uid()
        main.execute('INSERT INTO videos(id,name,source,status,path,created) VALUES(?,?,?,?,?,?)',(vid,'s.mp4','sermon','ready','/tmp/s.mp4',main.utc()))
        main.execute('INSERT INTO clips(id,video_id,title,start,end,status,created) VALUES(?,?,?,?,?,?,?)',(cid,vid,'Test',0,35,'review',main.utc()))
        main.execute('INSERT INTO sermon_clips(clip_id,crop_x) VALUES(?,?)',(cid,0.5))
        res=main.sermon_crop_change(cid,main.SermonCropChange(crop_x=0.62),None)
        self.assertEqual(res['crop_x'],0.62)
        main.execute("UPDATE clips SET status='exported' WHERE id=?",(cid,))
        with self.assertRaises(HTTPException):
            main.sermon_crop_change(cid,main.SermonCropChange(crop_x=0.7),None)
        main.execute('DELETE FROM clips WHERE id=?',(cid,))
        main.execute('DELETE FROM videos WHERE id=?',(vid,))

if __name__=='__main__':
    unittest.main()
