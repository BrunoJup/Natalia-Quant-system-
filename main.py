import os, json, math, requests, asyncio
from fastapi import FastAPI, Request
import uvicorn

# =========================
# ENV
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

app = FastAPI()

# =========================
# CLV STORAGE
# =========================
CLV_FILE = "clv.json"

def load_clv():
    try:
        return json.load(open(CLV_FILE))
    except:
        return {}

def save_clv(data):
    json.dump(data, open(CLV_FILE,"w"))

CLV_DB = load_clv()

# =========================
# MATH ENGINE
# =========================
def avg(x): return sum(x)/len(x) if x else 0

def poisson(l,k):
    return (math.exp(-l)*(l**k))/math.factorial(k)

def prob_over(line,l):
    return 1 - sum(poisson(l,k) for k in range(int(line)+1))

def ev(p,o): return (p*o)-1

# =========================
# LEAGUE MODEL
# =========================
def league(a,b):
    g = avg(a)+avg(b)
    if g > 3.3:
        return "eadriatic",1.25
    if g > 2.5:
        return "gt_league",1.1
    return "low",0.95

# =========================
# xG MODEL
# =========================
def xg(a_for,a_against,b_for,b_against,factor):
    return ((avg(a_for)*0.65 + avg(b_against)*0.35) +
            (avg(b_for)*0.65 + avg(a_against)*0.35)) * factor

# =========================
# OPENROUTER (ASYNC STYLE)
# =========================
async def parse_image(url):

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":"application/json"
    }

    prompt = """
Return ONLY JSON:
{
 "a_for":[1,2,3],
 "a_against":[1,1,2],
 "b_for":[2,2,1],
 "b_against":[1,2,2],
 "odds":{"over_2.5":1.8}
}
"""

    payload = {
        "model":"openai/gpt-4o-mini",
        "messages":[{
            "role":"user",
            "content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":url}}
            ]
        }]
    }

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )

        txt = r.json()["choices"][0]["message"]["content"]
        return json.loads(txt)

    except:
        return None

# =========================
# CORE ENGINE
# =========================
def evaluate(d):

    try:
        a=d["a_for"]
        b=d["a_against"]
        c=d["b_for"]
        e=d["b_against"]
    except:
        return "❌ Invalid data"

    lg,f = league(a,c)
    xg_val = xg(a,b,c,e,f)

    odds = d.get("odds",{})

    results=[]

    for line in [2.5,3.5,4.5,5.5,6.5,7.5,8.5]:

        key=f"over_{line}"

        if key not in odds:
            continue

        p = prob_over(line,xg_val)
        o = odds[key]

        if o < 1.2 or o > 15:
            continue

        e = ev(p,o)

        if e > 0.07 and p > 0.55:
            results.append((key,p,o,e))

    if not results:
        return f"❌ NO VALUE\nLeague: {lg}\nxG: {round(xg_val,2)}"

    results.sort(key=lambda x:x[3],reverse=True)
    top = results[:2]

    out=[]

    for m,p,o,e in top:

        clv = CLV_DB.get(m)
        CLV_DB[m] = o
        save_clv(CLV_DB)

        out.append(
            f"{m}\nProb:{round(p*100,1)}%\nOdds:{o}\nEV:{round(e,3)}\nCLV:{clv}"
        )

    return f"""
🔥 SNIPER PICKS

League: {lg}
xG: {round(xg_val,2)}

{chr(10).join(out)}
"""

# =========================
# TELEGRAM HELPERS
# =========================
def send(cid,msg):
    requests.post(f"{BASE_URL}/sendMessage",
        json={"chat_id":cid,"text":msg})

def file_url(fid):
    r=requests.get(f"{BASE_URL}/getFile?file_id={fid}")
    p=r.json()["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{p}"

# =========================
# WEBHOOK ENDPOINT (ASYNC READY)
# =========================
@app.post("/")
async def webhook(req: Request):

    d = await req.json()

    if "message" in d:
        m = d["message"]
        cid = m["chat"]["id"]

        if "photo" in m:

            fid = m["photo"][-1]["file_id"]
            url = file_url(fid)

            send(cid,"⏳ Analyzing...")

            data = await parse_image(url)

            if not data:
                send(cid,"❌ Failed to read image")
                return {"ok":True}

            send(cid, evaluate(data))

        else:
            send(cid,"📸 Send screenshot")

    return {"ok":True}

# =========================
# AUTO WEBHOOK SETUP
# =========================
@app.on_event("startup")
def startup():
    if RENDER_EXTERNAL_URL:
        requests.get(
            f"{BASE_URL}/setWebhook?url={RENDER_EXTERNAL_URL}"
        )

# =========================
# RUN
# =========================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
