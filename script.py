
import os, shutil

base = "/home/user/vercel_project"
api_dir = f"{base}/api"
os.makedirs(api_dir, exist_ok=True)

# vercel.json
open(f"{base}/vercel.json","w").write('''{
  "version": 2,
  "builds": [{ "src": "api/index.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "api/index.py" }]
}
''')

# requirements.txt
open(f"{base}/requirements.txt","w").write("""beautifulsoup4
requests
trafilatura
typing_extensions
python-dotenv
httpx
google-generativeai
groq
""")

# api/index.py
open(f"{api_dir}/index.py","w").write(r'''from __future__ import annotations
import os, json, re
from http.server import BaseHTTPRequestHandler


def get_llm_response(prompt: str, provider: str) -> str:
    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        model = genai.GenerativeModel("gemini-2.0-flash")
        return model.generate_content(prompt).text.strip()
    elif provider == "groq":
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
        )
        return resp.choices[0].message.content.strip()
    elif provider == "claude":
        import httpx
        headers = {
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "anthropic/claude-sonnet-4-5",
            "messages": [{"role": "user", "content": prompt}],
        }
        r = httpx.post("https://openrouter.ai/api/v1/chat/completions", json=body, headers=headers, timeout=60)
        return r.json()["choices"][0]["message"]["content"].strip()
    return "No provider configured."


def ddg_search(query: str, k: int = 6):
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import parse_qs, unquote
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for res in soup.select("div.result")[:k*2]:
            a = res.select_one(".result__a")
            snip = res.select_one(".result__snippet")
            if not a: continue
            link = a.get("href", "")
            if "duckduckgo.com" in link:
                qs2 = parse_qs(link.split("?",1)[-1])
                link = unquote(qs2.get("uddg", [""])[0])
            if not link or "duckduckgo" in link: continue
            results.append({"url": link, "title": a.get_text(strip=True), "snippet": snip.get_text(strip=True) if snip else ""})
            if len(results) >= k: break
        return results
    except: return []


def fetch_page(url: str):
    import requests, trafilatura
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        content = trafilatura.extract(r.text, favor_recall=True) or ""
        words = content.split()
        if len(words) < 80: return None
        return {"url": url, "title": url, "content": " ".join(words[:4000]), "word_count": min(len(words), 4000)}
    except: return None


def run_research(topic: str, provider: str) -> dict:
    plan_prompt = f"""Generate 8 diverse search queries to research: "{topic}"\nReturn ONLY a JSON array of strings."""
    raw = get_llm_response(plan_prompt, provider)
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    queries = json.loads(m.group()) if m else [f"{topic} overview", f"{topic} latest 2025", f"what is {topic}"]

    sources = []
    seen = set()
    for q in queries[:6]:
        for r in ddg_search(q, k=4):
            if r["url"] not in seen:
                seen.add(r["url"])
                page = fetch_page(r["url"])
                if page:
                    page["title"] = r["title"] or page["url"]
                    sources.append(page)
                    if len(sources) >= 10: break
        if len(sources) >= 10: break

    src_text = "\n---\n".join(
        f"[{i+1}] {s['title']}\n{s['url']}\n{s['content'][:2000]}"
        for i, s in enumerate(sources)
    )
    write_prompt = f"""Write a comprehensive research report on: {topic}

SOURCES:
{src_text}

Requirements:
- 1500+ words, professional tone
- Use markdown headers (## for sections)
- Cite sources as [1], [2] etc after every fact
- End with ## References listing all source URLs
- Cover: introduction, key concepts, current state, applications, challenges, future outlook"""

    report = get_llm_response(write_prompt, provider)
    return {"report": report, "sources": sources, "word_count": len(report.split())}


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path in ("/", ""):
            self._serve_html()
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/api/research":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            topic = body.get("topic", "").strip()
            provider = body.get("provider", "gemini")
            if not topic:
                self._json({"error": "topic is required"}, 400); return
            try:
                result = run_research(topic, provider)
                self._json(result)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self.send_response(404); self.end_headers()

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors(); self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        try:
            p = os.path.join(os.path.dirname(__file__), "..", "index.html")
            with open(p, "rb") as f: html = f.read()
        except: html = b"<h1>AI Research Agent</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers(); self.wfile.write(html)

    def log_message(self, *a): pass
''')

# index.html
open(f"{base}/index.html","w").write(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>AI Research Agent</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;font-family:'Inter',sans-serif}
body{background:linear-gradient(135deg,#0a0e1a 0%,#0f1528 50%,#0a0e1a 100%);min-height:100vh;color:#e2e8f0}
.layout{display:flex;min-height:100vh}
.sidebar{width:260px;background:linear-gradient(180deg,#0d1221,#111827);border-right:1px solid rgba(79,142,247,.1);padding:1.5rem 1rem;display:flex;flex-direction:column;gap:1rem;flex-shrink:0}
.sidebar-logo{font-size:1.4rem;font-weight:800;background:linear-gradient(135deg,#4F8EF7,#7C3AED);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sidebar-label{color:#8892b0;font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-top:1rem}
select{width:100%;background:rgba(15,23,42,.8);border:1px solid rgba(79,142,247,.25);border-radius:10px;color:#e2e8f0;padding:.6rem .8rem;font-size:.9rem;outline:none;cursor:pointer}
select:focus{border-color:#4F8EF7}
.sidebar-footer{margin-top:auto;color:#475569;font-size:.72rem;text-align:center}
.main{flex:1;padding:2rem;max-width:960px;margin:0 auto;width:100%}
.hero{text-align:center;margin-bottom:2rem}
.hero h1{background:linear-gradient(135deg,#4F8EF7,#7C3AED,#E040FB);-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-size:clamp(1.8rem,4vw,2.6rem);font-weight:800}
.hero p{color:#64748b;font-size:.95rem;margin-top:.4rem}
.card{background:rgba(15,23,42,.6);backdrop-filter:blur(20px);border:1px solid rgba(79,142,247,.15);border-radius:16px;padding:1.5rem;margin-bottom:1.5rem}
.input-label{color:#94a3b8;font-size:.85rem;font-weight:500;margin-bottom:.5rem}
.topic-input{width:100%;background:rgba(15,23,42,.9);border:1.5px solid rgba(79,142,247,.4);border-radius:12px;color:#e2e8f0;padding:.85rem 1rem;font-size:1rem;outline:none;transition:border-color .2s}
.topic-input:focus{border-color:#4F8EF7;box-shadow:0 0 20px rgba(79,142,247,.15)}
.topic-input::placeholder{color:#475569}
.btn-generate{width:100%;max-width:400px;display:block;margin:1rem auto 0;background:linear-gradient(135deg,#4F8EF7,#7C3AED);color:#fff;border:none;border-radius:12px;padding:.8rem 2rem;font-size:1rem;font-weight:600;cursor:pointer;transition:all .3s;box-shadow:0 4px 15px rgba(79,142,247,.25)}
.btn-generate:hover{box-shadow:0 6px 25px rgba(79,142,247,.45);transform:translateY(-1px)}
.btn-generate:disabled{opacity:.5;cursor:not-allowed;transform:none}
.badge{display:inline-block;padding:3px 10px;border-radius:6px;font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:.8rem}
.badge-gemini{background:rgba(66,133,244,.15);color:#4285f4}
.badge-groq{background:rgba(244,107,38,.15);color:#f46b26}
.badge-claude{background:rgba(204,146,63,.15);color:#cc923f}
.progress-wrap{display:flex;flex-direction:column;gap:.4rem;margin:.8rem 0}
.step{display:flex;align-items:center;gap:10px;padding:9px 14px;border-radius:10px;font-size:.88rem;font-weight:500;transition:all .3s}
.step.done{background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);color:#10b981}
.step.active{background:rgba(79,142,247,.1);border:1px solid rgba(79,142,247,.3);color:#4F8EF7;animation:pulse 2s infinite}
.step.pending{background:rgba(100,116,139,.05);border:1px solid rgba(100,116,139,.1);color:#475569}
@keyframes pulse{0%,100%{border-color:rgba(79,142,247,.3)}50%{border-color:rgba(79,142,247,.7)}}
.stats{display:flex;gap:1rem;margin:1rem 0;flex-wrap:wrap}
.stat{flex:1;min-width:100px;background:rgba(79,142,247,.06);border:1px solid rgba(79,142,247,.12);border-radius:12px;padding:1rem;text-align:center}
.stat-val{font-size:1.7rem;font-weight:700;background:linear-gradient(135deg,#4F8EF7,#7C3AED);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat-lbl{font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-top:4px}
.report-wrap{background:rgba(15,23,42,.5);border:1px solid rgba(79,142,247,.1);border-radius:16px;padding:2rem;line-height:1.75;color:#cbd5e1}
.report-wrap h1{color:#f1f5f9;font-size:1.7rem;border-bottom:2px solid rgba(79,142,247,.2);padding-bottom:.5rem;margin-bottom:1rem}
.report-wrap h2{color:#e2e8f0;font-size:1.2rem;margin-top:1.8rem;margin-bottom:.5rem}
.report-wrap h3{color:#cbd5e1;font-size:1rem;margin-top:1.2rem;margin-bottom:.4rem}
.report-wrap p{margin-bottom:.8rem}
.report-wrap a{color:#4F8EF7;text-decoration:none}
.report-wrap ul,.report-wrap ol{padding-left:1.5rem;margin-bottom:.8rem}
.report-wrap li{margin-bottom:.3rem}
.btn-dl{display:inline-block;background:rgba(16,185,129,.1);color:#10b981;border:1px solid rgba(16,185,129,.3);border-radius:10px;padding:.6rem 1.4rem;font-size:.9rem;font-weight:600;cursor:pointer;margin-top:1rem;transition:all .2s}
.btn-dl:hover{background:rgba(16,185,129,.2)}
.error-box{background:rgba(161,44,123,.08);border:1px solid rgba(161,44,123,.3);border-radius:12px;padding:1rem 1.2rem;color:#f87171;font-size:.88rem}
@media(max-width:640px){.sidebar{display:none}.main{padding:1rem}}
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-logo">🔬 Research Agent</div>
    <div>
      <div class="sidebar-label">LLM Provider</div>
      <select id="provider" onchange="updateBadge()">
        <option value="gemini">🔵 Gemini 2.0 Flash</option>
        <option value="groq">🟠 Groq (Llama 3.3 70B)</option>
        <option value="claude">🟤 Claude Sonnet</option>
      </select>
    </div>
    <div class="sidebar-footer">Powered by Gemini · Groq · Claude<br/>100% Free &amp; Open Source</div>
  </aside>
  <main class="main">
    <div class="hero">
      <h1>AI Research Agent</h1>
      <p>Autonomous deep research &amp; report generation — powered by Gemini, Groq &amp; Claude</p>
    </div>
    <div class="card">
      <span class="badge badge-gemini" id="provider-badge">🔵 GEMINI 2.0 FLASH</span>
      <div class="input-label">🔎 Research Topic</div>
      <input class="topic-input" id="topic" type="text" placeholder="e.g., Quantum Computing, CRISPR Gene Editing, Future of AI..."/>
      <button class="btn-generate" id="gen-btn" onclick="generateReport()">🚀 Generate Research Report</button>
    </div>
    <div class="card" id="progress-card" style="display:none">
      <div style="font-size:.95rem;font-weight:600;margin-bottom:.8rem">⚡ Research in Progress</div>
      <div class="progress-wrap" id="steps"></div>
    </div>
    <div id="result-area"></div>
  </main>
</div>
<script>
const STEPS=[{key:"plan",icon:"📋",label:"Planning research queries"},{key:"search",icon:"🔍",label:"Searching the web"},{key:"fetch",icon:"📄",label:"Fetching & extracting content"},{key:"write",icon:"✍️",label:"Writing report with citations"}];
const BADGES={gemini:{cls:"badge-gemini",txt:"🔵 GEMINI 2.0 FLASH"},groq:{cls:"badge-groq",txt:"🟠 GROQ LLAMA 3.3 70B"},claude:{cls:"badge-claude",txt:"🟤 CLAUDE SONNET"}};
function updateBadge(){const p=document.getElementById("provider").value,b=document.getElementById("provider-badge");b.className="badge "+BADGES[p].cls;b.textContent=BADGES[p].txt;}
function renderSteps(active){const wrap=document.getElementById("steps");wrap.innerHTML=STEPS.map(s=>{let cls="pending";if(s.key===active)cls="active";else if(STEPS.findIndex(x=>x.key===active)>STEPS.findIndex(x=>x.key===s.key))cls="done";return`<div class="step ${cls}"><span>${s.icon}</span><span>${s.label}${cls==="done"?" ✓":cls==="active"?"...":""}</span></div>`;}).join("");}
function renderAllDone(){document.getElementById("steps").innerHTML=STEPS.map(s=>`<div class="step done"><span>${s.icon}</span><span>${s.label} ✓</span></div>`).join("");}
function mdToHtml(md){return md.replace(/^### (.+)$/gm,"<h3>$1</h3>").replace(/^## (.+)$/gm,"<h2>$1</h2>").replace(/^# (.+)$/gm,"<h1>$1</h1>").replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>").replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank">$1</a>').replace(/^\s*[-*] (.+)$/gm,"<li>$1</li>").replace(/\n\n/g,"</p><p>").replace(/^(?!<[hul])/gm,"");}
async function generateReport(){
  const topic=document.getElementById("topic").value.trim();
  if(!topic)return;
  const provider=document.getElementById("provider").value,btn=document.getElementById("gen-btn");
  btn.disabled=true;btn.textContent="⏳ Generating...";
  document.getElementById("progress-card").style.display="block";
  document.getElementById("result-area").innerHTML="";
  let si=0;renderSteps(STEPS[0].key);
  const timer=setInterval(()=>{si=(si+1)%STEPS.length;renderSteps(STEPS[si].key);},3500);
  try{
    const res=await fetch("/api/research",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({topic,provider})});
    clearInterval(timer);
    const data=await res.json();
    if(!res.ok||data.error){renderAllDone();document.getElementById("result-area").innerHTML=`<div class="error-box">❌ ${data.error||"Unknown error"}</div>`;}
    else{
      renderAllDone();
      const wc=data.word_count||data.report.split(" ").length,sc=(data.sources||[]).length;
      document.getElementById("result-area").innerHTML=`<div class="stats"><div class="stat"><div class="stat-val">${sc}</div><div class="stat-lbl">Sources</div></div><div class="stat"><div class="stat-val">${wc.toLocaleString()}</div><div class="stat-lbl">Words</div></div><div class="stat"><div class="stat-val">${provider.charAt(0).toUpperCase()+provider.slice(1)}</div><div class="stat-lbl">Provider</div></div></div><div class="report-wrap" id="report-content">${mdToHtml(data.report)}</div><button class="btn-dl" onclick="downloadReport()">📥 Download Report (.md)</button>`;
      window._lastReport=data.report;window._lastTopic=topic;
    }
  }catch(e){clearInterval(timer);document.getElementById("result-area").innerHTML=`<div class="error-box">❌ Network error: ${e.message}</div>`;}
  btn.disabled=false;btn.textContent="🚀 Generate Research Report";
}
function downloadReport(){const blob=new Blob([window._lastReport||""],{type:"text/markdown"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=(window._lastTopic||"report").replace(/\s+/g,"_")+".md";a.click();}
document.getElementById("topic").addEventListener("keydown",e=>{if(e.key==="Enter")generateReport();});
</script>
</body>
</html>""")

shutil.make_archive("/home/user/vercel_project", "zip", "/home/user/vercel_project")
print("Done. Files:")
for root, dirs, files in os.walk("/home/user/vercel_project"):
    for f in files:
        print(" ", os.path.join(root,f).replace("/home/user/vercel_project/",""))
