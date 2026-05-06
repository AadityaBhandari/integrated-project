from __future__ import annotations
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
        data = r.json()
if "choices" in data:
    return data["choices"][0]["message"]["content"].strip()
elif "error" in data:
    raise Exception(f"OpenRouter error: {data['error']}")
else:
    raise Exception(f"Unexpected response: {data}")
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
