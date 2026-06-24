import os
import io
import csv
import re
import httpx
import pandas as pd
import pdfplumber
import pytesseract
from PIL import Image
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from supabase import create_client, Client

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HTML = open(os.path.join(os.path.dirname(__file__), "../index.html")).read()

def parse_ris(content: str):
    results = []
    entry = {}
    authors = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("TY  -"):
            entry = {}
            authors = []
        elif line.startswith("TI  -") or line.startswith("T1  -"):
            entry["judul"] = line.split("-", 1)[1].strip()
        elif line.startswith("AU  -") or line.startswith("A1  -"):
            authors.append(line.split("-", 1)[1].strip())
        elif line.startswith("PY  -") or line.startswith("Y1  -"):
            entry["tahun"] = line.split("-", 1)[1].strip()[:4]
        elif line.startswith("AB  -") or line.startswith("N2  -"):
            entry["abstrak"] = line.split("-", 1)[1].strip()
        elif line.startswith("DO  -"):
            entry["doi"] = line.split("-", 1)[1].strip()
        elif line.startswith("JF  -") or line.startswith("JO  -") or line.startswith("T2  -"):
            entry["jurnal"] = line.split("-", 1)[1].strip()
        elif line.startswith("ER  -"):
            entry["penulis"] = authors
            if "judul" in entry:
                results.append(entry)
    return results

def parse_pdf(content):
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return [{"judul":"","penulis":[],"tahun":"","abstrak":text[:1000]}]

def parse_txt(content):
    if "TY  -" in content and "ER  -" in content:
        return parse_ris(content)
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

@app.get("/", response_class=HTMLResponse)
def root():
    return HTML

@app.get("/cari")
def cari_jurnal(q: str):
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": q + " jurnal ilmiah peer reviewed", "gl": "id", "hl": "id"}
    r = httpx.post("https://google.serper.dev/search", json=payload, headers=headers)
    data = r.json()
    hasil = [{"judul": item.get("title"), "link": item.get("link"), "snippet": item.get("snippet")} for item in data.get("organic", [])]
    return {"query": q, "hasil": hasil}

@app.get("/doi")
def fetch_doi(doi: str):
    try:
        r = httpx.get(f"https://api.crossref.org/works/{doi}", timeout=10)
        d = r.json().get("message", {})
        return {
            "judul": d.get("title", [""])[0],
            "penulis": [f"{a.get('given','')} {a.get('family','')}" for a in d.get("author", [])],
            "tahun": str(d.get("published-print", d.get("published-online", {})).get("date-parts", [[""]])[0][0]),
            "jurnal": d.get("container-title", [""])[0],
            "abstrak": d.get("abstract", ""),
            "doi": doi
        }
    except Exception as e:
        return {"error": str(e)}

@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    semua_hasil = []
    for file in files:
        content = await file.read()
        nama = file.filename.lower()
        try:
            if nama.endswith(".ris") or nama.endswith(".ris.txt"):
                data = parse_ris(content.decode("utf-8"))
            elif nama.endswith(".pdf"):
                data = parse_pdf(content)
            elif nama.endswith(".txt"):
                data = parse_txt(content.decode("utf-8"))
            elif nama.endswith(".csv"):
                data = parse_csv(content.decode("utf-8"))
            elif nama.endswith(".xlsx"):
                data = parse_xlsx(content)
            elif nama.endswith((".png",".jpg",".jpeg")):
                data = parse_image(content)
            else:
                data = [{"error": "Format tidak didukung"}]
            semua_hasil.append({"file": file.filename, "total": len(data), "data": data})
        except Exception as e:
            semua_hasil.append({"file": file.filename, "error": str(e)})
    return {"total_file": len(semua_hasil), "hasil": semua_hasil}

@app.get("/pdf")
def cari_pdf(doi: str = None, judul: str = None):
    try:
        if doi:
            # Unpaywall dulu
            r = httpx.get(f"https://api.unpaywall.org/v2/{doi}?email=mesin@jurnal.app", timeout=10)
            if r.status_code == 200:
                data = r.json()
                oa = data.get("best_oa_location")
                if oa and oa.get("url_for_pdf"):
                    return {"pdf_url": oa["url_for_pdf"], "source": "unpaywall"}
        # Fallback ke Serper
        q = judul or doi or ""
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": q + " filetype:pdf", "gl": "id", "hl": "id"}
        r = httpx.post("https://google.serper.dev/search", json=payload, headers=headers)
        hasil = r.json().get("organic", [])
        pdf_links = [h for h in hasil if ".pdf" in h.get("link","").lower()]
        if pdf_links:
            return {"pdf_url": pdf_links[0]["link"], "source": "serper"}
        elif hasil:
            return {"pdf_url": hasil[0]["link"], "source": "serper_page"}
        return {"pdf_url": None, "source": None}
    except Exception as e:
        return {"error": str(e)}
