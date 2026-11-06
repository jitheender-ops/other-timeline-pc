"""The Other Timeline on a small Windows PC: watches the videos folder, builds each video, shows a dashboard.

  python ot.py            start the dashboard (http://localhost:8095) and the builder
  python ot.py --quiet    same, without opening the browser (used at Windows sign-in)
  python ot.py --setup    one-time: folders, whisper model, render browser, desktop/startup shortcuts
  python ot.py --test     self-check of the small pure helpers

A video is a folder in videos/: the HyperFrames project (index.html with <audio id="vo" src=...>),
plus voice.wav/.mp3/.m4a (the narration) and video.json ({"title", "description", "kind": "long"|"short",
"schedule": {"date": "Nov 20, 2026", "time": "7:00 PM"}}). Dropping in the voice file starts the build:
clean + level the voice -> captions (whisper) -> render the picture -> master to -14 LUFS -> checks.
Nothing is uploaded until someone presses Approve on the dashboard.

Tuned for i5-10th gen / 8 GB RAM / 2 GB VRAM: one build at a time, whisper small.en int8 on the CPU,
HyperFrames' low-memory render (auto at <= 8 GB) with resumable segments, child processes at
below-normal priority so the PC stays usable, and Windows kept awake while a build runs.
"""
import json, os, re, shutil, subprocess, sys, threading, time, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
VIDEOS, LOGS = ROOT / "videos", ROOT / "logs"
LOG = LOGS / "ot.log"
PORT = 8095
WIN = os.name == "nt"
VOICE_EXT = (".wav", ".mp3", ".m4a", ".flac")
LUFS, TP = -14.0, -1.5
WHISPER = "small.en"  # ponytail: CPU int8 (~5 min per 15 min of voice); CUDA on the 2 GB card needs extra libs, add if speed matters

# winget puts new tools here, and a process started before the install doesn't see them on PATH yet
if WIN:
    la = os.environ.get("LOCALAPPDATA", "")
    os.environ["PATH"] = os.pathsep.join([os.environ["PATH"], rf"{la}\Microsoft\WinGet\Links",
                                          rf"{os.environ.get('ProgramFiles', '')}\nodejs"])

jobs = []  # video folder names waiting to build or upload, in order
lock, slock = threading.Lock(), threading.Lock()


def log(msg):
    LOGS.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def status(v, **kw):
    p = v / "status.json"
    with slock:  # the builder and the folder scan both write it
        s = json.loads(p.read_text()) if p.exists() else {}
        if kw:
            s.update(kw, updated=time.strftime("%Y-%m-%d %H:%M"))
            p.write_text(json.dumps(s, indent=1))
    return s


def meta(v):
    p = v / "video.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def voice_of(v):
    return next((p for p in sorted(v.iterdir()) if p.stem.lower() == "voice" and p.suffix.lower() in VOICE_EXT), None)


def keep_awake(on):
    if WIN:  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED: no sleep mid-render
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))


def sh(cmd, v=None, cwd=None, env=None):
    """Run a command at below-normal priority; every output line goes to the log and the card's detail."""
    log("$ " + " ".join(map(str, cmd)))
    p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env={**os.environ, **(env or {})}, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                         creationflags=0x00004000 if WIN else 0)  # BELOW_NORMAL_PRIORITY_CLASS
    out = []
    for line in p.stdout:
        line = line.rstrip()
        if line:
            out.append(line); out[:] = out[-200:]
            with open(LOG, "a", encoding="utf-8") as f:
                f.write("    " + line + "\n")
            if v: status(v, detail=line[-160:])
    if p.wait():
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed: {(out or ['no output'])[-1]}")
    return "\n".join(out)


def tool(name):
    return shutil.which(name + (".cmd" if WIN and name in ("npm", "npx") else "")) or name


def measure(src, pre=""):
    """loudnorm first pass -> the numbers the second pass needs."""
    af = (pre + "," if pre else "") + f"loudnorm=I={LUFS}:TP={TP}:LRA=11:print_format=json"
    out = subprocess.run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(src), "-af", af, "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return json.loads(out[out.rindex("{"):out.rindex("}") + 1])


def loudnorm(src, dst, v, pre="", video=False):
    m = measure(src, pre)
    af = (pre + "," if pre else "") + (f"loudnorm=I={LUFS}:TP={TP}:LRA=11:measured_I={m['input_i']}:"
          f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true")
    tmp = dst.with_suffix(".part" + dst.suffix)  # a crash never leaves a half file under the real name
    codec = ["-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart"] if video else ["-c:a", "pcm_s16le"]
    sh([tool("ffmpeg"), "-v", "error", "-y", "-i", src, "-af", af, "-ar", "48000", *codec, tmp], v)
    tmp.replace(dst)


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def to_srt(segments):
    return "\n".join(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t.strip()}\n" for i, (s, e, t) in enumerate(segments, 1))


def transcribe(wav, b, v):
    from faster_whisper import WhisperModel
    status(v, detail="loading the speech model")
    model = WhisperModel(WHISPER, device="cpu", compute_type="int8", cpu_threads=min(4, os.cpu_count() or 4))
    segs, words = [], []
    for s in model.transcribe(str(wav), word_timestamps=True, vad_filter=True)[0]:
        segs.append((s.start, s.end, s.text))
        words += [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for w in s.words or []]
        status(v, detail=f"heard {srt_time(s.end)[:8]}: {s.text.strip()[:80]}")
    del model  # frees ~1 GB before the render needs it
    (b / "captions.srt").write_text(to_srt(segs), encoding="utf-8")
    (b / "words.json").write_text(json.dumps(words), encoding="utf-8")


def scene_audio(html):
    """(vo src, composition seconds) from the project's index.html."""
    tag = re.search(r'<audio[^>]*\bid="vo"[^>]*>', html)
    src = tag and re.search(r'\bsrc="([^"]+)"', tag.group(0))
    dur = re.search(r'data-composition-id="main"[^>]*\bdata-duration="([\d.]+)"', html) \
        or re.search(r'\bdata-duration="([\d.]+)"', html)
    return (src.group(1) if src else None), (float(dur.group(1)) if dur else None)


def duration(f):
    return float(subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(f)], capture_output=True, text=True).stdout.strip() or 0)


def size(f):
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip()
    return tuple(map(int, out.split(","))) if out else (0, 0)


def checks(v, b, voice_s, comp_s):
    final, kind = b / "final.mp4", meta(v).get("kind", "long")
    m, (w, h), d = measure(final), size(final), duration(final)
    qc = [
        ("Loudness about -14 LUFS", abs(float(m["input_i"]) + 14) <= 1, f"{float(m['input_i']):.1f} LUFS"),
        ("Peaks below -1 dB", float(m["input_tp"]) <= -1.0, f"{float(m['input_tp']):.1f} dBTP"),
        ("Voice fits the picture", not comp_s or voice_s <= comp_s + 0.5,
         f"voice {voice_s:.0f}s, picture {comp_s or d:.0f}s"),
        ("Right shape", (h > w) if kind == "short" else (w > h), f"{w}x{h} ({kind})"),
        ("Has a title", bool(meta(v).get("title")), meta(v).get("title", "add a title in video.json")[:60]),
    ]
    if kind == "short":
        qc.append(("Short is 60 s or less", d <= 60.5, f"{d:.0f}s"))
    sheet = b / "sheet.jpg"  # 30 frames across the whole video, for a quick look at pacing
    sh([tool("ffmpeg"), "-v", "error", "-y", "-i", final, "-vf",
        f"fps={30 / max(d, 1):.5f},scale=320:-2,tile=6x5", "-frames:v", "1", sheet], v)
    return [{"name": n, "ok": bool(ok), "detail": det} for n, ok, det in qc]


def build(v):
    b = v / "build"; b.mkdir(exist_ok=True)
    voice = voice_of(v)
    index = v / "index.html"
    if not index.exists():
        raise RuntimeError("this folder has no index.html (the HyperFrames scene). Copy the whole scene folder in.")
    vo_src, comp_s = scene_audio(index.read_text(encoding="utf-8"))
    if not vo_src:
        raise RuntimeError('the scene has no <audio id="vo" src="..."> line for the voice')
    status(v, state="building", step="1/5 Cleaning the voice", error=None, voice_mtime=voice.stat().st_mtime)
    loudnorm(voice, b / "voice.wav", v, pre="highpass=f=80,deesser,acompressor=threshold=-20dB:ratio=3:attack=5:release=80")
    status(v, step="2/5 Writing captions")
    transcribe(b / "voice.wav", b, v)
    status(v, step="3/5 Rendering the picture (the long part)")
    shutil.copy(b / "voice.wav", v / vo_src)
    hf_env = {"HF_SEGMENTED_CAPTURE": "true"}  # --resume continues a render that a crash or restart stopped
    sh([tool("npm"), "run", "render", "--", "-o", b / "picture.mp4", "-q", "delivery", "--resume",
        "--protocol-timeout", "900000", "--player-ready-timeout", "180000"], v, cwd=v, env=hf_env)
    status(v, step="4/5 Mastering the sound")
    loudnorm(b / "picture.mp4", b / "final.mp4", v, video=True)
    status(v, step="5/5 Checking")
    qc = checks(v, b, duration(b / "voice.wav"), comp_s)
    status(v, state="review", step=None, detail=None, qc=qc)
    log(f"{v.name}: ready for review ({sum(c['ok'] for c in qc)}/{len(qc)} checks pass)")


def upload(v):
    import studio
    status(v, state="uploading", step="Uploading to YouTube Studio", error=None)
    vid = studio.upload(v / "build" / "final.mp4", meta(v), lambda d: status(v, detail=d))
    status(v, state="uploaded", step=None, detail=None, video_id=vid, link=f"https://studio.youtube.com/video/{vid}/edit")
    log(f"{v.name}: uploaded as {vid}")


def worker():
    while True:
        with lock:
            name = jobs.pop(0) if jobs else None
        if not name:
            time.sleep(2); continue
        v = VIDEOS / name
        keep_awake(True)
        try:
            (upload if status(v).get("state") == "approved" else build)(v)
        except BaseException as e:
            log(f"{name}: FAILED\n{traceback.format_exc()}")
            status(v, state="failed" if status(v).get("state") != "uploading" else "upload_failed", error=str(e)[:400])
        finally:
            keep_awake(False)


def queue(name):
    with lock:
        if name not in jobs:
            jobs.append(name)


def scan():
    """Every 20 s: a folder with a new or replaced voice file gets built."""
    for v in sorted(p for p in VIDEOS.iterdir() if p.is_dir()):
        s, voice = status(v), voice_of(v)
        if not voice:
            if s.get("state") != "waiting_voice": status(v, state="waiting_voice")
            continue
        replaced = voice.stat().st_mtime != s.get("voice_mtime")
        if s.get("state") in (None, "waiting_voice") or (replaced and s.get("state") in ("review", "uploaded", "failed")):
            status(v, state="queued", step=None, error=None); queue(v.name)


def watcher():
    for v in (p for p in VIDEOS.iterdir() if p.is_dir()):  # work cut off by a restart
        st = status(v).get("state")
        if st in ("queued", "building", "approved"):
            status(v, state="approved" if st == "approved" else "queued"); queue(v.name)
        elif st == "uploading":  # a half-done Studio dialog can still finish by itself: never re-upload blind
            status(v, state="upload_failed", error="The PC restarted during the upload. Check YouTube Studio for this "
                                                   "video first; press Approve again only if it isn't there.")
    while True:
        try:
            scan()
        except Exception as e:
            log(f"scan: {e}")
        time.sleep(20)


def chrome_ok():
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:9223/json/version", timeout=2); return True
    except Exception:
        return False


def system():
    import psutil
    disk = shutil.disk_usage(ROOT)
    return {"cpu": psutil.cpu_percent(interval=None), "ram": psutil.virtual_memory().percent,
            "disk_free_gb": round(disk.free / 1e9), "chrome": chrome_ok(), "queue": list(jobs)}


def videos():
    out = []
    for v in sorted((p for p in VIDEOS.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        out.append({"name": v.name, "title": meta(v).get("title", ""), "kind": meta(v).get("kind", "long"),
                    "has_sheet": (v / "build" / "sheet.jpg").exists(), **status(v)})
    return out


def tail(n=60):
    return LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:] if LOG.exists() else []


def video_dir(q):
    name = (q.get("v") or [""])[0]
    v = (VIDEOS / name).resolve()
    if not name or v.parent != VIDEOS.resolve() or not v.is_dir():
        raise KeyError(name)
    return v


def open_path(p):
    if WIN: os.startfile(p)
    else: subprocess.Popen(["open", str(p)])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def local(self):  # DNS rebinding: a hostile site renamed to 127.0.0.1 still sends its own Host
        if self.headers.get("Host") in (f"localhost:{PORT}", f"127.0.0.1:{PORT}"):
            return True
        self.send(403, {"error": "forbidden"})

    def do_GET(self):
        if not self.local(): return
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/":
            return self.send(200, (Path(__file__).parent / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api":
            return self.send(200, {"system": system(), "videos": videos(), "log": tail()})
        if u.path == "/sheet":
            try:
                return self.send(200, (video_dir(q) / "build" / "sheet.jpg").read_bytes(), "image/jpeg")
            except (KeyError, OSError):
                return self.send(404, {"error": "no sheet"})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.local(): return
        if self.headers.get("X-OT") != "1":  # other websites can't add this header, so they can't press buttons
            return self.send(403, {"error": "forbidden"})
        u = urlparse(self.path); q = parse_qs(u.query)
        try:
            if u.path == "/signin":
                import studio; studio.chrome(); return self.send(200, {"ok": True})
            if u.path == "/folder":
                open_path(VIDEOS); return self.send(200, {"ok": True})
            v = video_dir(q)
            if u.path == "/play":
                open_path(v / "build" / "final.mp4")
            elif u.path == "/rebuild":
                status(v, state="queued", error=None); queue(v.name)
            elif u.path == "/approve":
                if status(v).get("state") not in ("review", "upload_failed"):
                    return self.send(409, {"error": "not ready"})
                status(v, state="approved", error=None); queue(v.name)
            else:
                return self.send(404, {"error": "not found"})
            self.send(200, {"ok": True})
        except KeyError:
            self.send(404, {"error": "no such video"})
        except Exception as e:
            self.send(500, {"error": str(e)})


def setup():
    for d in (VIDEOS, LOGS, ROOT / "chrome-profile"):
        d.mkdir(exist_ok=True)
    print("Downloading the speech model (about 250 MB)...", flush=True)
    from faster_whisper import WhisperModel
    WhisperModel(WHISPER, device="cpu", compute_type="int8")
    print("Downloading the render browser...", flush=True)
    subprocess.run([tool("npx"), "--yes", "hyperframes@0.8.225", "browser", "ensure"], check=True)
    for t in ("ffmpeg", "ffprobe"):
        subprocess.run([tool(t), "-version"], capture_output=True, check=True)
    if WIN:  # desktop icon + start at sign-in (quietly)
        pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        for folder, args in (("Desktop", ""), ("Startup", " --quiet")):
            ps = ("$w=New-Object -ComObject WScript.Shell;"
                  f"$s=$w.CreateShortcut([Environment]::GetFolderPath('{folder}')+'\\The Other Timeline.lnk');"
                  f"$s.TargetPath='{pyw}';$s.Arguments='\"{Path(__file__)}\"{args}';$s.WorkingDirectory='{ROOT}';$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print("Setup finished.", flush=True)


def selftest():
    assert srt_time(3725.5) == "01:02:05,500"
    assert to_srt([(0, 1.2, " Hi ")]) == "1\n00:00:00,000 --> 00:00:01,200\nHi\n"
    html = '<div data-composition-id="main" data-start="0" data-duration="27"><audio id="vo" src="assets/audio/vo.wav" data-start="1">'
    assert scene_audio(html) == ("assets/audio/vo.wav", 27.0)
    assert scene_audio("<audio id='x'>") == (None, None)
    print("selftest ok")


if __name__ == "__main__":
    if "--test" in sys.argv: selftest(); sys.exit()
    if "--setup" in sys.argv: setup(); sys.exit()
    VIDEOS.mkdir(exist_ok=True)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:  # already running: just show it
        webbrowser.open(f"http://localhost:{PORT}"); sys.exit()
    log("started")
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    if "--quiet" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")
    srv.serve_forever()
