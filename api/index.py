def get_llm_response(prompt: str, provider: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    model_map = {
        "gemini": "llama-3.3-70b-versatile",
        "groq":   "llama-3.1-8b-instant",
        "claude": "deepseek-r1-distill-llama-70b",
    }
    model = model_map.get(provider, "llama-3.3-70b-versatile")

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=8192,
    )
    return resp.choices[0].message.content.strip()
