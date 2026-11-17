import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = TEMP.name
os.environ['APP_PASSWORD'] = 'test-only'
from app import main


class MilestoneFourTests(unittest.TestCase):
    def setUp(self):
        self.video=main.uid();self.ids=[main.uid() for _ in range(3)]
        main.execute('INSERT INTO videos(id,name,source,status,path,duration,error,created) VALUES(?,?,?,?,?,?,?,?)',
                     (self.video,'sample.mp4','upload','ready','/tmp/does-not-exist.mp4',60,None,main.utc()))
        main.execute('INSERT INTO transcripts(video_id,data,provider,error) VALUES(?,?,?,?)',
                     (self.video,json.dumps({'words':[{'start':1,'end':1.3,'word':'Wait'},
                        {'start':2,'end':2.3,'word':'what'},
                        {'start':3,'end':3.5,'word':'happened?'}]}),'test',None))
        for cid in self.ids:
            main.execute('INSERT INTO clips(id,video_id,title,start,end,score,status,path,created,caption_style,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (cid,self.video,'Test',0,10,0.4,'review',None,main.utc(),'none','test'))
    def tearDown(self):
        main.execute('DELETE FROM clips WHERE video_id=?',(self.video,))
        main.execute('DELETE FROM transcripts WHERE video_id=?',(self.video,))
        main.execute('DELETE FROM videos WHERE id=?',(self.video,))

    def test_ranked_transcript_is_explainable(self):
        transcript={'words':[{'start':0.5,'end':1,'word':'Wait'},
                             {'start':1.5,'end':2,'word':'no way'},
                             {'start':2.5,'end':3,'word':'what?'}]}
        with patch.dict(os.environ,{'HIGHLIGHT_LLM_MODEL':''}):
            score,reason,title=main.score_highlight(0,8,0.3,transcript)
            quiet,quiet_reason,_=main.score_highlight(0,8,0.0,None)
            self.assertGreater(score,quiet)
            self.assertIn('reaction',reason)
            self.assertIn('Wait',title)
            self.assertIn('transcript unavailable',quiet_reason)

    def test_batch_queue_is_atomic_and_deduplicated(self):
        with patch.object(main,'submit_render') as submit:
            r=main.batch_approve(main.BatchSelection(ids=[self.ids[0],self.ids[0],self.ids[1]]),None)
            self.assertEqual(r['queued'],2)
            self.assertEqual(submit.call_count,2)
            self.assertEqual(main.get_clip(self.ids[0])['status'],'rendering')
            with self.assertRaises(Exception):
                main.batch_approve(main.BatchSelection(ids=[self.ids[0],self.ids[2]]),None)
            self.assertEqual(main.get_clip(self.ids[2])['status'],'review')

    def test_batch_preset_updates_review_only(self):
        r=main.batch_preset(main.BatchPreset(ids=self.ids[:2],caption_style='minimal'),None)
        self.assertEqual(r['updated'],2)
        self.assertEqual(main.get_clip(self.ids[0])['caption_style'],'minimal')
        self.assertEqual(main.get_clip(self.ids[2])['caption_style'],'none')
        main.execute('UPDATE clips SET status=? WHERE id=?',('exported',self.ids[2]))
        with self.assertRaises(Exception):
            main.batch_preset(main.BatchPreset(ids=[self.ids[1],self.ids[2]],caption_style='bold'),None)
        self.assertEqual(main.get_clip(self.ids[1])['caption_style'],'minimal')

    def test_thumbnail_404_when_source_missing(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as e:
            main.clip_thumbnail(self.ids[0],None)
        self.assertEqual(e.exception.status_code,404)

    def test_waveform_empty_when_video_missing(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as e:
            main.clip_waveform(self.ids[0],None)
        self.assertEqual(e.exception.status_code,404)


if __name__=='__main__':
    unittest.main()
