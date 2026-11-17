import os, re, json, time, uuid, sqlite3, secrets, subprocess, threading, math, shutil
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
init()

def utc():return datetime.now(timezone.utc).isoformat()
def uid():return uuid.uuid4().hex

def cmd(args,timeout=300):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=True)

def duration(path):
    data=json.loads(cmd(['ffprobe','-v','quiet','-show_entries','format=duration','-of','json',str(path)],30).stdout)
    return float(data['format']['duration'])

def analyze(path, dur, count=8):
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
    span=min(35,max(8,dur/2))
    for idx in ranked:
        center=idx*window+window/2
        start=max(0,min(center-span/2,dur-span)); end=min(dur,start+span)
        if any(max(0,min(end,b)-max(start,a))>span*0.30 for a,b,_ in chosen):continue
        chosen.append((round(start,2),round(end,2),round(energies[idx],5)))
        if len(chosen)>=count:break
    if not chosen:chosen=[(0,min(30,dur),0.1)]
    return sorted(chosen,key=lambda x:x[0])

def process_video(vid, count=8):
    row=execute('SELECT * FROM videos WHERE id=?',(vid,)).fetchone()
    if not row:return
    try:
        d=duration(row['path'])
        picks=analyze(Path(row['path']),d,count)
        with lock:
            with db() as c:
                c.execute('UPDATE videos SET status=?,duration=?,error=NULL WHERE id=?',('ready',d,vid))
                for i,(start,end,score) in enumerate(picks,1):
                    c.execute('INSERT INTO clips VALUES(?,?,?,?,?,?,?,?,?)',(uid(),vid,f'Highlight {i:02d}',start,end,score,'review',None,utc()))
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
    return {'videos':[serialize(x) for x in execute('SELECT id,name,source,status,duration,error,created FROM videos ORDER BY created DESC').fetchall()],
        'clips':[serialize(x) for x in execute('SELECT id,video_id,title,start,end,score,status,created FROM clips ORDER BY created DESC').fetchall()],
        'monitors':[serialize(x) for x in execute('SELECT id,label,platform,status,error,created FROM monitors ORDER BY created DESC').fetchall()]}

@app.post('/api/upload')
async def upload(file:UploadFile=File(...),_:None=Depends(auth)):
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
    execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?)',(vid,name,'upload','processing',str(dest),None,None,utc()))
    threading.Thread(target=process_video,args=(vid,),daemon=True).start()
    return {'id':vid,'status':'processing'}

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

@app.patch('/api/clips/{cid}')
def clip_edit(cid:str,p:ClipEdit,_:None=Depends(auth)):
    clip=get_clip(cid); row=execute('SELECT duration FROM videos WHERE id=?',(clip['video_id'],)).fetchone()
    if p.end<=p.start or p.end-p.start>180 or (row['duration'] is not None and p.end>row['duration']+0.01):
        raise HTTPException(400,'Choose a valid range of up to 180 seconds within the video')
    execute('UPDATE clips SET title=?,start=?,end=?,status=?,path=NULL WHERE id=?',(p.title,p.start,p.end,'review',cid))
    return {'ok':True}

def render_job(cid):
    clip=get_clip(cid); video=execute('SELECT path FROM videos WHERE id=?',(clip['video_id'],)).fetchone()
    output=ROOT/'clips'/(cid+'.mp4');tmp=ROOT/'clips'/(cid+'.partial.mp4')
    try:
        # Center crop to portrait, with 9:16 canvas; preserves original aspect with blurred backdrop.
        filtergraph="[0:v]split=2[bg][fg];[bg]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,boxblur=30:10[base];[fg]scale=540:960:force_original_aspect_ratio=decrease[small];[base][small]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
        cmd(['ffmpeg','-y','-nostdin','-v','error','-ss',str(clip['start']),'-i',video['path'],'-t',str(clip['end']-clip['start']),'-filter_complex',filtergraph,'-map','[v]','-map','0:a:0?','-c:v','libx264','-preset','veryfast','-crf','24','-r','30','-c:a','aac','-b:a','128k','-movflags','+faststart',str(tmp)],timeout=900)
        os.replace(tmp,output)
        execute('UPDATE clips SET path=?,status=? WHERE id=?',(str(output),'exported',cid))
    except Exception:
        tmp.unlink(missing_ok=True)
        execute('UPDATE clips SET status=? WHERE id=?',('render_failed',cid))

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
