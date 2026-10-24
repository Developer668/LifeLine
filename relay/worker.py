"""NeuroLoop-style outbound MoLab worker, no inbound GPU port required.

Start after serving MiniCPM-o 4.5 via vLLM-Omni on the SAME MoLab machine.
It polls the persistent RELAY API and uses separate HTTP requests for inference.
"""
import concurrent.futures
import os
import time
import httpx

API = os.getenv("RELAY_API_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("RELAY_WORKER_TOKEN", "")
MODEL = os.getenv("MODEL_NAME", "openbmb/MiniCPM-o-4_5")
MODEL_BASE = os.getenv("MODEL_BASE_URL", "").rstrip("/")
MODEL_TOKEN = os.getenv("MODEL_BEARER_TOKEN", "")
MAX_WORKERS = max(1, min(8, int(os.getenv("RELAY_WORKER_CONCURRENCY", "2"))))

POLICY = ("You are RELAY, a prototype informational and trusted-contact coordination assistant, "
          "not an emergency service. Be concise, evidence-based, never diagnose, promise rescue, "
          "or assume a place is safe. If life-threatening risk is described, tell users to contact "
          "their local emergency number immediately. Never invent alerts or source links. "
          "Keep responses below 65 words. Do not perform tool calls.")


def infer(payload):
    text = str(payload.get("text", ""))[:2000]
    context = str(payload.get("context", ""))[:3000]
    if not MODEL_BASE:
        return ("RELAY simulation: I received your request. " +
                ("Official information: " + context[:220] if context else
                 "Please share your city or ZIP code if you want an official weather alert lookup. "
                 "If someone is in immediate danger, call local emergency services directly."))
    headers = {"Authorization": "Bearer " + MODEL_TOKEN} if MODEL_TOKEN else {}
    content = [{"type": "text", "text": text + ("\nVerified data:\n" + context if context else "")}]
    if payload.get("image_base64"):
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + payload["image_base64"]}})
    response = httpx.post(MODEL_BASE + "/v1/chat/completions", headers=headers, json={
        "model": MODEL,
        "messages": [{"role": "system", "content": POLICY},
                     {"role": "user", "content": content}],
        "max_tokens": 140, "temperature": 0.1,
        "chat_template_kwargs": {"enable_thinking": False}
    }, timeout=55)
    response.raise_for_status()
    ans = response.json()["choices"][0]["message"]["content"]
    if not isinstance(ans, str) or not ans.strip():
        raise ValueError("No model text response")
    return ans.strip()[:900]


def fetch_official_context(text):
    import re
    match = re.search(r"\b(\d{5})\b", text)
    if not match:
        return ""
    zip_code = match.group(1)
    try:
        with httpx.Client(timeout=4, follow_redirects=False) as client:
            loc = client.get("https://api.zippopotam.us/us/" + zip_code)
            loc.raise_for_status()
            p = loc.json()["places"][0]
            lat, lon = float(p["latitude"]), float(p["longitude"])
            resp = client.get("https://api.weather.gov/alerts/active", params={
                "point": f"{lat},{lon}"}, headers={
                "User-Agent": "RELAY-hackathon/0.1 (prototype; source NWS)",
                "Accept": "application/geo+json"})
            resp.raise_for_status()
            features = resp.json().get("features", [])[:3]
            if not features:
                return (f"Approximate ZIP-centroid weather lookup for {zip_code}: "
                        "no matching NWS active weather alerts returned. This does not confirm safety "
                        "or the absence of local evacuation orders.")
            alerts = []
            for feature in features:
                p = feature.get("properties", {})
                alerts.append({"event": p.get("event"), "severity": p.get("severity"),
                    "headline": p.get("headline"), "ends": p.get("ends"),
                    "sent": p.get("sent"), "source": p.get("@id") or feature.get("id")})
            return "NWS weather alerts for approximate ZIP-centroid " + zip_code + ": " + str(alerts)[:2100]
    except Exception:
        return "Official weather data could not be verified. Do not assume conditions are safe."


def run_one(job):
    payload = job["payload"]
    if job["kind"] != "message":
        return "Unsupported request type."
    context = fetch_official_context(str(payload.get("text", "")))
    payload = {**payload, "context": context}
    return infer(payload)


def process(client, job):
    try:
        text = run_one(job)
        client.post(f"/worker/jobs/{job['id']}/complete", json={
            "lease": job["lease"], "result": text}, timeout=15).raise_for_status()
    except Exception:
        # Never leak private message content or model internals into logs.
        try:
            client.post(f"/worker/jobs/{job['id']}/complete", json={
                "lease": job["lease"], "error": "Model processing unavailable."}, timeout=15).raise_for_status()
        except Exception:
            pass


def main():
    if not TOKEN:
        raise SystemExit("Set RELAY_WORKER_TOKEN first")
    headers = {"Authorization": "Bearer " + TOKEN}
    with httpx.Client(base_url=API, headers=headers, timeout=15) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            running = set()
            print(f"RELAY MoLab worker started (max_in_flight={MAX_WORKERS}; model={'configured' if MODEL_BASE else 'simulated'})")
            while True:
                running = {f for f in running if not f.done()}
                if len(running) >= MAX_WORKERS:
                    time.sleep(.25)
                    continue
                try:
                    res = client.post("/worker/claim")
                    res.raise_for_status()
                    job = res.json().get("job")
                    if job:
                        running.add(pool.submit(process, client, job))
                    else:
                        time.sleep(.5)
                except (httpx.HTTPError, ValueError):
                    time.sleep(2)


if __name__ == "__main__":
    main()
