import os, re, json, time, uuid, sqlite3, secrets, subprocess, threading, math, shutil, urllib.request
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT=Path(os.environ.get('DATA_DIR','/tmp/clip-studio')).resolve()
ROOT.mkdir(parents=True,exist_ok=True)
for name in ('uploads','clips','live'):(ROOT/name).mkdir(exist_ok=True)
DB=ROOT/'database.sqlite3'
app=FastAPI(title='Clip Studio',docs_url=None,redoc_url=None)
security=HTTPBasic(auto_error=False)
lock=threading.RLock()
LIVE={}

def auth(c:HTTPBasicCredentials=Depends(security)):
    user=os.getenv('APP_USERNAME','admin'); pwd=os.getenv('APP_PASSWORD','')
    if not pwd: raise HTTPException(503,'Set APP_PASSWORD before using the application')
    if not c or not (secrets.compare_digest(c.username,user) and secrets.compare_digest(c.password,pwd)):
        raise HTTPException(401,'Authentication required',headers={'WWW-Authenticate':'Basic realm="Clip Studio"'})

def db():
    x=sqlite3.connect(DB,timeout=30)
    x.row_factory=sqlite3.Row
    return x

def execute(sql,args=()):
    with lock:
        with db() as c:
            return c.execute(sql,args)

def init():
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,name TEXT,source TEXT,status TEXT,path TEXT,duration REAL,error TEXT,created TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS clips(id TEXT PRIMARY KEY,video_id TEXT,title TEXT,start REAL,end REAL,score REAL,status TEXT,path TEXT,created TEXT, FOREIGN KEY(video_id) REFERENCES videos(id))')
        c.execute('CREATE TABLE IF NOT EXISTS monitors(id TEXT PRIMARY KEY,label TEXT,platform TEXT,source_url TEXT,status TEXT,error TEXT,created TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS transcripts(video_id TEXT PRIMARY KEY,data TEXT,provider TEXT,error TEXT)')
        video_cols=[r[1] for r in c.execute('PRAGMA table_info(videos)')]
        if 'aspect_ratio' not in video_cols:c.execute("ALTER TABLE videos ADD COLUMN aspect_ratio TEXT DEFAULT '9:16'")
        if 'facecam_mode' not in video_cols:c.execute("ALTER TABLE videos ADD COLUMN facecam_mode TEXT DEFAULT 'off'")
        if 'facecam_region' not in video_cols:c.execute("ALTER TABLE videos ADD COLUMN facecam_region TEXT")
        cols=[r[1] for r in c.execute('PRAGMA table_info(clips)')]
        if 'caption_style' not in cols:c.execute("ALTER TABLE clips ADD COLUMN caption_style TEXT DEFAULT 'none'")
        if 'reason' not in cols:c.execute("ALTER TABLE clips ADD COLUMN reason TEXT DEFAULT 'Audio activity'")
        if 'caption_edits' not in cols:c.execute("ALTER TABLE clips ADD COLUMN caption_edits TEXT")
        if 'aspect_ratio' not in cols:c.execute("ALTER TABLE clips ADD COLUMN aspect_ratio TEXT DEFAULT '9:16'")
        if 'facecam_mode' not in cols:c.execute("ALTER TABLE clips ADD COLUMN facecam_mode TEXT DEFAULT 'off'")
        if 'facecam_region' not in cols:c.execute("ALTER TABLE clips ADD COLUMN facecam_region TEXT")
        if 'layout' not in cols:c.execute("ALTER TABLE clips ADD COLUMN layout TEXT")
init()

def utc():return datetime.now(timezone.utc).isoformat()
def uid():return uuid.uuid4().hex

def cmd(args,timeout=300):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=True)

def duration(path):
    data=json.loads(cmd(['ffprobe','-v','quiet','-show_entries','format=duration','-of','json',str(path)],30).stdout)
    return float(data['format']['duration'])

def analyze(path, dur, count=8, clip_length=35):
    # Transcript-free audio activity is an honest initial heuristic, not semantic AI.
    # Extract low-sample-rate mono audio into per-window RMS readings.
    if dur<3:return [(0,max(0.5,dur),0.5)]
    raw=subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(path),'-vn','-ac','1','-ar','4000','-f','s16le','pipe:1'],capture_output=True,timeout=max(120,int(dur*2)))
    samples=memoryview(raw.stdout).cast('B')
    window=4; bytes_per_window=4000*2*window
    energies=[]
    for i in range(0,len(samples),bytes_per_window):
        block=samples[i:i+bytes_per_window]
        if len(block)<4000:continue
        # Sample at 100 Hz for efficient large recordings.
        vals=[int.from_bytes(block[k:k+2],'little',signed=True) for k in range(0,len(block)-1,80)]
        rms=math.sqrt(sum(v*v for v in vals)/max(1,len(vals)))/32768
        energies.append(rms)
    if not energies:
        n=min(count,max(1,int(dur//30)))
        return [(min(i*dur/n,max(0,dur-30)), min(dur,min(i*dur/n,max(0,dur-30))+30),0.1) for i in range(n)]
    ranked=sorted(range(len(energies)), key=lambda i:energies[i],reverse=True)
    chosen=[]
    span=min(clip_length,max(1,dur))
    for idx in ranked:
        center=idx*window+window/2
        start=max(0,min(center-span/2,dur-span)); end=min(dur,start+span)
        if any(max(0,min(end,b)-max(start,a))>span*0.30 for a,b,_ in chosen):continue
        chosen.append((round(start,2),round(end,2),round(energies[idx],5)))
        if len(chosen)>=count:break
    if not chosen:chosen=[(0,min(30,dur),0.1)]
    return sorted(chosen,key=lambda x:x[0])

def transcribe(path):
    key=os.getenv('TRANSCRIPTION_API_KEY','').strip()
    if not key:return None,'Speech analysis is not configured'
    url=os.getenv('TRANSCRIPTION_API_URL','https://api.groq.com/openai/v1/audio/transcriptions')
    if not url.startswith('https://'):return None,'HTTPS transcription endpoint required'
    wave=ROOT/'uploads'/(uid()+'.mp3')
    try:
        cmd(['ffmpeg','-y','-nostdin','-v','error','-i',str(path),'-vn','-ac','1','-ar','16000','-c:a','libmp3lame','-b:a','48k',str(wave)],timeout=240)
        if wave.stat().st_size>20*1024*1024:return None,'Compressed audio exceeds 20 MB. Try a shorter recording.'
        boundary='----ClipStudio'+uid()
        def part(name,value,filename=None,content_type='text/plain'):
            head=f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            if filename:head+=f'; filename="{filename}"'
            return (head+f'\r\nContent-Type: {content_type}\r\n\r\n').encode()+value+b'\r\n'
        body=(part('model',os.getenv('TRANSCRIPTION_MODEL','whisper-large-v3-turbo').encode())+
              part('response_format',b'verbose_json')+
              part('timestamp_granularities[]',b'word')+
              part('file',wave.read_bytes(),'audio.mp3','audio/mpeg')+
              f'--{boundary}--\r\n'.encode())
        req=urllib.request.Request(url,data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary='+boundary,'User-Agent':'ClipStudio/2.1','Accept':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=180) as response:result=json.load(response)
        words=result.get('words') or []
        if not words:words=[{'start':v['start'],'end':v['end'],'word':v.get('text','')} for v in result.get('segments',[])]
        if not words:return None,'Transcription response contained no timestamped speech. Verify the audio contains speech.'
        return {'text':result.get('text',''),'words':words},None
    except urllib.error.HTTPError as exc:
        detail=exc.read(600).decode('utf-8','replace')
        return None,f'Transcription API HTTP {exc.code}: {detail[:350]}'
    except Exception as exc:
        return None,('Transcription failed: '+str(exc))[:400]
    finally:
        wave.unlink(missing_ok=True)

REACTIONS=('no way','oh my','unbelievable','insane','amazing','wait','what','wow','crazy','never','how','why','look','finally','incredible','surprise')
def hybrid_picks(path,dur,count,span,transcript):
    audio=analyze(path,dur,max(count*2,count),span)
    words=(transcript or {}).get('words') or []
    if not words:return [(a,b,score,'Audio activity') for a,b,score in audio[:count]]
    candidates=[]
    step=max(8,span/2)
    t=0
    while t<dur:
        a=max(0,min(t,max(0,dur-span)));b=min(dur,a+span)
        selected=[w for w in words if a<=float(w.get('start',-1))<b]
        if len(selected)>=4:
            text=' '.join(str(w.get('word',w.get('text',''))) for w in selected)
            lower=text.lower()
            reactions=sum(lower.count(k) for k in REACTIONS)
            score=0.15*min(5,reactions)+0.15*min(3,text.count('?')+text.count('!'))+min(0.45,len(selected)/max(1,span*2.5)*0.45)
            energy=max((v for x,y,v in audio if max(0,min(b,y)-max(a,x))>3),default=0)
            candidates.append((round(a,2),round(b,2),round(score+min(energy,0.4),4),f'Speech highlight: {len(selected)} words, {reactions} reactions'))
        t+=step
    chosen=[]
    for item in sorted(candidates,key=lambda item:item[2],reverse=True):
        if any(max(0,min(item[1],v[1])-max(item[0],v[0]))>span*0.4 for v in chosen):continue
        chosen.append(item)
        if len(chosen)>=count:break
    if not chosen:return [(a,b,v,'Audio activity') for a,b,v in audio[:count]]
    return sorted(chosen,key=lambda v:v[0])

def process_video(vid, count=8, clip_length=35):
    row=execute('SELECT * FROM videos WHERE id=?',(vid,)).fetchone()
    if not row:return
    try:
        d=duration(row['path'])
        transcript,error=transcribe(Path(row['path']))
        execute('INSERT OR REPLACE INTO transcripts(video_id,data,provider,error) VALUES(?,?,?,?)',(vid,json.dumps(transcript) if transcript else None,'Whisper API' if transcript else 'audio-only',error))
        picks=hybrid_picks(Path(row['path']),d,count,min(clip_length,d),transcript)
        with lock:
            with db() as c:
                c.execute('UPDATE videos SET status=?,duration=?,error=NULL WHERE id=?',('ready',d,vid))
                for i,(start,end,score,reason) in enumerate(picks,1):
                    c.execute('INSERT INTO clips(id,video_id,title,start,end,score,status,path,created,caption_style,reason,aspect_ratio,facecam_mode,facecam_region) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid(),vid,f'Highlight {i:02d}',start,end,score,'review',None,utc(),'bold' if transcript else 'none',reason,row['aspect_ratio'] or '9:16',row['facecam_mode'] or 'off',row['facecam_region']))
    except Exception as e:
        execute('UPDATE videos SET status=?,error=? WHERE id=?',('failed',str(e)[:450],vid))

def serialize(row):return dict(row)

def get_clip(cid):
    row=execute('SELECT * FROM clips WHERE id=?',(cid,)).fetchone()
    if not row:raise HTTPException(404,'Clip not found')
    return row

@app.get('/health')
def health():return {'ok':True}

@app.get('/')
def root_page(_:None=Depends(auth)):
    return FileResponse(Path(__file__).parent/'static'/'index.html')

@app.get('/api/state')
def state(_:None=Depends(auth)):
    return {'videos':[serialize(x) for x in execute('SELECT v.id,v.name,v.source,v.status,v.duration,v.error,v.created,v.aspect_ratio,v.facecam_mode,v.facecam_region,t.provider AS transcript_provider,t.error AS transcript_error,CASE WHEN t.data IS NOT NULL THEN 1 ELSE 0 END AS has_transcript FROM videos v LEFT JOIN transcripts t ON t.video_id=v.id ORDER BY v.created DESC').fetchall()],
        'clips':[serialize(x) for x in execute('SELECT id,video_id,title,start,end,score,status,created,caption_style,reason,aspect_ratio,facecam_mode,facecam_region,layout FROM clips ORDER BY created DESC').fetchall()],
        'monitors':[serialize(x) for x in execute('SELECT id,label,platform,status,error,created FROM monitors ORDER BY created DESC').fetchall()]}

@app.get('/api/capabilities')
def capabilities(_:None=Depends(auth)):
    return {'transcription_enabled':bool(os.getenv('TRANSCRIPTION_API_KEY','').strip()),'live_enabled':os.getenv('ENABLE_LIVE_MONITOR','false').lower()=='true','version':'2.0'}

@app.get('/api/videos/{vid}/transcript')
def transcript_data(vid:str,_:None=Depends(auth)):
    row=execute('SELECT data,provider,error FROM transcripts WHERE video_id=?',(vid,)).fetchone()
    if not row:raise HTTPException(404,'Transcript unavailable')
    return {'transcript':json.loads(row['data']) if row['data'] else None,'provider':row['provider'],'error':row['error']}

@app.post('/api/upload')
async def upload(file:UploadFile=File(...),highlight_count:int=Form(8),clip_length:int=Form(35),aspect_ratio:str=Form('9:16'),has_facecam:bool=Form(False),facecam_region:str=Form(''),_:None=Depends(auth)):
    if not 1<=highlight_count<=12 or not 10<=clip_length<=90:raise HTTPException(400,'Select 1–12 highlights and clip length 10–90 seconds')
    if aspect_ratio not in ('9:16','1:1','16:9'):raise HTTPException(400,'Choose a valid aspect ratio')
    if has_facecam and aspect_ratio!='9:16':raise HTTPException(400,'Facecam layout is currently supported only for 9:16')
    region=None
    if has_facecam:
        try:region=validate_region(json.loads(facecam_region))
        except (ValueError,TypeError,KeyError):raise HTTPException(400,'Choose and crop the facecam before uploading')
        if region is None:raise HTTPException(400,'Crop the facecam before uploading')
    name=Path(file.filename or 'recording').name
    if Path(name).suffix.lower() not in ('.mp4','.mov','.mkv','.webm','.ts','.m4v'):
        raise HTTPException(400,'Please use MP4, MOV, MKV, WEBM, TS or M4V')
    vid=uid(); dest=ROOT/'uploads'/(vid+Path(name).suffix.lower())
    maxsize=int(float(os.getenv('MAX_UPLOAD_GB','2'))*1024**3)
    total=0
    try:
        with dest.open('wb') as output:
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>maxsize:raise HTTPException(413,'Upload exceeds allowed file size')
                output.write(chunk)
        if not total:raise HTTPException(400,'Empty file')
    except Exception:
        dest.unlink(missing_ok=True);raise
    execute('INSERT INTO videos(id,name,source,status,path,duration,error,created,aspect_ratio,facecam_mode,facecam_region) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(vid,name,'upload','processing',str(dest),None,None,utc(),aspect_ratio,'manual' if has_facecam else 'off',json.dumps(region) if region else None))
    threading.Thread(target=process_video,args=(vid,highlight_count,clip_length),daemon=True).start()
    return {'id':vid,'status':'processing'}

@app.post('/api/videos/{vid}/reanalyze')
def reanalyze(vid:str,highlight_count:int=8,clip_length:int=35,_:None=Depends(auth)):
    if not 1<=highlight_count<=12 or not 10<=clip_length<=90:raise HTTPException(400,'Invalid highlight settings')
    video=execute('SELECT * FROM videos WHERE id=?',(vid,)).fetchone()
    if not video:raise HTTPException(404,'Video not found')
    if not Path(video['path']).exists():raise HTTPException(404,'Original recording is no longer available on temporary storage')
    if video['status']=='processing':raise HTTPException(409,'Analysis already underway')
    with lock:
        with db() as c:
            c.execute("DELETE FROM clips WHERE video_id=? AND status IN ('review','render_failed')",(vid,))
            c.execute('UPDATE videos SET status=?,error=NULL WHERE id=?',('processing',vid))
    threading.Thread(target=process_video,args=(vid,highlight_count,clip_length),daemon=True).start()
    return {'status':'processing'}

@app.get('/api/video/{vid}/stream')
def video_stream(vid:str,request:Request,_:None=Depends(auth)):
    row=execute('SELECT path FROM videos WHERE id=?',(vid,)).fetchone()
    if not row:raise HTTPException(404,'Video not found')
    return serve_range(Path(row['path']),request)

def serve_range(path:Path,request:Request):
    if not path.exists():raise HTTPException(404,'Media unavailable')
    size=path.stat().st_size; match=re.match(r'bytes=(\d*)-(\d*)',request.headers.get('range',''))
    ext=path.suffix.lower(); mime={'mp4':'video/mp4','mov':'video/quicktime','webm':'video/webm','mkv':'video/x-matroska','ts':'video/mp2t'}.get(ext.lstrip('.'),'application/octet-stream')
    if match:
        a,b=match.groups()
        if not a and b:
            start=max(0,size-int(b));end=size-1
        else:
            start=int(a or 0);end=min(size-1,int(b)) if b else size-1
        if start>=size or end<start:raise HTTPException(416,'Invalid range')
    else:start=0;end=size-1
    def iter_file():
        with path.open('rb') as f:
            f.seek(start);remaining=end-start+1
            while remaining:
                block=f.read(min(1024*1024,remaining))
                if not block:break
                remaining-=len(block);yield block
    headers={'Accept-Ranges':'bytes','Content-Length':str(end-start+1),'Cache-Control':'private, no-store'}
    if match:headers['Content-Range']=f'bytes {start}-{end}/{size}'
    return StreamingResponse(iter_file(),status_code=206 if match else 200,headers=headers,media_type=mime)

class ClipEdit(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    start:float=Field(ge=0)
    end:float=Field(gt=0)
    caption_style:str='none'
    layout:dict|None=None

@app.patch('/api/clips/{cid}')
def clip_edit(cid:str,p:ClipEdit,_:None=Depends(auth)):
    clip=get_clip(cid); row=execute('SELECT duration FROM videos WHERE id=?',(clip['video_id'],)).fetchone()
    if p.end<=p.start or p.end-p.start>180 or (row['duration'] is not None and p.end>row['duration']+0.01):
        raise HTTPException(400,'Choose a valid range of up to 180 seconds within the video')
    if p.caption_style not in ('none','bold','minimal'):raise HTTPException(400,'Invalid subtitle style')
    if p.caption_style!='none':
        transcript=execute('SELECT data,error FROM transcripts WHERE video_id=?',(clip['video_id'],)).fetchone()
        if (not transcript or not transcript['data']) and not clip['caption_edits']:
            raise HTTPException(409,'No transcript or manually entered subtitles. Add subtitles in the editor before exporting.')
    layout=validate_layout(p.layout,clip) if p.layout is not None else (clip['layout'] or None)
    execute('UPDATE clips SET title=?,start=?,end=?,caption_style=?,layout=?,status=?,path=NULL WHERE id=?',
        (p.title,p.start,p.end,p.caption_style,json.dumps(layout) if isinstance(layout,dict) else layout,'review',cid))
    return {'ok':True}

def generated_captions(clip):
    row=execute('SELECT data FROM transcripts WHERE video_id=?',(clip['video_id'],)).fetchone()
    if not row or not row['data']:return []
    words=json.loads(row['data']).get('words') or []
    selected=[w for w in words if float(w.get('end',0))>clip['start'] and float(w.get('start',0))<clip['end']]
    chunks=[]
    step=5
    for index in range(0,len(selected),step):
        part=selected[index:index+step]
        start=max(clip['start'],float(part[0].get('start',clip['start'])))
        end=min(clip['end'],float(part[-1].get('end',start+0.5)))
        if end<=start:continue
        line=' '.join(str(w.get('word',w.get('text',''))).strip() for w in part).strip()
        if line:chunks.append({'start':round(start,3),'end':round(end,3),'text':line})
    return chunks

def captions_for_clip(clip):
    if clip['caption_edits']:
        return json.loads(clip['caption_edits'])
    return generated_captions(clip)

@app.get('/api/clips/{cid}/captions')
def get_captions(cid:str,_:None=Depends(auth)):
    clip=get_clip(cid)
    return {'captions':captions_for_clip(clip),'edited':bool(clip['caption_edits'])}

class CaptionSegment(BaseModel):
    start:float=Field(ge=0)
    end:float=Field(gt=0)
    text:str=Field(min_length=1,max_length=400)

class CaptionChanges(BaseModel):
    captions:list[CaptionSegment]=Field(max_length=500)

@app.put('/api/clips/{cid}/captions')
def update_captions(cid:str,p:CaptionChanges,_:None=Depends(auth)):
    clip=get_clip(cid)
    if clip['status']=='rendering':raise HTTPException(409,'Cannot edit captions while exporting')
    previous_end=clip['start']
    payload=[]
    for segment in p.captions:
        if segment.start<clip['start']-0.02 or segment.end>clip['end']+0.02 or segment.end<=segment.start or segment.start<previous_end-0.02:
            raise HTTPException(400,'Caption timestamps must be ordered and inside the highlight')
        payload.append({'start':round(segment.start,3),'end':round(segment.end,3),'text':segment.text.strip()})
        previous_end=segment.end
    # An empty list intentionally removes all subtitle lines for this clip.
    execute('UPDATE clips SET caption_edits=?,path=NULL,status=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),'review',cid))
    return {'saved':True,'count':len(payload)}


def validate_region(region):
    if region is None:return None
    try:
        x,y,w,h=[float(region[k]) for k in ('x','y','w','h')]
    except (TypeError,ValueError,KeyError):
        raise HTTPException(400,'Invalid facecam region')
    if not all(math.isfinite(n) for n in (x,y,w,h)) or x<0 or y<0 or w<0.08 or h<0.08 or x+w>1.0001 or y+h>1.0001:
        raise HTTPException(400,'Facecam region must be inside the video and at least 8% wide and tall')
    return {k:round(v,4) for k,v in zip(('x','y','w','h'),(x,y,w,h))}


def validate_layout(layout,clip):
    if not isinstance(layout,dict):raise HTTPException(400,'Invalid layout settings')
    defaults={'split':40,'cam_zoom':1.0,'cam_x':0,'cam_y':0,'game_zoom':1.0,'game_x':0,'game_y':0}
    result={}
    for key,default in defaults.items():
        value=layout.get(key,default)
        if isinstance(value,bool):raise HTTPException(400,'Invalid layout value')
        try:value=float(value)
        except (ValueError,TypeError):raise HTTPException(400,'Invalid layout value')
        if not math.isfinite(value):raise HTTPException(400,'Invalid layout value')
        lo,hi=(25,65) if key=='split' else (1,3) if key.endswith('zoom') else (-100,100)
        if not lo<=value<=hi:raise HTTPException(400,'Layout values out of range')
        result[key]=round(value,3)
    return result

@app.post('/api/clips/{cid}/layout/all')
def apply_layout_to_video(cid:str,layout:dict,_:None=Depends(auth)):
    clip=get_clip(cid)
    data=validate_layout(layout,clip)
    # Only unapproved clips; leave already exported files and concurrent renders unchanged.
    with lock:
        with db() as c:
            affected=c.execute("UPDATE clips SET layout=? WHERE video_id=? AND status IN ('review','render_failed')",
                 (json.dumps(data),clip['video_id'])).rowcount
    return {'updated':affected}

def srt_clock(seconds):
    ms=max(0,round(seconds*1000));h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def subtitle_file(clip):
    if clip['caption_style']=='none':return None
    captions=captions_for_clip(clip)
    if not captions:return None
    chunks=[]
    for item in captions:
        a=max(0,float(item['start'])-clip['start'])
        b=min(clip['end']-clip['start'],float(item['end'])-clip['start'])
        if b<=a:continue
        line=str(item['text']).replace('<','').replace('>','').replace('{','').replace('}','').replace('\n',' ')
        if clip['caption_style']=='bold':line=line.upper()
        chunks.append(f"{len(chunks)+1}\n{srt_clock(a)} --> {srt_clock(b)}\n{line}\n")
    if not chunks:return None
    path=ROOT/'clips'/(clip['id']+'.srt')
    path.write_text('\n'.join(chunks),encoding='utf-8')
    return path

def render_job(cid):
    clip=get_clip(cid); video=execute('SELECT path FROM videos WHERE id=?',(clip['video_id'],)).fetchone()
    output=ROOT/'clips'/(cid+'.mp4');tmp=ROOT/'clips'/(cid+'.partial.mp4')
    try:
        ratio=clip['aspect_ratio'] or '9:16'
        if ratio not in ('9:16','1:1','16:9'):raise ValueError('Unknown aspect ratio')
        width,height={'9:16':(540,960),'1:1':(720,720),'16:9':(960,540)}[ratio]
        region=json.loads(clip['facecam_region']) if clip['facecam_region'] else None
        if ratio=='9:16' and clip['facecam_mode']!='off':
            region=validate_region(region)
            if not region:raise ValueError('Facecam region missing')
            config=validate_layout(json.loads(clip['layout']) if clip['layout'] else {},clip)
            split=int(round(config['split']*height/100/2)*2)
            bottom=height-split
            x,y,w,h=(region[k] for k in ('x','y','w','h'))
            def panel_filter(source,target_h,zoom,px,py,output):
                # Cover the panel, with adjustable zoom and pan. Crop after zoom prevents black bars.
                sw=int(round(width*zoom/2)*2)
                sh=int(round(target_h*zoom/2)*2)
                return (f"[{source}]scale={sw}:{sh}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{target_h}:(in_w-out_w)*{(px+100)/200}:(in_h-out_h)*{(py+100)/200},setsar=1[{output}]")
            cam_part=panel_filter('cropped',split,config['cam_zoom'],config['cam_x'],config['cam_y'],'top')
            game_part=panel_filter('game',bottom,config['game_zoom'],config['game_x'],config['game_y'],'bottom')
            filtergraph=(f"[0:v]split=2[cam][game];"
                f"[cam]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}[cropped];"
                f"{cam_part};{game_part};"
                "[top][bottom]vstack=inputs=2,format=yuv420p[v]")
        else:
            filtergraph=(f"[0:v]split=2[bg][fg];"
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=30:10[base];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[small];"
                "[base][small]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")
        subs=subtitle_file(clip)
        if subs:
            escaped=str(subs).replace('\\','\\\\').replace(':','\\:').replace("'","\\'")
            style='FontName=DejaVu Sans,FontSize=18,Bold=1,Outline=2,Alignment=2,MarginV=90' if clip['caption_style']=='bold' else 'FontName=DejaVu Sans,FontSize=15,Outline=1,Alignment=2,MarginV=90'
            filtergraph+=f";[v]subtitles=filename='{escaped}':force_style='{style}'[outv]"
        video_map='[outv]' if subs else '[v]'
        cmd(['ffmpeg','-y','-nostdin','-v','error','-ss',str(clip['start']),'-i',video['path'],'-t',str(clip['end']-clip['start']),'-filter_complex',filtergraph,'-map',video_map,'-map','0:a:0?','-c:v','libx264','-preset','veryfast','-crf','24','-r','30','-c:a','aac','-b:a','128k','-movflags','+faststart',str(tmp)],timeout=900)
        os.replace(tmp,output)
        execute('UPDATE clips SET path=?,status=? WHERE id=?',(str(output),'exported',cid))
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        execute('UPDATE clips SET status=? WHERE id=?',('render_failed',cid))
        print('Clip export failed:',str(exc)[:600],flush=True)

@app.post('/api/clips/{cid}/approve')
def approve(cid:str,_:None=Depends(auth)):
    clip=get_clip(cid)
    if clip['status']=='rendering':raise HTTPException(409,'Already rendering')
    execute('UPDATE clips SET status=? WHERE id=?',('rendering',cid))
    threading.Thread(target=render_job,args=(cid,),daemon=True).start()
    return {'status':'rendering'}

@app.get('/api/clips/{cid}/download')
def download(cid:str,_:None=Depends(auth)):
    clip=get_clip(cid)
    if clip['status']!='exported' or not clip['path'] or not Path(clip['path']).exists():raise HTTPException(409,'Export not ready')
    return FileResponse(clip['path'],media_type='video/mp4',filename=f'{re.sub(r"[^a-zA-Z0-9_-]","_",clip["title"])}.mp4')

@app.delete('/api/clips/{cid}')
def delete_clip(cid:str,_:None=Depends(auth)):
    clip=get_clip(cid)
    if clip['status']=='rendering':raise HTTPException(409,'Rendering in progress')
    if clip['path']:Path(clip['path']).unlink(missing_ok=True)
    execute('DELETE FROM clips WHERE id=?',(cid,))
    return {'ok':True}

class MonitorCreate(BaseModel):
    label:str=Field(min_length=1,max_length=100)
    platform:str
    source_url:str

def validate_url(url):
    # Only https playlists, no localhost or userinfo. Operators must supply an authorized feed.
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or not p.path.lower().endswith('.m3u8'):
        raise HTTPException(400,'Provide an authorized HTTPS HLS .m3u8 URL')
    import ipaddress, socket
    try:
        for result in socket.getaddrinfo(p.hostname,443):
            if not ipaddress.ip_address(result[4][0]).is_global:raise HTTPException(400,'Private or local stream targets are not allowed')
    except socket.gaierror:raise HTTPException(400,'Cannot resolve feed hostname')

@app.post('/api/monitors')
def monitor_add(p:MonitorCreate,_:None=Depends(auth)):
    if p.platform not in ('twitch','youtube','other'):raise HTTPException(400,'Invalid platform')
    validate_url(p.source_url)
    mid=uid()
    execute('INSERT INTO monitors VALUES(?,?,?,?,?,?,?)',(mid,p.label,p.platform,p.source_url,'stopped',None,utc()))
    return {'id':mid}

def record_live(mid, stop):
    r=execute('SELECT * FROM monitors WHERE id=?',(mid,)).fetchone()
    segment_dir=ROOT/'live'/mid;segment_dir.mkdir(parents=True,exist_ok=True)
    # Segment muxer writes files every 120s. Completed segments are analyzed while stream runs.
    command=['ffmpeg','-nostdin','-v','error','-i',r['source_url'],'-map','0:v:0','-map','0:a:0?','-c','copy','-f','segment','-segment_time','120','-reset_timestamps','1',str(segment_dir/'part-%05d.ts')]
    proc=None;seen=set()
    try:
        proc=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        while not stop.is_set() and proc.poll() is None:
            time.sleep(3)
            files=sorted(segment_dir.glob('part-*.ts'))
            for item in files[:-1]:
                if item.name in seen:continue
                seen.add(item.name)
                vid=uid();dest=ROOT/'uploads'/(vid+'.ts')
                shutil.copyfile(item,dest)
                execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?)',(vid,r['label']+' · '+item.stem,r['platform']+' live','processing',str(dest),None,None,utc()))
                threading.Thread(target=process_video,args=(vid,4),daemon=True).start()
                item.unlink(missing_ok=True)
        if proc.poll() is not None and not stop.is_set():
            err=proc.stderr.read(1024).decode(errors='replace') if proc.stderr else ''
            execute('UPDATE monitors SET status=?,error=? WHERE id=?',('failed',err[-450:] or 'Livestream disconnected',mid))
    except Exception as e:execute('UPDATE monitors SET status=?,error=? WHERE id=?',('failed',str(e)[:450],mid))
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill()
        if stop.is_set():execute('UPDATE monitors SET status=? WHERE id=?',('stopped',mid))
        LIVE.pop(mid,None)

@app.post('/api/monitors/{mid}/start')
def start_monitor(mid:str,_:None=Depends(auth)):
    if os.getenv('ENABLE_LIVE_MONITOR','false').lower() != 'true':
        raise HTTPException(503,'Live monitoring is disabled on the free test deployment. Upload a short video instead.')
    if mid in LIVE:raise HTTPException(409,'Monitor already running')
    row=execute('SELECT id FROM monitors WHERE id=?',(mid,)).fetchone()
    if not row:raise HTTPException(404,'Monitor not found')
    stop=threading.Event();LIVE[mid]=stop
    execute('UPDATE monitors SET status=?,error=NULL WHERE id=?',('running',mid))
    threading.Thread(target=record_live,args=(mid,stop),daemon=True).start()
    return {'status':'running'}

@app.post('/api/monitors/{mid}/stop')
def stop_monitor(mid:str,_:None=Depends(auth)):
    if mid not in LIVE:raise HTTPException(409,'Monitor is not running on this server')
    LIVE[mid].set()
    return {'status':'stopping'}

@app.on_event('shutdown')
def shutdown():
    for item in list(LIVE.values()):item.set()
