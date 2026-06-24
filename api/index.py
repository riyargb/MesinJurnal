import os
import io
import csv
import httpx
import rispy
import pandas as pd
import pdfplumber
import pytesseract
from PIL import Image
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def parse_ris(content: str):
    entries = rispy.loads(content)
    return [{"judul": e.get("title", ""), "penulis": e.get("authors", []), "tahun": e.get("year", ""), "abstrak": e.get("abstract", "")} for e in entries]

def parse_pdf(content: bytes):
    results = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    results.append({"judul": "", "penulis": [], "tahun": "", "abstrak": text[:500]})
    return results

def parse_txt(content: str):
    return [{"judul": "", "penulis": [], "tahun": "", "abstrak": content[:500]}]

def parse_csv(content: str):
    results = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        results.append({"judul": row.get("title", row.get("judul", "")), "penulis": row.get("authors", row.get("penulis", "")), "tahun": row.get("year", row.get("tahun", "")), "abstrak": row.get("abstract", row.get("abstrak", ""))})
    return results

def parse_xlsx(content: bytes):
    df = pd.read_excel(io.BytesIO(content))
    df.columns = [c.lower() for c in df.columns]
    results = []
    for _, row in df.iterrows():
        results.append({"judul": row.get("title", row.get("judul", "")), "penulis": row.get("authors", row.get("penulis", "")), "tahun": row.get("year", row.get("tahun", "")), "abstrak": row.get("abstract", row.get("abstrak", ""))})
    return results

def parse_image(content: bytes):
    image = Image.open(io.BytesIO(content))
    text = pytesseract.image_to_string(image)
    return [{"judul": "", "penulis": [], "tahun": "", "abstrak": text[:500]}]

@app.get("/")
def root():
    return {"status": "Mesin Jurnal aktif", "info": "Gunakan /docs untuk API"}

@app.get("/cari")
def cari_jurnal(q: str):
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": q + " filetype:pdf", "gl": "id", "hl": "id"}
    r = httpx.post("https://google.serper.dev/search", json=payload, headers=headers)
    data = r.json()
    hasil = [{"judul": item.get("title"), "link": item.get("link"), "snippet": item.get("snippet")} for item in data.get("organic", [])]
    return {"query": q, "hasil": hasil}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    nama = file.filename.lower()
    try:
        if nama.endswith(".ris"):
            data = parse_ris(content.decode("utf-8"))
        elif nama.endswith(".pdf"):
            data = parse_pdf(content)
        elif nama.endswith(".txt"):
            data = parse_txt(content.decode("utf-8"))
        elif nama.endswith(".csv"):
            data = parse_csv(content.decode("utf-8"))
        elif nama.endswith(".xlsx"):
            data = parse_xlsx(content)
        elif nama.endswith((".png", ".jpg", ".jpeg")):
            data = parse_image(content)
        else:
            return {"error": "Format tidak didukung"}
        return {"file": file.filename, "total": len(data), "data": data}
    except Exception as e:
        return {"error": str(e)}
