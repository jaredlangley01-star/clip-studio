import os
import tempfile
import unittest
from fastapi import HTTPException

TEMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TEMP.name
os.environ['APP_PASSWORD']='test-only'
from app import main

class PrivateReviewTests(unittest.TestCase):
    def setUp(self):
        self.video=main.uid()
        self.clip=main.uid()
        main.execute('INSERT INTO videos(id,name,source,status,path,duration,created) VALUES(?,?,?,?,?,?,?)',
            (self.video,'authorized.mp4','upload','ready','/tmp/authorized.mp4',420,main.utc()))
        self.facecam='{"x":0.7,"y":0.1,"w":0.2,"h":0.2}'
        main.execute('INSERT INTO clips(id,video_id,title,start,end,score,status,created,caption_style,reason,aspect_ratio,facecam_mode,facecam_region,rank_score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (self.clip,self.video,'Good moment',5,155,0.9,'review',main.utc(),'none','Audio spike','9:16','manual',self.facecam,88))
    def tearDown(self):
        main.execute('DELETE FROM clips WHERE video_id=?',(self.video,))
        main.execute('DELETE FROM videos WHERE id=?',(self.video,))

    def test_creates_private_wide_and_portrait_copies(self):
        wide=main.create_clip_variant(self.clip,main.ClipVariant(aspect_ratio='16:9'),None)
        portrait=main.create_clip_variant(self.clip,main.ClipVariant(aspect_ratio='9:16',with_facecam=True),None)
        first=main.get_clip(wide['id'])
        second=main.get_clip(portrait['id'])
        self.assertEqual(first['aspect_ratio'],'16:9')
        self.assertEqual(first['facecam_mode'],'off')
        self.assertEqual(second['aspect_ratio'],'9:16')
        self.assertEqual(second['facecam_mode'],'manual')
        self.assertEqual(second['facecam_region'],self.facecam)
        self.assertEqual(second['status'],'review')
        self.assertEqual(second['end']-second['start'],150)

    def test_variant_does_not_approve_or_publish(self):
        created=main.create_clip_variant(self.clip,main.ClipVariant(aspect_ratio='16:9'),None)
        self.assertEqual(main.get_clip(created['id'])['status'],'review')
        self.assertEqual(main.get_clip(created['id'])['path'],None)

    def test_facecam_variant_requires_selected_region(self):
        main.execute('UPDATE clips SET facecam_region=NULL WHERE id=?',(self.clip,))
        with self.assertRaises(HTTPException) as cm:
            main.create_clip_variant(self.clip,main.ClipVariant(aspect_ratio='9:16',with_facecam=True),None)
        self.assertEqual(cm.exception.status_code,409)

if __name__=='__main__':unittest.main()
