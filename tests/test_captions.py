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


if __name__ == '__main__':
    unittest.main()
