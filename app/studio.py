"""Upload one video to YouTube Studio through the user's own signed-in Google Chrome (CDP port 9223).

  studio.chrome()                         open (or reuse) the dedicated Chrome window, profile in ROOT/chrome-profile
  studio.upload(mp4, meta, progress)      -> video id; meta = video.json (title, description, kind, schedule)
  python studio.py                        self-check of the pure helpers

Ported from the Mac uploader (macedit-ops/recut/money/upload.py). Real Chrome only: Google blocks sign-in in
Playwright's bundled browser. Stops on a sign-in/CAPTCHA page or the daily upload limit, never tries to get past.
"""
import os, re, subprocess, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "logs" / "shots"
CDP = "http://localhost:9223"
WIN = os.name == "nt"
DONE = ("Video scheduled", "Video saved", "Video published")


def norm(s):
    return re.sub(r"\s+", " ", s.replace("\u202f", " ")).strip()


def video_id_from(href):
    """youtu.be/ID, youtube.com/shorts/ID?x=1, watch?v=ID, studio.youtube.com/video/ID/edit -> ID ('' if none)."""
    m = re.search(r"[?&]v=([\w-]{6,})", href or "")
    if m: return m.group(1)
    parts = [p for p in re.sub(r"^\w+://[^/]+", "", (href or "").split("?")[0].split("#")[0]).split("/") if p]
    for k in ("video", "shorts"):
        if k in parts[:-1]: return parts[parts.index(k) + 1]
    return parts[-1] if len(parts) == 1 and re.fullmatch(r"[\w-]{6,}", parts[0]) else ""


def chrome_path():
    if WIN:
        bases = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        cands = [Path(b) / "Google" / "Chrome" / "Application" / "chrome.exe" for b in bases if b]
    else:
        cands = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    return next((str(c) for c in cands if c.is_file()), None)


def up():
    try:
        urllib.request.urlopen(f"{CDP}/json/version", timeout=2).read(); return True
    except Exception:
        return False


def chrome(show=True):
    """Start the dedicated Chrome (detached, survives this app) or reuse it; show=True also opens a Studio tab."""
    if up():
        if show:
            try: urllib.request.urlopen(urllib.request.Request(f"{CDP}/json/new?https://studio.youtube.com", method="PUT"), timeout=5)
            except Exception: pass
        return
    exe = chrome_path()
    if not exe:
        raise RuntimeError("Google Chrome is not installed")
    kw = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {"start_new_session": True}
    subprocess.Popen([exe, "--remote-debugging-port=9223", f"--user-data-dir={ROOT / 'chrome-profile'}", "--no-first-run",
                      "https://studio.youtube.com"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kw)
    for _ in range(60):
        if up(): return
        time.sleep(0.5)
    raise RuntimeError("Chrome started but its debugging port 9223 didn't open in 30 s; close all Chrome windows and try again")


def shot(pg, name):
    try:
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(SHOTS / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}.png"))
    except Exception:
        pass


def guard(pg):
    t = pg.locator("body").inner_text(timeout=20000)
    if "accounts.google.com" in pg.url or "captcha" in t.lower() or "Verify it's you" in t:
        shot(pg, "signin")
        raise RuntimeError("Please sign in to YouTube in the Chrome window, then press Approve again")
    if "Daily upload limit reached" in t:
        shot(pg, "limit")
        raise RuntimeError("YouTube's daily upload limit is reached; press Approve again tomorrow")


def fill(pg, box, text):
    box.click(); time.sleep(0.3)
    pg.keyboard.press("ControlOrMeta+A"); pg.keyboard.press("Backspace")
    pg.keyboard.insert_text(text); time.sleep(0.5)


def set_schedule(pg, scope, date, tm):
    scope.locator("#datepicker-trigger, ytcp-dropdown-trigger").first.click(); time.sleep(2)
    di = pg.locator("ytcp-date-picker input").first
    di.click(click_count=3); di.fill(date); di.press("Enter"); time.sleep(1.5)
    ti = scope.locator("#time-of-day-container input").first
    ti.click(); time.sleep(1.2)
    items = pg.locator("tp-yt-paper-listbox tp-yt-paper-item")
    idx = next((i for i in range(items.count()) if norm(items.nth(i).inner_text()) == tm), None)
    if idx is None:
        raise RuntimeError(f"Studio has no schedule time {tm!r} (use e.g. '7:00 PM', 15-minute steps)")
    items.nth(idx).scroll_into_view_if_needed(); items.nth(idx).click(); time.sleep(1)
    got = norm(scope.locator("#datepicker-trigger, ytcp-dropdown-trigger").first.inner_text()), norm(ti.input_value())
    if got != (date, tm):
        raise RuntimeError(f"schedule picker shows {got}, wanted {(date, tm)}")


def upload(path, meta, progress=print):
    title, desc = norm(meta["title"]), meta.get("description") or ""
    if not title: raise RuntimeError("video.json has no title")
    if len(title) > 100: raise RuntimeError(f"title is {len(title)} characters; YouTube allows 100")
    if not Path(path).is_file(): raise RuntimeError(f"{path} not found")
    sched = meta.get("schedule") or None
    from playwright.sync_api import sync_playwright
    chrome(show=False)
    with sync_playwright() as p:
        pg = p.chromium.connect_over_cdp(CDP).contexts[0].new_page()
        pg.set_default_timeout(30000)
        pg.bring_to_front()
        progress("Opening YouTube Studio")
        pg.goto("https://studio.youtube.com/", wait_until="domcontentloaded"); time.sleep(3); guard(pg)
        ch = re.search(r"/channel/(UC[\w-]+)", pg.url)  # Studio home redirects to the signed-in channel
        if not ch:
            shot(pg, "no_channel"); raise RuntimeError("Couldn't find the channel in YouTube Studio; sign in again in the Chrome window")
        upload_url = f"https://studio.youtube.com/channel/{ch.group(1)}/videos/upload?d=ud"
        for attempt in range(3):  # Studio sometimes renders a blank page; reload
            try:
                pg.goto(upload_url, wait_until="domcontentloaded")
                pg.wait_for_selector("input[type=file]", state="attached", timeout=40000); break
            except Exception:
                guard(pg)
                if attempt == 2:
                    shot(pg, "no_upload_page"); raise RuntimeError("Studio's upload page didn't open; try Approve again")
                time.sleep(10)
        time.sleep(2); guard(pg)
        progress("Sending the file")
        pg.locator("input[type=file]").first.set_input_files(str(path))
        try:
            vid = dialog(pg, title, desc, sched, progress)
        except BaseException as e:
            # 2026-10-28: a dialog whose schedule was already set can finish on its own and go out scheduled
            # (made a duplicate, XrcFFnWDe_w) - so the tab stays open and the user checks Studio first
            shot(pg, "failed")
            raise RuntimeError(f"{e} -- the Studio tab was left open so the draft isn't lost; it may still save itself. "
                               "Check Studio for a duplicate before pressing Approve again") from e
        pg.close()
    if not vid: raise RuntimeError("upload finished but no video id was found; check Studio")
    return vid


def dialog(pg, title, desc, sched, progress):
    dlg = pg.locator("ytcp-uploads-dialog")
    dlg.locator("#textbox").first.wait_for(timeout=90000); time.sleep(4); guard(pg)
    progress("Filling in title and description")
    fill(pg, dlg.locator("#textbox").nth(0), title)
    fill(pg, dlg.locator("#textbox").nth(1), desc)
    dlg.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').click(); time.sleep(1)
    for _ in range(3):
        dlg.locator("#next-button").click(); time.sleep(2)
    rv = pg.locator("ytcp-uploads-review")
    if sched:
        progress(f"Scheduling for {sched['date']} {sched['time']}")
        rv.locator("#second-container-expand-button").click(); time.sleep(2)
        set_schedule(pg, rv, sched["date"], sched["time"])
    else:
        rv.locator('tp-yt-paper-radio-button[name="PRIVATE"]').click(); time.sleep(1)
    shot(pg, "1_details")
    return finish(pg, dlg, progress)


def finish(pg, dlg, progress):
    """Wait for upload + checks, then Done/Schedule, then the confirmation."""
    # wait for upload + Studio's copyright checks before scheduling; scheduling earlier pops a
    # "still checking" warning, and a dialog left open loses the draft when the tab closes.
    # 2026-10-28: ytcp-video-upload-progress no longer renders, and on a slow upload the video link reads
    # "Creating link..." for minutes - so read the dialog's own text, and the link only once the checks are done.
    end = time.time() + 3 * 3600  # long-form files are 1-2 GB
    while True:
        prog = norm(dlg.first.inner_text())
        if "Checks complete" in prog or "No issues found" in prog:
            break
        if "Daily upload limit reached" in prog: guard(pg)
        m = re.search(r"(Uploading|Processing|Checking)[^.!]{0,60}", prog)
        progress(m.group(0).strip() if m else "Waiting for YouTube's checks")
        if time.time() > end:
            if re.search(r"Uploading \d+%", prog):
                raise RuntimeError(f"upload still not finished after 3 h ({m.group(0)})")
            break  # uploaded, checks just slow: the prechecks warning below handles it
        time.sleep(5)
    progress("Checks complete, saving")
    link = dlg.locator("a.style-scope.ytcp-video-info, ytcp-video-info a").first.get_attribute("href") or ""
    vid = video_id_from(link)
    shot(pg, "2_before_done")
    dlg.locator("#done-button").click()
    # Studio then shows a share dialog ("Video scheduled", drawn outside body.innerText) or a still-processing one
    done = [pg.get_by_text(t, exact=True).first for t in DONE]
    for _ in range(40):
        if any(d.is_visible() for d in done):
            break
        warn = pg.locator("ytcp-prechecks-warning-dialog").first
        if warn.is_visible():  # "still checking" warning: confirm saving anyway
            shot(pg, "warning")
            warn.locator("#primary-action-button, ytcp-button#secondary-action-button").first.click()
        got = pg.get_by_role("button", name="Got it")
        if got.count() and got.first.is_visible():
            # "We're still checking your content" notice (slow checks): Got it saves and closes the
            # dialog with no share dialog
            shot(pg, "warning")
            got.first.click(); time.sleep(3)
        if not dlg.first.is_visible() and not any(d.is_visible() for d in done):
            time.sleep(3)
            if not any(d.is_visible() for d in done):
                shot(pg, "3_saved"); return vid
        time.sleep(3)
    else:
        shot(pg, "stuck")
        raise RuntimeError(f"no confirmation from Studio after saving (see {SHOTS})")
    time.sleep(2); guard(pg)
    shot(pg, "3_saved")
    href = pg.locator("a[href*='youtube.com/shorts/'], a[href*='youtu.be/']").last.get_attribute("href") or ""
    vid = video_id_from(href) or vid
    close = pg.get_by_role("button", name="Close", exact=True)
    if close.count(): close.last.click(); time.sleep(1)
    return vid


if __name__ == "__main__":
    assert video_id_from("https://youtu.be/AbC_12-xyz9") == "AbC_12-xyz9"
    assert video_id_from("https://youtube.com/shorts/AbC_12-xyz9?x=1") == "AbC_12-xyz9"
    assert video_id_from("https://www.youtube.com/shorts/AbC_12-xyz9/") == "AbC_12-xyz9"
    assert video_id_from("https://studio.youtube.com/video/AbC_12-xyz9/edit") == "AbC_12-xyz9"
    assert video_id_from("https://www.youtube.com/watch?v=AbC_12-xyz9&t=3") == "AbC_12-xyz9"
    assert video_id_from("") == video_id_from(None) == video_id_from("https://studio.youtube.com/") == ""
    assert norm(" a  b\n") == "a b"
    c = chrome_path(); assert c is None or isinstance(c, str), c
    import playwright.sync_api  # noqa: F401  (the dependency upload() needs)
    print("studio selftest ok")
