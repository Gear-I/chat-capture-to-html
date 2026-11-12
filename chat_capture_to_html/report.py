"""Write the chat as one self-contained HTML file.

Screenshots are linked, not embedded, so the report never carries a modified copy
of the evidence. Links are relative to the HTML file when possible.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .capture import HASH_OK
from .pipeline import ChatResult

STATUS_TEXT = {"ok": "verified", "mismatch": "HASH MISMATCH", "missing": "no hash file", "unreadable": "hash file unreadable"}


def _href(target: Path, out_dir: Path) -> str:
    target = target.resolve()
    try:
        rel = os.path.relpath(target, out_dir.resolve())
        return rel.replace(os.sep, "/").replace(" ", "%20").replace("#", "%23")
    except ValueError:  # different drive on Windows
        return target.as_uri()


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(c)


def render(res: ChatResult, out_path: Path, me: str, them: str) -> str:
    cap = res.capture
    out_dir = out_path.parent
    e = html.escape
    shots = cap.shots
    hrefs = [_href(s.path, out_dir) for s in shots]
    sender = {"right": me, "left": them, "center": "System / date"}

    shot_json = [{"name": s.path.name, "href": hrefs[i], "sha": s.sha256, "status": s.status,
                  "w": res.shots[i].width, "h": res.shots[i].height} for i, s in enumerate(shots)]

    rows = []
    prev_seg = None
    for n, m in enumerate(res.messages, 1):
        if prev_seg is not None and m.segment != prev_seg:
            rows.append('<div class="gap" role="note">Screenshots do not overlap here — messages may be missing.</div>')
        prev_seg = m.segment
        primary = next((s for s in m.sources if s.shot in m.text_from), m.sources[0])
        ps = shots[primary.shot]
        bad = any(shots[i].status != HASH_OK for i in m.text_from)
        src_links = " ".join(
            f'<a href="{e(hrefs[s.shot])}" data-shot="{s.shot}" data-box="{",".join(map(str, s.box))}" '
            f'class="src{" bad" if shots[s.shot].status != HASH_OK else ""}" '
            f'title="{e(shots[s.shot].path.name)}&#10;SHA-256 {shots[s.shot].sha256}&#10;{STATUS_TEXT[shots[s.shot].status]}'
            f'{" — text read from this shot" if s.shot in m.text_from else ""}">#{s.shot + 1}</a>'
            for s in m.sources)
        text = "<br>".join(e(l) for l in m.lines)
        conf = f"OCR confidence {m.conf:.0f}%"
        lowc = " low" if m.conf < 60 else ""
        hash_line = (f'<span class="hash{" bad" if ps.status != HASH_OK else ""}">'
                     f'{e(ps.path.name)} · SHA-256 <code>{ps.sha256}</code> · {STATUS_TEXT[ps.status]}</span>')
        if m.kind == "meta":
            rows.append(f'<div class="meta" id="m{n}"><span class="t">{text}</span>'
                        f'<span class="refs">{src_links}</span></div>')
            continue
        swatch = f'<span class="sw" style="background:{_hex(m.color)}" title="bubble colour {_hex(m.color)}"></span>' if m.color else ""
        who = m.sender_name or sender[m.side]
        rows.append(
            f'<div class="msg {m.side}{" warn" if bad else ""}" id="m{n}">'
            f'<div class="who">{swatch}{e(who)}'
            f'{f" <span class=side>({m.side}{", name read from screenshot" if m.sender_name else ""})</span>" if m.side != "center" else ""}</div>'
            f'<div class="bubble" style="--bc:{_hex(m.color) if m.color else "transparent"}">{text}</div>'
            f'<div class="foot"><a class="n" href="#m{n}">#{n}</a>'
            f'{f" · {e(m.time)}" if m.time else ""} · <span class="conf{lowc}">{conf}</span> · '
            f'screenshots {src_links}</div>'
            f'<div class="foot">{hash_line}</div></div>')

    n_ok = sum(1 for s in shots if s.status == HASH_OK)
    shot_rows = []
    for i, (s, info) in enumerate(zip(shots, res.shots)):
        pl = info.placement
        where = "" if pl is None else (f"segment {pl.segment + 1}, y={pl.y}" +
                                       (f", matched #{pl.matched_with + 1} (diff {pl.score:.2f})" if pl.matched_with is not None else ", start of segment"))
        shot_rows.append(
            f'<tr class="{"" if s.status == HASH_OK else "bad"}"><td>#{i + 1}</td>'
            f'<td><a href="{e(hrefs[i])}" data-shot="{i}">{e(s.path.name)}</a></td>'
            f'<td><code>{s.sha256}</code></td><td><code>{s.recorded_sha256 or "—"}</code></td>'
            f'<td>{STATUS_TEXT[s.status]}{(" — " + e(s.note)) if s.note and s.status != HASH_OK else ""}</td>'
            f'<td>{e(info.skip_reason) if not info.used else e(where) + f"; {info.bubbles} bubbles"}</td></tr>')
    orphans = "".join(f"<li>{e(p.name)}</li>" for p in cap.orphan_hash_files)

    legend = "".join(
        f'<li><span class="sw" style="background:{_hex(g["color"])}"></span>{_hex(g["color"])} — '
        + ", ".join(f'{c}× {side} ({e(sender[side])})' for side, c in g["sides"].most_common()) + "</li>"
        for g in res.color_groups)
    mixed = [g for g in res.color_groups if len([s for s in g["sides"] if s != "center"]) > 1]

    warn_items = list(res.warnings)
    if n_ok != len(shots):
        warn_items.insert(0, f"{len(shots) - n_ok} of {len(shots)} screenshots failed hash verification; their messages are marked.")
    if cap.orphan_hash_files:
        warn_items.append(f"{len(cap.orphan_hash_files)} .txt hash file(s) have no matching PNG.")
    if mixed:
        warn_items.append("A bubble colour appears on both sides; check sender attribution against the screenshots.")
    warns = "".join(f"<li>{e(w)}</li>" for w in warn_items)

    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    taken = [s.taken for s in shots if s.taken]
    span = f"{min(taken):%Y-%m-%d %H:%M:%S} – {max(taken):%Y-%m-%d %H:%M:%S} (from file names)" if taken else "unknown"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(cap.chat)} — chat capture</title>
<style>
:root{{--bg:#f6f6f4;--fg:#1c1c1e;--mut:#6b6b70;--card:#fff;--line:#dcdcdf;--bad:#b42318;--badbg:#fdecea;--acc:#0a5bd8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececee;--mut:#9a9aa0;--card:#1f1f22;--line:#333338;--bad:#ff8a80;--badbg:#3a1c1a;--acc:#7fb0ff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:16px}}h1{{font-size:22px;margin:8px 0}}h2{{font-size:17px;margin:28px 0 8px}}
a{{color:var(--acc)}}code{{font:12px ui-monospace,Consolas,monospace;word-break:break-all}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
dl{{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0}}dt{{color:var(--mut)}}dd{{margin:0;min-width:0;overflow-wrap:anywhere}}
.warns{{background:var(--badbg);border-color:var(--bad);color:var(--fg)}}.warns li{{margin:2px 0}}
.chat{{display:flex;flex-direction:column;gap:12px;margin-top:12px}}
.msg{{max-width:80%;display:flex;flex-direction:column}}.msg.left{{align-self:flex-start}}.msg.right{{align-self:flex-end;align-items:flex-end}}.msg.center{{align-self:center;align-items:center}}
.who{{font-size:12px;color:var(--mut);display:flex;align-items:center;gap:6px}}.side{{opacity:.8}}
.bubble{{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--bc);border-radius:12px;padding:8px 12px;white-space:pre-wrap;overflow-wrap:anywhere;margin:3px 0}}
.right .bubble{{border-left-width:1px;border-right:5px solid var(--bc)}}
.foot{{font-size:12px;color:var(--mut);overflow-wrap:anywhere}}.right .foot{{text-align:right}}
.hash{{font-size:11px}}.hash code{{font-size:11px}}.bad,.bad a,.hash.bad{{color:var(--bad);font-weight:600}}
.warn .bubble{{outline:2px solid var(--bad)}}
.meta{{align-self:center;text-align:center;font-size:13px;color:var(--mut);max-width:90%}}.meta .refs{{margin-left:6px;font-size:11px}}
.gap{{align-self:stretch;text-align:center;font-size:13px;color:var(--bad);border-top:2px dashed var(--bad);padding-top:6px}}
.sw{{display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid var(--line);vertical-align:-2px;margin-right:4px}}
.conf.low{{color:var(--bad)}}.src{{margin-right:2px}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;font-size:12px;width:100%}}td,th{{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}}
tr.bad td{{color:var(--bad)}}
#lb{{position:fixed;inset:0;background:rgba(0,0,0,.8);display:none;align-items:center;justify-content:center;z-index:9;padding:16px}}
#lb.on{{display:flex}}#lb figure{{margin:0;max-height:100%;display:flex;flex-direction:column;align-items:center}}
#lb .wrap{{position:relative;max-height:calc(100vh - 90px)}}#lb img{{max-height:calc(100vh - 90px);max-width:100%;display:block}}
#lb .box{{position:absolute;border:3px solid #ff3b30;box-shadow:0 0 0 9999px rgba(0,0,0,.25);pointer-events:none}}
#lb figcaption{{color:#fff;font-size:12px;margin-top:6px;text-align:center;overflow-wrap:anywhere;max-width:90vw}}
#lb button{{position:absolute;top:10px;right:14px;font-size:22px;background:none;color:#fff;border:0;cursor:pointer}}
@media (max-width:600px){{.msg{{max-width:95%}}}}
</style></head><body><main>
<h1>{e(cap.chat)}</h1>
<div class="card"><dl>
<dt>App</dt><dd>{e(cap.app)}</dd>
<dt>Chat (named by examiner)</dt><dd>{e(cap.chat)}</dd>
{f"<dt>Header text (OCR)</dt><dd>{e(res.header_text)}</dd>" if res.header_text else ""}
<dt>Capture folder</dt><dd><code>{e(str(cap.folder.resolve()))}</code></dd>
<dt>Screenshots</dt><dd>{len(shots)} ({n_ok} hash-verified)</dd>
<dt>Captured</dt><dd>{e(span)}</dd>
<dt>Senders</dt><dd>right-hand bubbles = {e(me)}; left-hand bubbles = {e(them)}</dd>
<dt>OCR</dt><dd>{e(res.ocr_engine)}, offline</dd>
<dt>Generated</dt><dd>{e(generated)} by chat-capture-to-html {__version__}</dd>
</dl></div>
{f'<h2>Warnings</h2><div class="card warns"><ul>{warns}</ul></div>' if warns else ""}
<h2>Chat</h2>
<p class="foot">Text is machine-read (OCR) and can contain errors: the screenshot is the evidence. Click a <b>#n</b> screenshot link to see where the message is in that shot; hover it for the file name and SHA-256.{" Times shown were split off the end of each bubble." if res.bubble_times_stripped else ""}</p>
<div class="chat">
{chr(10).join(rows) if rows else "<p>No messages found.</p>"}
</div>
<h2>Bubble colours</h2><div class="card"><ul>{legend or "<li>none</li>"}</ul></div>
<h2>Screenshots and hashes</h2>
<p class="foot">“Computed” is the SHA-256 of the PNG file now; “Recorded” is the value UFADE wrote in the .txt file at capture time. Content band used for matching: rows {res.content_top}–{res.content_bottom}.</p>
<div class="tw"><table><thead><tr><th></th><th>File</th><th>Computed SHA-256</th><th>Recorded SHA-256</th><th>Check</th><th>Placement</th></tr></thead>
<tbody>{"".join(shot_rows)}</tbody></table></div>
{f'<h2>Hash files without a screenshot</h2><ul>{orphans}</ul>' if orphans else ""}
</main>
<div id="lb" role="dialog" aria-modal="true"><button aria-label="Close">×</button><figure><div class="wrap"><img alt=""><div class="box"></div></div><figcaption></figcaption></figure></div>
<script>
const SHOTS={json.dumps(shot_json)};
const lb=document.getElementById('lb'),img=lb.querySelector('img'),box=lb.querySelector('.box'),cap=lb.querySelector('figcaption');
function show(i,b){{const s=SHOTS[i];img.src=s.href;cap.innerHTML='';
const a=document.createElement('a');a.href=s.href;a.textContent=s.name;a.style.color='#fff';a.target='_blank';
cap.append(a,document.createTextNode(' · SHA-256 '+s.sha+' · '+({{ok:'verified',mismatch:'HASH MISMATCH',missing:'no hash file',unreadable:'hash file unreadable'}})[s.status]));
if(b){{const[x0,y0,x1,y1]=b;Object.assign(box.style,{{display:'block',left:100*x0/s.w+'%',top:100*y0/s.h+'%',width:100*(x1-x0)/s.w+'%',height:100*(y1-y0)/s.h+'%'}});}}else box.style.display='none';
lb.classList.add('on');}}
document.addEventListener('click',ev=>{{const a=ev.target.closest('a[data-shot]');if(!a||ev.ctrlKey||ev.metaKey||ev.shiftKey)return;ev.preventDefault();
show(+a.dataset.shot,a.dataset.box?a.dataset.box.split(',').map(Number):null);}});
lb.addEventListener('click',ev=>{{if(ev.target===lb||ev.target.tagName==='BUTTON')lb.classList.remove('on');}});
document.addEventListener('keydown',ev=>{{if(ev.key==='Escape')lb.classList.remove('on');}});
</script></body></html>
"""
