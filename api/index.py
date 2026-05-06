from __future__ import annotations
import os, json, re
from http.server import BaseHTTPRequestHandler


def get_llm_response(prompt: str, provider: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    model_map = {
        "gemini": "llama-3.3-70b-versatile",
        "groq":   "llama-3.1-8b-instant",
        "claude": "qwen-qwq-32b",  
    }
    model = model_map.get(provider, "llama-3.3-70b-versatile")

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=8192,
    )
    return resp.choices[0].message.content.strip()


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
    # Node 1: Planner
    plan_prompt = f"""Generate 8 diverse and specific search queries to thoroughly research: "{topic}"
Vary the angles: include overview, recent developments, applications, challenges, and expert opinions.
Return ONLY a valid JSON array of strings. No explanation, no extra text."""

    raw = get_llm_response(plan_prompt, provider)
    m = re.search(r"\[.*?\]", raw, re.DOTALL)
    queries = json.loads(m.group()) if m else [f"{topic} overview", f"{topic} latest 2025", f"what is {topic}"]

    # Search + Scrape
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

    # Node 2: Writer
    write_prompt = f"""You are a professional research analyst. Write a comprehensive research report on: {topic}

SOURCES (use ONLY these for citations — do not use outside knowledge for citation numbers):
{src_text}

Requirements:
- 1500+ words, professional tone
- Use markdown headers (## for sections)
- Only cite a source [N] if that source directly supports the claim
- If a fact has no matching source, write it without a citation number
- Do not reuse the same citation number repeatedly for unrelated facts
- Cover: Introduction, Key Concepts, Current State, Applications, Challenges, Future Outlook
- End with ## References listing all used source URLs"""

    report = get_llm_response(write_prompt, provider)

    # Node 3: Reviewer
    review_prompt = f"""You are a strict fact-checker and editor reviewing this research report on: {topic}

REPORT TO REVIEW:
{report}

AVAILABLE SOURCES (the only valid citations):
{src_text}

Your tasks:
1. Check every citation [N] — verify it matches the content of that source
2. Remove or fix any citation [N] that does not match its source content
3. Remove any factual claim that cannot be supported by the provided sources
4. Fix any repeated use of the same citation for unrelated claims
5. Do NOT add new facts or new information not in the sources
6. Keep the full report structure and length intact
7. Return ONLY the corrected full report in the same markdown format — no commentary"""

    report = get_llm_response(review_prompt, provider)

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
