from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import requests
import os

app = FastAPI(title="Mesin Jurnal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mengambil variabel sensitif dari Environment Variables Vercel
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Validasi pengecekan env
if not all([SERPER_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeWarning("Peringatan: Kredensial Environment Variables belum lengkap diset di Vercel!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def ambil_metadata_crossref(judul: str):
    url = f"https://api.crossref.org/works?query={requests.utils.quote(judul)}&rows=1"
    try:
        response = requests.get(url, headers={"User-Agent": "MesinJurnalBot/1.0 (mailto:riyawanff09@gmail.com)"}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            items = data.get("message", {}).get("items", [])
            if items:
                item = items[0]
                tahun = "Tidak diketahui"
                pub_date = item.get("published-print") or item.get("published-online") or item.get("created")
                if pub_date and "date-parts" in pub_date:
                    tahun = str(pub_date["date-parts"][0][0])
                
                penulis = "Anonim"
                authors = item.get("author", [])
                if authors:
                    penulis = f"{authors[0].get('given', '')} {authors[0].get('family', '')}".strip()
                
                doi = item.get("DOI", "")
                return penulis, tahun, doi
    except Exception:
        pass
    return "Tidak diketahui", "Tidak diketahui", ""

@app.get("/")
def index():
    return {"status": "Mesin Jurnal Backend Berjalan Lancar!", "endpoint_pencarian": "/cari?q=keyword"}

@app.get("/cari")
def cari_jurnal(q: str = Query(..., description="Kata kunci jurnal")):
    keyword_bersih = q.lower().strip()

    try:
        cache = supabase.table("hasil_jurnal").select("*").eq("keyword", keyword_bersih).execute()
        if cache.data:
            return {"source": "database_cache", "results": cache.data}
    except Exception as e:
        print(f"Gagal membaca Supabase: {e}")

    serper_url = "https://google.serper.dev/scholar"
    headers = {
        'X-API-KEY': SERPER_API_KEY,
        'Content-Type': 'application/json'
    }
    payload = {"q": keyword_bersih, "num": 5}

    try:
        response = requests.post(serper_url, headers=headers, json=payload)
        serper_data = response.json()
    except Exception as e:
        return {"error": f"Gagal menghubungi Serper API: {e}"}

    hasil_akhir = []
    
    if "organic" in serper_data:
        for item in serper_data["organic"]:
            judul = item.get("title")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            
            penulis, depth_tahun, doi = ambil_metadata_crossref(judul)
            
            jurnal_obj = {
                "keyword": keyword_bersih,
                "judul": judul,
                "link_pdf": link,
                "snippet": snippet,
                "penulis": penulis,
                "tahun": depth_tahun,
                "doi": doi
            }
            hasil_akhir.append(jurnal_obj)
            
            try:
                supabase.table("hasil_jurnal").insert(jurnal_obj).execute()
            except Exception as e:
                print(f"Gagal menyimpan ke Supabase: {e}")

    return {"source": "serper_api_scholar_live", "results": hasil_akhir}
