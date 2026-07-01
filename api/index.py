import os
import io
import csv
import httpx
import rispy
import pandas as pd
import pdfplumber
import pytesseract
from PIL import Image
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client
from datetime import datetime, timedelta
import json
import hmac
import hashlib

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")
SAWERIA_STREAM_KEY = os.environ.get("SAWERIA_STREAM_KEY", "GANTI_STREAM_KEY")
SAWERIA_PRO_AMOUNT = int(os.environ.get("SAWERIA_PRO_AMOUNT", "50000"))
SAWERIA_MAX_AMOUNT = int(os.environ.get("SAWERIA_MAX_AMOUNT", "100000"))
SAWERIA_USERNAME = os.environ.get("SAWERIA_USERNAME", "Kikomaukiko")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HTML = open(os.path.join(os.path.dirname(__file__), "../index.html")).read()

TIER_LIMITS = {
    "free":     {"cari": 5,   "upload": 10},
    "pro":      {"cari": 30,  "upload": 50},
    "max":      {"cari": 50,  "upload": 100},
    "infinite": {"cari": 999999, "upload": 999999},
}
RESET_HOURS = 5

# ── PARSER ──────────────────────────────────────────────────────────────────

def parse_ris(content: str):
    results = []
    entry = {}
    authors = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("TY  -"): entry = {}; authors = []
        elif line.startswith("TI  -") or line.startswith("T1  -"): entry["judul"] = line.split("-",1)[1].strip()
        elif line.startswith("AU  -") or line.startswith("A1  -"): authors.append(line.split("-",1)[1].strip())
        elif line.startswith("PY  -") or line.startswith("Y1  -"): entry["tahun"] = line.split("-",1)[1].strip()[:4]
        elif line.startswith("AB  -") or line.startswith("N2  -"): entry["abstrak"] = line.split("-",1)[1].strip()
        elif line.startswith("DO  -"): entry["doi"] = line.split("-",1)[1].strip()
        elif line.startswith("JF  -") or line.startswith("JO  -") or line.startswith("T2  -"): entry["jurnal"] = line.split("-",1)[1].strip()
        elif line.startswith("KW  -"):
            if "keywords" not in entry: entry["keywords"] = []
            entry["keywords"].append(line.split("-",1)[1].strip())
        elif line.startswith("ER  -"):
            entry["penulis"] = authors
            if "judul" in entry: results.append(entry)
    return results

def parse_pdf(content):
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return [{"judul":"","penulis":[],"tahun":"","abstrak":text[:1000]}]

def parse_txt(content):
    if "TY  -" in content and "ER  -" in content: return parse_ris(content)
    return [{"judul":"","penulis":[],"tahun":"","abstrak":content[:1000]}]

def parse_csv(content):
    results = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        results.append({"judul":row.get("title",row.get("judul","")),"penulis":row.get("authors",row.get("penulis","")),"tahun":row.get("year",row.get("tahun","")),"abstrak":row.get("abstract",row.get("abstrak",""))})
    return results

def parse_xlsx(content):
    df = pd.read_excel(io.BytesIO(content))
    df.columns = [c.lower() for c in df.columns]
    results = []
    for _, row in df.iterrows():
        results.append({"judul":str(row.get("title",row.get("judul",""))),"penulis":str(row.get("authors",row.get("penulis",""))),"tahun":str(row.get("year",row.get("tahun",""))),"abstrak":str(row.get("abstract",row.get("abstrak","")))})
    return results

def parse_image(content):
    image = Image.open(io.BytesIO(content))
    text = pytesseract.image_to_string(image)
    return [{"judul":"","penulis":[],"tahun":"","abstrak":text[:1000]}]

# ── AUTH & QUOTA ─────────────────────────────────────────────────────────────

def get_user_from_token(token: str):
    try:
        res = supabase.auth.get_user(token)
        return res.user
    except:
        return None

def get_or_create_quota(user_id: str):
    try:
        res = supabase.table("user_quota").select("*").eq("user_id", user_id).single().execute()
        return res.data
    except:
        now = datetime.utcnow().isoformat()
        new_quota = {
            "user_id": user_id,
            "tier": "free",
            "cari_used": 0,
            "upload_used": 0,
            "reset_at": (datetime.utcnow() + timedelta(hours=RESET_HOURS)).isoformat()
        }
        supabase.table("user_quota").insert(new_quota).execute()
        return new_quota

def parse_dt(s):
    if not s: return datetime.utcnow()
    try:
        s = s.replace('+00:00','').replace('Z','').split('.')[0]
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
    except:
        return datetime.utcnow()

def check_and_use_quota(user_id: str, action: str):
    quota = get_or_create_quota(user_id)
    tier = quota.get("tier", "free")
    limit = TIER_LIMITS.get(tier, TIER_LIMITS["free"])[action]
    used_key = f"{action}_used"
    used = quota.get(used_key, 0)
    reset_at = parse_dt(quota.get("reset_at"))

    # Infinite tier - tidak ada limit dan reset
    if tier == "infinite":
        supabase.table("user_quota").update({used_key: used + 1}).eq("user_id", user_id).execute()
        return True, used + 1, limit, tier, reset_at

    # Auto reset jika sudah lewat waktu
    if datetime.utcnow() > reset_at:
        new_reset = (datetime.utcnow() + timedelta(hours=RESET_HOURS)).isoformat()
        supabase.table("user_quota").update({
            "cari_used": 0,
            "upload_used": 0,
            "reset_at": new_reset
        }).eq("user_id", user_id).execute()
        used = 0
        reset_at = datetime.utcnow() + timedelta(hours=RESET_HOURS)

    if used >= limit:
        return False, used, limit, tier, reset_at

    supabase.table("user_quota").update({used_key: used + 1}).eq("user_id", user_id).execute()
    return True, used + 1, limit, tier, reset_at

# ── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root(): return HTML

@app.post("/auth/register")
async def register(body: dict):
    try:
        res = supabase.auth.sign_up({"email": body["email"], "password": body["password"]})
        return {"success": True, "message": "Cek email untuk verifikasi"}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/auth/login")
async def login(body: dict):
    try:
        res = supabase.auth.sign_in_with_password({"email": body["email"], "password": body["password"]})
        quota = get_or_create_quota(res.user.id)
        return {
            "success": True,
            "access_token": res.session.access_token,
            "user": {"email": res.user.email, "id": res.user.id},
            "quota": quota
        }
    except Exception as e:
        raise HTTPException(401, "Email atau password salah")

@app.get("/auth/me")
async def me(request: Request):
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = auth.replace("Bearer ", "").strip()
    if not token: raise HTTPException(401, "Unauthorized")
    user = get_user_from_token(token)
    if not user: raise HTTPException(401, "Token tidak valid")
    quota = get_or_create_quota(user.id)
    return {"user": {"email": user.email, "id": user.id}, "quota": quota}

@app.get("/quota")
async def get_quota_endpoint(request: Request):
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = auth.replace("Bearer ", "").strip()
    if not token: raise HTTPException(401, "Unauthorized")
    user = get_user_from_token(token)
    if not user: raise HTTPException(401, "Token tidak valid")
    quota = get_or_create_quota(user.id)
    tier = quota.get("tier","free")
    return {
        "tier": tier,
        "cari_used": quota.get("cari_used",0),
        "cari_limit": TIER_LIMITS[tier]["cari"],
        "upload_used": quota.get("upload_used",0),
        "upload_limit": TIER_LIMITS[tier]["upload"],
        "reset_at": quota.get("reset_at"),
        "saweria_pro": f"https://saweria.co/{SAWERIA_USERNAME}?amount={SAWERIA_PRO_AMOUNT}",
        "saweria_max": f"https://saweria.co/{SAWERIA_USERNAME}?amount={SAWERIA_MAX_AMOUNT}"
    }


@app.post("/webhook/saweria")
async def saweria_webhook(body: dict):
    try:
        amount = int(body.get("amount", 0))
        message = body.get("message", "").strip()
        email = message.replace("PRO-","").replace("MAX-","").strip()
        if not email: return {"status": "ignored"}

        SAWERIA_INFINITE_AMOUNT = int(os.environ.get("SAWERIA_INFINITE_AMOUNT", "150000"))
        if amount >= SAWERIA_INFINITE_AMOUNT:
            tier = "infinite"
        elif amount >= SAWERIA_MAX_AMOUNT:
            tier = "max"
        elif amount >= SAWERIA_PRO_AMOUNT:
            tier = "pro"
        else:
            return {"status": "amount terlalu kecil"}

        # Cari user by email
        try:
            users = supabase.auth.admin.list_users()
            user_id = None
            for u in users:
                if u.email == email:
                    user_id = u.id
                    break
        except:
            user_id = None

        if user_id:
            supabase.table("user_quota").update({"tier": tier}).eq("user_id", user_id).execute()
            return {"status": "ok", "tier": tier, "email": email}

        return {"status": "user not found"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/cari")
async def cari_jurnal(q: str, request: Request):
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = auth.replace("Bearer ", "").strip()
    if token:
        user = get_user_from_token(token)
        if user:
            ok, used, limit, tier, reset_at = check_and_use_quota(user.id, "cari")
            if not ok:
                raise HTTPException(429, {
                    "message": f"Kuota cari habis ({limit}/{limit}). Reset pukul {reset_at.strftime('%H:%M')} WIB.",
                    "tier": tier,
                    "reset_at": reset_at.isoformat()
                })

    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": q + " jurnal ilmiah peer reviewed", "gl": "id", "hl": "id"}
    r = httpx.post("https://google.serper.dev/search", json=payload, headers=headers)
    data = r.json()
    hasil = [{"judul":item.get("title"),"link":item.get("link"),"snippet":item.get("snippet")} for item in data.get("organic",[])]
    return {"query": q, "hasil": hasil}

@app.get("/doi")
def fetch_doi(doi: str):
    try:
        r = httpx.get(f"https://api.crossref.org/works/{doi}", timeout=10)
        d = r.json().get("message",{})
        date_parts = d.get("published-print",d.get("published-online",{})).get("date-parts",[[""]])
        tahun = str(date_parts[0][0]) if date_parts and date_parts[0] else ""
        return {
            "judul": d.get("title",[""])[0],
            "penulis": [f"{a.get('given','')} {a.get('family','')}" for a in d.get("author",[])],
            "tahun": tahun,
            "jurnal": d.get("container-title",[""])[0],
            "abstrak": d.get("abstract","").replace("<jats:p>","").replace("</jats:p>",""),
            "doi": doi
        }
    except Exception as e: return {"error": str(e)}

@app.get("/pdf")
def cari_pdf(doi: str = None, judul: str = None):
    try:
        if doi:
            r = httpx.get(f"https://api.unpaywall.org/v2/{doi}?email=mesin@jurnal.app", timeout=10)
            if r.status_code == 200:
                data = r.json()
                oa = data.get("best_oa_location")
                if oa and oa.get("url_for_pdf"):
                    return {"pdf_url": oa["url_for_pdf"], "source": "unpaywall"}
        q = judul or doi or ""
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": q + " filetype:pdf site:researchgate.net OR site:semanticscholar.org OR site:academia.edu"}
        r = httpx.post("https://google.serper.dev/search", json=payload, headers=headers)
        hasil = r.json().get("organic",[])
        if hasil: return {"pdf_url": hasil[0]["link"], "source": "serper"}
        return {"pdf_url": None, "source": None}
    except Exception as e: return {"error": str(e)}

@app.post("/export/ris")
async def export_ris(data: list):
    output = ""
    for item in data:
        output += "TY  - JOUR\n"
        if item.get("judul"): output += f"TI  - {item['judul']}\n"
        for p in (item.get("penulis") or []):
            output += f"AU  - {p}\n"
        if item.get("tahun"): output += f"PY  - {item['tahun']}\n"
        if item.get("jurnal"): output += f"JF  - {item['jurnal']}\n"
        if item.get("abstrak"): output += f"AB  - {item['abstrak']}\n"
        if item.get("doi"): output += f"DO  - {item['doi']}\n"
        output += "ER  -\n\n"
    return StreamingResponse(io.BytesIO(output.encode()), media_type="application/x-research-info-systems", headers={"Content-Disposition":"attachment; filename=export.ris"})

@app.post("/export/csv")
async def export_csv(data: list):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["judul","penulis","tahun","jurnal","abstrak","doi"])
    writer.writeheader()
    for item in data:
        writer.writerow({"judul":item.get("judul",""),"penulis":", ".join(item.get("penulis",[]) if isinstance(item.get("penulis"),list) else [str(item.get("penulis",""))]),"tahun":item.get("tahun",""),"jurnal":item.get("jurnal",""),"abstrak":item.get("abstrak",""),"doi":item.get("doi","")})
    return StreamingResponse(io.BytesIO(output.getvalue().encode()), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=export.csv"})

@app.post("/upload")
async def upload_files(request: Request, files: list[UploadFile] = File(...)):
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = auth.replace("Bearer ", "").strip()
    if token:
        user = get_user_from_token(token)
        if user:
            ok, used, limit, tier, reset_at = check_and_use_quota(user.id, "upload")
            if not ok:
                raise HTTPException(429, {
                    "message": f"Kuota upload habis ({limit}/{limit}). Reset pukul {reset_at.strftime('%H:%M')} WIB.",
                    "tier": tier,
                    "reset_at": reset_at.isoformat()
                })

    semua_hasil = []
    for file in files:
        content = await file.read()
        nama = file.filename.lower()
        try:
            if nama.endswith(".ris") or nama.endswith(".ris.txt"): data = parse_ris(content.decode("utf-8"))
            elif nama.endswith(".pdf"): data = parse_pdf(content)
            elif nama.endswith(".txt"): data = parse_txt(content.decode("utf-8"))
            elif nama.endswith(".csv"): data = parse_csv(content.decode("utf-8"))
            elif nama.endswith(".xlsx"): data = parse_xlsx(content)
            elif nama.endswith((".png",".jpg",".jpeg")): data = parse_image(content)
            else: data = [{"error":"Format tidak didukung"}]
            semua_hasil.append({"file":file.filename,"total":len(data),"data":data})
        except Exception as e:
            semua_hasil.append({"file":file.filename,"error":str(e)})
    return {"total_file":len(semua_hasil),"hasil":semua_hasil}

from fastapi.responses import FileResponse
import pathlib

@app.get("/icon.jpg")
def serve_icon():
    p = pathlib.Path(__file__).parent.parent / "icon.jpg"
    return FileResponse(p, media_type="image/jpeg")

@app.get("/manifest.json")
def serve_manifest():
    p = pathlib.Path(__file__).parent.parent / "manifest.json"
    return FileResponse(p, media_type="application/json")

@app.get("/sw.js")
def serve_sw():
    p = pathlib.Path(__file__).parent.parent / "sw.js"
    return FileResponse(p, media_type="application/javascript")
