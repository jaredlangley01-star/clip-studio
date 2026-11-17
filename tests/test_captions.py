import json
import os
import tempfile
import unittest
from pathlib import Path

TEMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = TEMP.name
os.environ['APP_PASSWORD'] = 'test-only'

from app import main


class CaptionEditorTests(unittest.TestCase):
    def setUp(self):
        self.video_id = main.uid()
        self.clip_id = main.uid()
        main.execute(
            'INSERT INTO videos VALUES(?,?,?,?,?,?,?,?)',
            (self.video_id, 'test.mp4', 'upload', 'ready', '/tmp/test.mp4', 60, None, main.utc()))
        main.execute(
            'INSERT INTO clips(id,video_id,title,start,end,score,status,path,created,caption_style,reason) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (self.clip_id, self.video_id, 'Test', 10, 20, 1, 'review', None, main.utc(), 'bold', 'speech'))
        words = {'words': [
            {'start': 11, 'end': 11.5, 'word': 'Hello'},
            {'start': 12, 'end': 12.5, 'word': 'world'},
        ]}
        main.execute(
            'INSERT INTO transcripts(video_id,data,provider,error) VALUES(?,?,?,?)',
            (self.video_id, json.dumps(words), 'test', None))

    def tearDown(self):
        main.execute('DELETE FROM clips WHERE id=?', (self.clip_id,))
        main.execute('DELETE FROM transcripts WHERE video_id=?', (self.video_id,))
        main.execute('DELETE FROM videos WHERE id=?', (self.video_id,))

    def test_caption_corrections_flow_to_export(self):
        result = main.get_captions(self.clip_id, None)
        self.assertEqual(result['captions'][0]['text'], 'Hello world')
        correction = main.CaptionChanges(captions=[
            main.CaptionSegment(start=11, end=12.5, text='Hello correctly spelled')
        ])
        main.update_captions(self.clip_id, correction, None)
        row = main.get_clip(self.clip_id)
        self.assertEqual(main.captions_for_clip(row)[0]['text'], 'Hello correctly spelled')
        path = main.subtitle_file(row)
        self.assertIsNotNone(path)
        self.assertIn('HELLO CORRECTLY SPELLED', Path(path).read_text())
        self.assertIn('00:00:01,000 --> 00:00:02,500', Path(path).read_text())

    def test_invalid_timing_rejected(self):
        from fastapi import HTTPException
        correction = main.CaptionChanges(captions=[
            main.CaptionSegment(start=5, end=6, text='outside')
        ])
        with self.assertRaises(HTTPException):
            main.update_captions(self.clip_id, correction, None)


    def test_add_retime_delete_caption(self):
        added = main.CaptionChanges(captions=[
            main.CaptionSegment(start=10.1, end=11.2, text='First corrected line'),
            main.CaptionSegment(start=11.5, end=13, text='Added line'),
        ])
        main.update_captions(self.clip_id, added, None)
        row = main.get_clip(self.clip_id)
        self.assertEqual(len(main.captions_for_clip(row)), 2)
        self.assertEqual(main.captions_for_clip(row)[1]['text'], 'Added line')
        self.assertIn('00:00:01,500 --> 00:00:03,000', main.subtitle_file(row).read_text())
        main.update_captions(self.clip_id, main.CaptionChanges(captions=[]), None)
        row = main.get_clip(self.clip_id)
        self.assertEqual(main.captions_for_clip(row), [])
        self.assertIsNone(main.subtitle_file(row))

    def test_aspect_ratio_and_manual_camera(self):
        p=main.ClipEdit(title='Test',start=10,end=20,caption_style='bold',
                        aspect_ratio='9:16',facecam_mode='manual',
                        facecam_region={'x':0.7,'y':0.1,'w':0.28,'h':0.33})
        main.clip_edit(self.clip_id,p,None)
        row=main.get_clip(self.clip_id)
        self.assertEqual(row['aspect_ratio'],'9:16')
        self.assertEqual(json.loads(row['facecam_region'])['w'],0.28)
        p=main.ClipEdit(title='Test',start=10,end=20,caption_style='none',
                        aspect_ratio='1:1',facecam_mode='off')
        main.clip_edit(self.clip_id,p,None)
        self.assertEqual(main.get_clip(self.clip_id)['aspect_ratio'],'1:1')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            main.clip_edit(self.clip_id,main.ClipEdit(title='Test',start=10,end=20,
              aspect_ratio='16:9',facecam_mode='manual',
              facecam_region={'x':0.7,'y':0.1,'w':0.28,'h':0.33}),None)
        with self.assertRaises(HTTPException):
            main.validate_region({'x':0.9,'y':0,'w':0.3,'h':0.2})


    def test_portrait_facecam_export(self):
        import subprocess
        import shutil
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg not available')
        source=Path(TEMP.name)/'test-source.mp4'
        subprocess.run(['ffmpeg','-y','-nostdin','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=12',
          '-f','lavfi','-i','anullsrc=r=44100:cl=mono','-t','1.3','-shortest','-c:v','libx264','-pix_fmt','yuv420p',
          '-c:a','aac',str(source)],check=True,timeout=40)
        main.execute('UPDATE videos SET path=? WHERE id=?',(str(source),self.video_id))
        main.execute('UPDATE clips SET start=?,end=?,caption_style=?,facecam_mode=?,aspect_ratio=?,facecam_region=? WHERE id=?',
          (0,1,'none','manual','9:16',json.dumps({'x':0.65,'y':0.05,'w':0.3,'h':0.42}),self.clip_id))
        main.render_job(self.clip_id)
        row=main.get_clip(self.clip_id)
        self.assertEqual(row['status'],'exported','Check FFmpeg filtergraph compatibility')
        result=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
            '-show_entries','stream=width,height','-of','json',row['path']]))
        self.assertEqual((result['streams'][0]['width'],result['streams'][0]['height']),(540,960))


if __name__ == '__main__':
    unittest.main()
