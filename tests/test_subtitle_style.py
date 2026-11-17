import json
import os
import tempfile
import unittest
from pathlib import Path

TMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TMP.name
os.environ['APP_PASSWORD']='testing-only'
from app import main

class SubtitleStyleTests(unittest.TestCase):
    def setUp(self):
        self.video=main.uid();self.cid=main.uid()
        main.execute('INSERT INTO videos(id,name,source,status,path,duration,error,created) VALUES(?,?,?,?,?,?,?,?)',
            (self.video,'video.mp4','upload','ready','/tmp/nonexistent.mp4',60,None,main.utc()))
        main.execute('INSERT INTO clips(id,video_id,title,start,end,score,status,path,created,caption_style,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (self.cid,self.video,'Clip',0,10,0.5,'review',None,main.utc(),'bold','sample'))
        main.execute('INSERT INTO transcripts(video_id,data,provider,error) VALUES(?,?,?,?)',
            (self.video,json.dumps({'words':[{'start':1,'end':2,'word':'Hello'}]}),'test',None))
    def tearDown(self):
        main.execute('DELETE FROM clips WHERE id=?',(self.cid,))
        main.execute('DELETE FROM transcripts WHERE video_id=?',(self.video,))
        main.execute('DELETE FROM videos WHERE id=?',(self.video,))
    def test_theme_persists_per_clip_and_presets(self):
        theme={'font_family':'DejaVu Serif','font_size':57,'stroke_width':4,
            'stroke_color':'#551122','color':'#FFAA00','opacity':75,'position':20}
        saved=main.validate_caption_theme(theme)
        self.assertEqual(saved['opacity'],75)
        self.assertEqual(saved['font_id'],None)
        p=main.ClipEdit(title='Clip',start=0,end=10,caption_style='custom',caption_theme=theme)
        main.clip_edit(self.cid,p,None)
        self.assertEqual(json.loads(main.get_clip(self.cid)['caption_theme'])['font_size'],57)
        preset=main.create_caption_preset(main.CaptionPresetCreate(name='Custom Gold',theme=theme),None)
        self.assertEqual(preset['theme']['stroke_color'],'#551122')
        self.assertIn(preset['id'],[r['id'] for r in main.list_caption_presets(None)])
        main.delete_caption_preset(preset['id'],None)
    def test_invalid_styles_blocked(self):
        from fastapi import HTTPException
        for change in [{'font_family':'danger;command'}, {'font_size':1000},
                       {'color':'not-a-colour'}, {'stroke_width':-2},
                       {'opacity':-1}, {'font_id':'missing'}]:
            with self.subTest(change=change):
                with self.assertRaises(HTTPException):
                    main.validate_caption_theme(change)
    def test_libass_hex_colour_opacity(self):
        self.assertEqual(main.ass_color('#FF0000',100),'&H000000FF')
        self.assertEqual(main.ass_color('#102030',50),'&H80203010')
    def test_fontlist_has_builtin_families(self):
        library=main.list_fonts(None)
        self.assertIn('DejaVu Sans',library['builtin'])

if __name__=='__main__':
    unittest.main()
