import os
from fastapi import FastAPI
import httpx
from supabase import create_client, Client

app = FastAPI()

# Ambil Environment Variables dari Vercel
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")

# Taktik jitu bypass proxy: Masukkan httpx.Client langsung ke create_client
custom_http_client = httpx.Client(trust_env=False)

# Inisialisasi client Supabase dengan http_client langsung di fungsi utama
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, http_client=custom_http_client)

@app.get("/")
def read_root():
    return {"status": "Mesin Jurnal Backend Berjalan Lancar!", "info": "Gunakan /docs untuk visualisasi API"}

@app.get("/cari")
def cari_jurnal(q: str):
    return {"query": q, "message": "Endpoint siap menerima parameter pencarian"}
