import os
from fastapi import FastAPI
import httpx
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

app = FastAPI()

# Ambil Environment Variables dari Vercel
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")

# Taktik jitu: Matikan deteksi proxy otomatis lingkungan Vercel biar tidak TypeError
custom_http_client = httpx.Client(trust_env=False)
options = ClientOptions(http_client=custom_http_client)

# Inisialisasi client Supabase dengan opsi custom
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=options)

@app.get("/")
def read_root():
    return {"status": "Mesin Jurnal Backend Berjalan Lancar!", "info": "Gunakan /docs untuk visualisasi API"}

@app.get("/cari")
def cari_jurnal(q: str):
    # Logika pencarian kamu tetap aman di sini
    return {"query": q, "message": "Endpoint siap menerima parameter pencarian"}
