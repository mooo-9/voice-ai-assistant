"""The morning review: every drafted application not yet sent, on one page,
none ticked. Mo ticks the ones to send and presses Send; the rest stay saved
here, letters and all, for him to send another day.
Served by the dashboard at /jobs; the batch itself comes from /api/jobs with
the dashboard token, so the drafts aren't readable without it."""
from pathlib import Path

from core.career import companies, pipeline, profile, programmes, referrals, store


def batch_json() -> dict:
    s = store.settings()
    live = s["live"] and profile.has_cv()
    return {
        "mode": "live" if live else "practice",
        "missing": [profile.ANSWER_KEYS[k] for k in profile.missing_answers()],
        "apps": [{
            "id": a["id"], "title": a["title"], "company": a.get("company") or "",
            "tier": a.get("tier", ""), "score": a.get("score", 0), "fit": a.get("fit", ""),
            "missing": a.get("missing", []), "channel": a["channel"], "url": a["url"],
            "location": a.get("location", ""), "to": a.get("hr_email", ""),
            "subject": a["draft"]["subject"], "body": a["draft"]["body"],
            "cv": Path(a["cv_path"]).name if a.get("cv_path") else "",
            "drafted": next((e["at"][:10] for e in reversed(a.get("events", []))
                             if e["status"] == "ready"), ""),
        } for a in pipeline.ready_batch()],
        "tiers": list(companies.TIER_RANK),
        "closing": [{"name": p["name"], "days": programmes.days_left(p), "url": p["url"]}
                    for p in programmes.closing_soon()],
        "referrals": [{
            "id": r["id"], "name": r["name"], "headline": r.get("headline", ""),
            "company": r["company"], "url": r["url"], "alumni": bool(r.get("alumni")),
            "connected": bool(r.get("connected")),
            "role": r.get("role", ""), "note": r["note"], "message": r["message"],
        } for r in referrals.to_send()],
        # Forms that stopped on a question: answered here, they're sent again.
        "waiting": [{"id": a["id"], "title": a["title"], "company": a.get("company") or "",
                     "question": pipeline._question(a)} for a in pipeline.blocked_questions()],
    }


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Job Applications</title>
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<style>
__FONTS__
__TOKENS__
 *{box-sizing:border-box}
 html,body{margin:0}
 body{background:var(--bg-void);color:var(--text-hi);font-family:var(--font-ui);
   -webkit-font-smoothing:antialiased;padding:22px 16px 110px;max-width:880px;margin:0 auto}
 .brand{font-size:13px;font-weight:600;letter-spacing:4px;color:var(--accent-ember)}
 .brand a{color:var(--text-low);text-decoration:none}
 h1{font-size:24px;font-weight:600;letter-spacing:-.3px;margin:12px 0 4px}
 .sub{color:var(--text-mid);font-size:13px}
 .banner{margin:14px 0;padding:10px 12px;border:1px solid var(--stroke-hairline);border-left:3px solid var(--sem-warn);
   border-radius:var(--r-2);font-size:13px;color:var(--text-mid);background:var(--surface-1)}
 .chips{display:flex;gap:var(--s-2);flex-wrap:wrap;margin:14px 0}
 .chip{background:var(--surface-0);border:1px solid var(--stroke-hairline);color:var(--text-mid);border-radius:var(--r-pill);
   padding:6px 14px;font-family:inherit;font-size:12px;cursor:pointer}
 .chip.on{color:var(--accent-ember);border-color:var(--accent-ember);background:var(--accent-wash)}
 .app{background:var(--surface-1);border:1px solid var(--stroke-hairline);border-radius:var(--r-3);
   padding:12px 14px;margin-bottom:10px;display:flex;gap:12px;align-items:flex-start}
 .app.off{opacity:.45}
 .app input{width:18px;height:18px;margin-top:3px;accent-color:var(--accent-ember);flex:0 0 auto}
 .app input.answer{width:100%;height:auto;margin:8px 0;padding:8px 10px;border-radius:8px;
  border:1px solid var(--stroke-hairline);background:var(--surface-0);color:var(--text-hi);font:inherit}
 .main{flex:1;min-width:0}
 .t{font-size:15px;font-weight:600;overflow-wrap:anywhere}
 .t a{color:inherit;text-decoration:none}
 .meta{color:var(--text-mid);font-size:12px;margin-top:2px;overflow-wrap:anywhere}
 .badge{font-family:var(--font-mono);font-size:10px;letter-spacing:1.5px;text-transform:uppercase;
   color:var(--accent-ember);border:1px solid var(--accent-ember);border-radius:var(--r-pill);padding:1px 7px;margin-left:6px}
 .score{font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:var(--text-hi)}
 .fit{font-size:13px;color:var(--text-mid);margin-top:6px}
 .gap{font-size:12px;color:var(--text-low);margin-top:3px}
 details{margin-top:8px}
 summary{cursor:pointer;color:var(--text-mid);font-size:12px}
 pre{white-space:pre-wrap;font-family:inherit;font-size:13px;color:var(--text-hi);background:var(--surface-0);
   border:1px solid var(--stroke-hairline);border-radius:var(--r-2);padding:10px;margin:8px 0 0}
 .bar{position:fixed;left:0;right:0;bottom:0;background:var(--surface-2);border-top:1px solid var(--stroke-hairline);
   padding:12px 16px;display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap}
 .go{color:var(--text-on-ember);background:var(--accent-ember);border:1px solid var(--accent-ember);border-radius:var(--r-pill);
   padding:10px 22px;font-family:inherit;font-size:14px;font-weight:600;cursor:pointer}
 .go:disabled{opacity:.5;cursor:default}
 .msg{font-size:13px;color:var(--text-mid)}
 .empty{color:var(--text-mid);font-size:14px;margin-top:30px;text-align:center}
 .banner.hot{border-left-color:var(--accent-ember)}
 .banner a{color:var(--accent-ember)}
 h2{font-size:17px;font-weight:600;margin:28px 0 4px}
 .row{display:flex;gap:var(--s-2);flex-wrap:wrap;margin-top:8px}
</style></head><body>
<div class="brand"><a href="/">EL FAGER</a> // JOBS</div>
<h1 id="head">Loading…</h1>
<div class="sub" id="sub"></div>
<div id="banners"></div>
<div class="chips" id="chips"></div>
<div id="waiting"></div>
<div id="list"></div>
<div id="refs"></div>
<div class="bar"><span class="msg" id="msg"></span>
 <button class="go" id="go" onclick="approve()" disabled>Send</button></div>
<script>
let data=null, filter='all';
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function token(){
 let t=localStorage.getItem('elf_token');
 if(!t){t=prompt('Dashboard token (data/settings.json → dashboard_token):');if(t)localStorage.setItem('elf_token',t);}
 return t||'';
}
async function load(){
 const r=await fetch('/api/jobs',{headers:{'Authorization':'Bearer '+token()}});
 if(r.status===401){localStorage.removeItem('elf_token');$('head').textContent='Wrong token — reload to try again';return;}
 data=await r.json(); data.apps.forEach(a=>a.on=false); render();
}
const CH={email:'email with CV',wuzzuf:'Wuzzuf apply',linkedin:'LinkedIn apply',site:'company form'};
function render(){
 const n=data.apps.length;
 $('head').textContent=n?`${n} applications ready`:'Nothing to review';
 $('sub').textContent=n?'Tick the ones to send, then press Send. The rest stay saved here for another day.':'';
 let b='';
 data.closing.forEach(p=>{b+=`<div class="banner hot"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a> closes in ${p.days} day${p.days===1?'':'s'}. Apply on their site.</div>`;});
 if(data.mode==='practice')b+='<div class="banner">Practice mode: approving marks these as practice runs. Nothing is sent until your CV is imported and you switch to live.</div>';
 if(data.missing.length)b+=`<div class="banner">Forms will ask for answers you haven’t given: ${esc(data.missing.join(', '))}. Tell El Fager, e.g. “my military status is exempted”.</div>`;
 $('banners').innerHTML=b;
 const counts={all:n,big4:data.apps.filter(a=>a.tier==='big4').length,top:data.apps.filter(a=>a.tier==='top').length};
 $('chips').innerHTML=n?[['all','All'],['big4','Big 4'],['top','Top companies']].map(([k,l])=>
   `<button class="chip ${filter===k?'on':''}" onclick="filter='${k}';render()">${l} · ${counts[k]}</button>`).join('')
   +'<button class="chip" onclick="tick(true)">Tick all</button><button class="chip" onclick="tick(false)">Untick all</button>':'';
 const shown=data.apps.filter(a=>filter==='all'||a.tier===filter);
 $('list').innerHTML=n?shown.map(a=>`<div class="app ${a.on?'':'off'}">
  <input type="checkbox" ${a.on?'checked':''} onchange="flip('${a.id}',this.checked)">
  <div class="main">
   <div class="t"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>${a.tier==='big4'?'<span class="badge">Big 4</span>':a.tier==='top'?'<span class="badge">Top</span>':''}</div>
   <div class="meta">${esc(a.company)}${a.location?' · '+esc(a.location):''} · <span class="score">${a.score}</span>/100 · ${esc(CH[a.channel]||a.channel)}${a.to?' → '+esc(a.to):''}${a.drafted?' · drafted '+esc(a.drafted):''}${a.cv?' · CV: '+esc(a.cv):''}</div>
   <div class="fit">${esc(a.fit)}</div>
   ${a.missing.length?`<div class="gap">They ask for: ${esc(a.missing.join('; '))}</div>`:''}
   <details><summary>Read the ${a.channel==='email'?'email':'cover letter'}</summary><pre>${esc(a.subject)}\n\n${esc(a.body)}</pre></details>
  </div></div>`).join(''):'<div class="empty">Nothing saved. Press RUN on Job hunt in the Command Center’s AUTOMATIONS; the batch is ready by morning.</div>';
 renderRefs();
 renderWaiting();
 const k=data.apps.filter(a=>a.on).length;
 $('go').disabled=!k; $('go').textContent=k?`Send ${k}`:'Send';
 $('msg').textContent=n?`${n-k} stay saved`:'';
}
function renderRefs(){
 const r=data.referrals;
 $('refs').innerHTML=r.length?`<h2>People who could refer you</h2>
  <div class="sub">Send these yourself on LinkedIn: connect with the note, then send the message once they accept. Your connections need only the message.</div>`+
  r.map(p=>`<div class="app"><div class="main">
   <div class="t"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a>${p.connected?'<span class="badge">Connection</span>':p.alumni?'<span class="badge">Alumni</span>':''}</div>
   <div class="meta">${esc(p.headline||p.company)} · ${esc(p.company)}${p.role?' · for '+esc(p.role):''}</div>
   ${p.connected?`<pre>${esc(p.message)}</pre>`:`<pre>${esc(p.note)}</pre>
   <details><summary>Message after they accept</summary><pre>${esc(p.message)}</pre></details>`}
   <div class="row">
    ${p.connected?'':`<button class="chip" onclick="copyText('${p.id}','note')">Copy note</button>`}
    <button class="chip" onclick="copyText('${p.id}','message')">Copy message</button>
    <button class="chip" onclick="markRef('${p.id}','sent')">Mark sent</button>
    <button class="chip" onclick="markRef('${p.id}','skipped')">Skip</button>
   </div></div></div>`).join(''):'';
}
function renderWaiting(){
 const w=data.waiting||[];
 $('waiting').innerHTML=w.length?`<h2>A form needs your answer</h2>
  <div class="sub">Answer once: every form after gets it too, and these are sent again.</div>`+
  w.map((q,i)=>`<div class="app"><div class="main">
   <div class="t">${esc(q.question)}</div>
   <div class="meta">${esc(q.title)} · ${esc(q.company)}</div>
   <input class="answer" id="ans${i}" placeholder="Your answer">
   <div class="row"><button class="chip" onclick="answer(${i})">Save and send again</button></div>
  </div></div>`).join(''):'';
}
async function answer(i){
 const q=data.waiting[i], a=$('ans'+i).value.trim();
 if(!a){$('msg').textContent='Type the answer first';return;}
 const r=await fetch('/api/jobs_answer',{method:'POST',
   headers:{'Content-Type':'application/json','Authorization':'Bearer '+token()},
   body:JSON.stringify({question:q.question,answer:a})});
 const j=await r.json(); $('msg').textContent=j.result||j.error||'';
 if(j.ok){data.waiting=data.waiting.filter(x=>x.question!==q.question);renderWaiting();}
}
async function copyText(id,field){
 const p=data.referrals.find(x=>x.id===id);
 try{await navigator.clipboard.writeText(p[field]);$('msg').textContent='Copied';}catch(e){$('msg').textContent='Copy failed — select the text instead';}
}
async function markRef(id,status){
 const r=await fetch('/api/referral_mark',{method:'POST',
   headers:{'Content-Type':'application/json','Authorization':'Bearer '+token()},body:JSON.stringify({id,status})});
 const j=await r.json(); $('msg').textContent=j.result||j.error||'';
 if(j.ok){data.referrals=data.referrals.filter(x=>x.id!==id);renderRefs();}
}
function flip(id,on){data.apps.find(a=>a.id===id).on=on;render();}
function tick(on){data.apps.filter(a=>filter==='all'||a.tier===filter).forEach(a=>a.on=on);render();}
async function approve(){
 $('go').disabled=true; $('msg').textContent='Sending…';
 const only=data.apps.filter(a=>a.on).map(a=>a.id);
 const r=await fetch('/api/jobs_approve',{method:'POST',
   headers:{'Content-Type':'application/json','Authorization':'Bearer '+token()},body:JSON.stringify({only})});
 const j=await r.json(); if(j.ok)data.apps=data.apps.filter(a=>!a.on); render(); $('msg').textContent=j.result||j.error||'Done';
}
load();
</script></body></html>"""
