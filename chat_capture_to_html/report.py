"""Write the chat as one self-contained HTML file.

Screenshots are linked, not embedded, so the report never carries a modified copy
of the evidence. Links are relative to the HTML file when possible.
"""

from __future__ import annotations

import base64
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .capture import HASH_OK
from .pipeline import ChatResult

# The logo as the report's tab icon, inline so the report stays one file.
_LOGO = Path(__file__).with_name("logo.svg")
FAVICON = ("data:image/svg+xml;base64," + base64.b64encode(_LOGO.read_bytes()).decode("ascii")) if _LOGO.exists() else ""

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


def _views(m, shots, info, hrefs, e) -> str:
    """The message as it looks in the screenshot: the original PNG, shown through
    a window the size of the bubble. No image is cut out or saved; the page
    only positions the linked file."""
    out = []
    for v in m.views:
        x0, y0, x1, y1 = v.box
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        iw = info[v.shot].width or w
        # screen pixels per point: 3 on most phones, 2 on small phones and iPads
        dpr = 2 if iw <= 900 or iw > 1400 else 3
        s = shots[v.shot]
        alt = f"{'Message' if m.kind == 'bubble' else 'Line'} as shown in screenshot #{v.shot + 1}"
        out.append(
            f'<a class="view" href="{e(hrefs[v.shot])}" data-shot="{v.shot}" data-box="{x0},{y0},{x1},{y1}" '
            f'title="{e(s.path.name)}&#10;SHA-256 {s.sha256}" '
            f'style="width:{w / dpr:.0f}px;aspect-ratio:{w}/{h}">'
            f'<img src="{e(hrefs[v.shot])}" alt="{e(alt)}" loading="lazy" '
            f'style="width:{iw / w * 100:.4f}%;left:{-x0 / w * 100:.4f}%;top:{-y0 / h * 100:.4f}%"></a>')
    return "".join(out)


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
        text = "<br>".join(e(l) for l in m.lines) if m.lines else _views(m, shots, res.shots, hrefs, e)
        conf = f" · OCR confidence {m.conf:.0f}%" if res.text_read else ""
        lowc = " low" if m.conf < 60 else ""
        hash_line = (f'<span class="hash{" bad" if ps.status != HASH_OK else ""}">'
                     f'{e(ps.path.name)} · SHA-256 <code>{ps.sha256}</code> · {STATUS_TEXT[ps.status]}</span>')
        if m.kind == "meta":
            rows.append(f'<div class="meta" id="m{n}"><span class="t">{text}</span>'
                        f'<span class="refs">{src_links}</span></div>')
            continue
        swatch = f'<span class="sw" style="background:{_hex(m.color)}" title="bubble colour {_hex(m.color)}"></span>' if m.color else ""
        who = m.sender_name or sender[m.side]
        side_note = ""
        if m.side != "center":
            named = ", name read from screenshot" if m.sender_name else ""
            side_note = f' <span class="side">({m.side}{named})</span>'
        rows.append(
            f'<div class="msg {m.side}{" warn" if bad else ""}" id="m{n}">'
            f'<div class="who">{swatch}{e(who)}{side_note}</div>'
            f'<div class="bubble{"" if m.lines else " image"}" style="--bc:{_hex(m.color) if m.color else "transparent"}">{text}</div>'
            f'<div class="foot"><a class="n" href="#m{n}">#{n}</a>'
            f'{f" · {e(m.time)}" if m.time else ""}<span class="conf{lowc}">{conf}</span> · '
            f'screenshots {src_links}</div>'
            f'<div class="foot">{hash_line}</div></div>')

    n_ok = sum(1 for s in shots if s.status == HASH_OK)
    shot_rows = []
    for i, (s, info) in enumerate(zip(shots, res.shots)):
        pl = info.placement
        where = "" if pl is None else (f"segment {pl.segment + 1}, y={pl.y}" +
                                       (f", matched #{pl.matched_with + 1} (diff {pl.score:.3f})" if pl.matched_with is not None
                                        else ", start of segment" + (f"; previous shot: {pl.note}" if pl.note else "")))
        shot_rows.append(
            f'<tr class="{"" if s.status == HASH_OK else "bad"}"><td>#{i + 1}</td>'
            f'<td><a href="{e(hrefs[i])}" data-shot="{i}">{e(s.path.name)}</a></td>'
            f'<td><code>{s.sha256}</code></td><td><code>{s.recorded_sha256 or "—"}</code>'
            f'<br><small>{" + ".join(x for x, p in ((".json", s.json_path), (".txt", s.txt_path)) if p) or "no hash file"}'
            f'{(" · " + e(s.device.udid)) if s.device and s.device.udid and len(cap.devices) > 1 else ""}</small></td>'
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
        warn_items.append(f"{len(cap.orphan_hash_files)} hash file(s) (.txt or .json) have no matching PNG.")
    warn_items += cap.device_warnings()
    if mixed:
        warn_items.append("A bubble colour appears on both sides; check sender attribution against the screenshots.")
    warns = "".join(f"<li>{e(w)}</li>" for w in warn_items)
    devs = cap.devices
    devices_html = "".join(
        f"<dt>Device</dt><dd>{e(d.label())}<br><code>UDID {e(u)}</code></dd>" for u, d in sorted(devs.items())
    ) if devs else "<dt>Device</dt><dd>not recorded (the capture has no .json files naming it)</dd>"

    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    utc = [s.captured_utc for s in shots if s.captured_utc]
    taken = [s.taken for s in shots if s.taken]
    if shots and len(utc) == len(shots):
        span = f"{min(utc):%Y-%m-%d %H:%M:%S} – {max(utc):%Y-%m-%d %H:%M:%S} UTC (from the .json files)"
    elif taken:
        span = f"{min(taken):%Y-%m-%d %H:%M:%S} – {max(taken):%Y-%m-%d %H:%M:%S} (from file names, device's local time)"
    else:
        span = "unknown"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(cap.chat)} — chat capture</title>
{f'<link rel="icon" type="image/svg+xml" href="{FAVICON}">' if FAVICON else ""}
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
.bubble.image{{padding:4px;white-space:normal;width:fit-content;max-width:100%}}
.view{{display:block;position:relative;overflow:hidden;border-radius:10px;background:var(--line);max-width:100%}}.view+.view{{margin-top:4px}}
.view img{{position:absolute;max-width:none;height:auto;display:block}}
.meta .view{{margin:0 auto}}
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
{devices_html}
<dt>Screenshots</dt><dd>{len(shots)} ({n_ok} hash-verified)</dd>
<dt>Captured</dt><dd>{e(span)}</dd>
<dt>Senders</dt><dd>right-hand bubbles = {e(me)}; left-hand bubbles = {e(them)}</dd>
<dt>OCR</dt><dd>{e(res.ocr_engine) + ", offline" if res.text_read else "off (--no-ocr): each message is shown as it appears in its screenshot"}</dd>
<dt>Generated</dt><dd>{e(generated)} by chat-capture-to-html {__version__}</dd>
</dl></div>
{f'<h2>Warnings</h2><div class="card warns"><ul>{warns}</ul></div>' if warns else ""}
<h2>Chat</h2>
<p class="foot">{"Text is machine-read (OCR) and can contain errors: the screenshot is the evidence." if res.text_read else "No text was machine-read: each message is the original screenshot, shown through a window around the bubble."} Click a <b>#n</b> screenshot link to see where the message is in that shot; hover it for the file name and SHA-256.{" Times shown were split off the end of each bubble." if res.bubble_times_stripped else ""}</p>
<div class="chat">
{chr(10).join(rows) if rows else "<p>No messages found.</p>"}
</div>
<h2>Bubble colours</h2><div class="card"><ul>{legend or "<li>none</li>"}</ul></div>
<h2>Screenshots and hashes</h2>
<p class="foot">“Computed” is the SHA-256 of the PNG file now; “Recorded” is the value UFADE wrote in the .txt file at capture time. Chat area used: rows {res.content_top}–{res.content_bottom}, columns {res.chat_left}–{res.chat_right}.</p>
<div class="tw"><table><thead><tr><th></th><th>File</th><th>Computed SHA-256</th><th>Recorded SHA-256 (from)</th><th>Check</th><th>Placement</th></tr></thead>
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


def render_index(items, out_path: Path) -> str:
    """One page listing every chat processed in a run, grouped by device UDID
    (from the captures' .json files), with links to each report."""
    e = html.escape
    groups: dict[str, list] = {}
    for cap, res, target in items:
        devs = cap.devices
        key = " + ".join(sorted(devs)) if devs else ""
        groups.setdefault(key, []).append((cap, res, target))
    sections = []
    for key in sorted(groups, key=lambda k: (k == "", k)):
        rows = []
        for cap, res, target in sorted(groups[key], key=lambda t: (t[0].app, t[0].chat)):
            n_ok = sum(1 for s in cap.shots if s.status == HASH_OK)
            gaps = len(res.segment_order) - 1
            warn = n_ok != len(cap.shots) or cap.orphan_hash_files or cap.device_warnings()
            rows.append(
                f'<tr class="{"bad" if warn else ""}"><td>{e(cap.app)}</td>'
                f'<td><a href="{e(_href(target, out_path.parent))}">{e(cap.chat)}</a></td>'
                f'<td>{len(cap.shots)}</td><td>{n_ok}</td>'
                f'<td>{sum(1 for m in res.messages if m.kind == "bubble")}</td><td>{gaps}</td></tr>')
        if key:
            first = next(iter(groups[key][0][0].devices.values()))
            title = f"{e(first.label())} <code>UDID {e(key)}</code>"
        else:
            title = "Device not recorded (captures without .json files)"
        sections.append(f"<h2>{title}</h2><div class=\"tw\"><table><thead><tr><th>App</th><th>Chat</th>"
                        f"<th>Screenshots</th><th>Verified</th><th>Messages</th><th>Gaps</th></tr></thead>"
                        f"<tbody>{''.join(rows)}</tbody></table></div>")
    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chat captures — overview</title>
{f'<link rel="icon" type="image/svg+xml" href="{FAVICON}">' if FAVICON else ""}
<style>
:root{{--bg:#f6f6f4;--fg:#1c1c1e;--mut:#6b6b70;--line:#dcdcdf;--bad:#b42318;--acc:#0a5bd8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececee;--mut:#9a9aa0;--line:#333338;--bad:#ff8a80;--acc:#7fb0ff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:16px}}h1{{font-size:22px}}h2{{font-size:17px;margin:24px 0 8px}}
a{{color:var(--acc)}}code{{font:12px ui-monospace,Consolas,monospace;word-break:break-all;color:var(--mut)}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left}}tr.bad td{{color:var(--bad)}}
p{{color:var(--mut);font-size:13px}}
</style></head><body><main>
<h1>Chat captures — overview</h1>
<p>{len(items)} chats, grouped by the device each capture names. Red rows have hash or device warnings: open the report for details.
Generated {e(generated)} by chat-capture-to-html {__version__}.</p>
{"".join(sections)}
</main></body></html>
"""
