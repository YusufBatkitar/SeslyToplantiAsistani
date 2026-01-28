import os
import uuid
import shutil
import subprocess
import tempfile
import base64
import json
import time
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, UploadFile, File, Query, Body, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from db_utils import upload_file, save_meeting_record, delete_user_account
from starlette.middleware.base import BaseHTTPMiddleware
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import uvicorn
import logging
from rapor import generate_meeting_report, save_to_supabase
from urllib.parse import urlparse, parse_qs
import re
from dotenv import load_dotenv

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

templates = Jinja2Templates(directory="web_arayuz")


def clean_transcript(text: str) -> str:
    if not text:
        return ""

    # Gereksiz boşlukları tek boşluğa indir
    t = " ".join(text.split())

    # Cümle sonlarına göre böl
    sentences = re.split(r'(?<=[.!?])\s+', t)

    # Her cümleyi yeni satıra koy
    formatted = "\n".join(sentences)

    return formatted.strip()

from dotenv import load_dotenv
load_dotenv(override=True)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY bulunamadı! .env dosyasını kontrol edin.")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
genai.configure(api_key=API_KEY, transport="rest")


# Lifespan event handler (modern FastAPI pattern)
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    
    print("\n" + "="*60)
    print(" SESLY SERVER BAŞLATILDI!")
    print("="*60)
    port = os.getenv("PORT", "8000")
    print(f" Web Arayüzü: http://127.0.0.1:{port}")
    print(f" Alternatif:  http://localhost:{port}")
    print("="*60)
    print(f"🔹 Ana Sayfa:    http://127.0.0.1:{port}/")
    print(f"🔹 Toplantılar:  http://127.0.0.1:{port}/meetings")
    print(f"🔹 Takvim:       http://127.0.0.1:{port}/calendar")
    print(f"🔹 Ayarlar:      http://127.0.0.1:{port}/settings")
    print("="*60 + "\n")
    
    print("="*60 + "\n")

    # =========================================================
    # STARTUP CLEANUP: Remove stale files from previous runs
    # =========================================================
    print("[SERVER] Temizlik yapılıyor...")
    try:
        # ---------------------------------------------------------
        # ZOMBIE PROCESS CLEANUP (Recorder & Ffmpeg)
        # ---------------------------------------------------------
        import psutil
        current_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                # 1. Kill old FFMPEG
                if proc.info['name'] and 'ffmpeg' in proc.info['name'].lower():
                    print(f"[CLEANUP] Killing zombie ffmpeg (PID: {proc.info['pid']})")
                    proc.kill()
                
                # 2. Kill old RECORDER scripts (not this server)
                if proc.info['pid'] != current_pid and proc.info['name'] and 'python' in proc.info['name'].lower():
                    cmdline = proc.info.get('cmdline') or []
                    # Check if it looks like one of our workers
                    if any(x in str(cmdline) for x in ['zoom_bot_recorder', 'meet_worker', 'teams_web_worker']):
                         print(f"[CLEANUP] Killing zombie worker (PID: {proc.info['pid']})")
                         proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        cleanup_targets = [
            Path("latest_transcript.txt"),
            Path("live_transcript_cache.json"),
            Path("participants.json"),
            Path("speaker_activity_log.json")
        ]
        
        for p in cleanup_targets:
            if p.exists():
                p.unlink()
                print(f"[CLEANUP] Silindi: {p.name}")
        
        # Temp reports klasörünü temizle
        reports_dir = Path("temp_reports")
        if reports_dir.exists():
            for item in reports_dir.glob("*"):
                if item.is_file():
                    item.unlink()
            print("[CLEANUP] Raporlar temizlendi.")
            
    except Exception as e:
        print(f"[WARN] Startup cleanup hatası: {e}")
        
    # Reset Worker Status (Ghost Bot önleme)
    try:
        Path("data/worker_status.json").write_text(
            json.dumps({"running": False, "recording": False, "status_message": "Sistem Hazır"}, ensure_ascii=False), 
            encoding="utf-8"
        )
        print("[CLEANUP] Worker status sıfırlandı (data/worker_status.json)")
    except Exception: pass
    
    yield  # Server çalışıyor
    
    # Shutdown (gerekirse buraya cleanup kodu eklenebilir)
    print("\n[SERVER] Kapatılıyor...")

# FastAPI app'i lifespan ile oluştur
app = FastAPI(
    title="Sesly Toplantı Bot + Transkript Servisi",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Raporları sunmak için statik dizin (HTML raporlar tarayıcıda açılsın diye)
Path("temp_reports").mkdir(exist_ok=True)
app.mount("/reports", StaticFiles(directory="temp_reports"), name="reports")

# =========================================================
# REPORT PROXY - Supabase HTML'i doğru Content-Type ile sun
# =========================================================
import httpx

@app.get("/view-report")
async def view_report(url: str = Query(..., description="Supabase report URL")):
    """
    Supabase'den HTML raporu çekip doğru Content-Type ile sun.
    Bu endpoint MIME type sorununu çözer.
    """
    try:
        # URL güvenlik kontrolü
        if "supabase" not in url and "localhost" not in url:
            return Response(content="Geçersiz URL", status_code=400)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
            
        if response.status_code != 200:
            return Response(content=f"Rapor yüklenemedi: {response.status_code}", status_code=response.status_code)
        
        # HTML olarak döndür
        return Response(
            content=response.content,
            media_type="text/html; charset=utf-8",
            headers={"Content-Type": "text/html; charset=utf-8"}
        )
        
    except Exception as e:
        return Response(content=f"Hata: {str(e)}", status_code=500)

@app.get("/view-transcript")
async def view_transcript(url: str = Query(..., description="Supabase transcript URL")):
    """
    Supabase'den transkripti çekip doğru Content-Type ile sun.
    UTF-8 encoding sorununu çözer.
    """
    try:
        # URL güvenlik kontrolü
        if "supabase" not in url and "localhost" not in url:
            return Response(content="Geçersiz URL", status_code=400)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
            
        if response.status_code != 200:
            return Response(content=f"Transkript yüklenemedi: {response.status_code}", status_code=response.status_code)
        
        # Metni UTF-8 olarak decode et ve düzgün göster
        # Supabase bazen Latin-1 olarak encode ediyor
        try:
            text = response.content.decode('utf-8')
        except:
            text = response.content.decode('latin-1')
        
        # Plain text olarak döndür (UTF-8)
        return Response(
            content=text.encode('utf-8'),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Type": "text/plain; charset=utf-8"}
        )
        
    except Exception as e:
        return Response(content=f"Hata: {str(e)}", status_code=500)

# =========================================================
# WEBM SPLIT & TRANSCRIBE (YENİ - WAV YOK)
# =========================================================

def split_webm_ffmpeg(webm_path: Path, output_dir: Path, segment_length=300):
    """
    Tek bir büyük WebM dosyasını ~5 dakikalık WebM segmentlere böler.
    """
    ffmpeg_path = os.getenv("FFMPEG_PATH")
    if not ffmpeg_path:
        ffmpeg_path = shutil.which("ffmpeg")
    
    # Fallback to the known hardcoded path if nothing else works
    if not ffmpeg_path:
        hardcoded_path = r"C:\Users\user\Desktop\ffmpeg-2025-10-19-git-dc39a576ad-full_build\bin\ffmpeg.exe"
        if os.path.exists(hardcoded_path):
            ffmpeg_path = hardcoded_path
        else:
            ffmpeg_path = "ffmpeg"  

    output_pattern = output_dir / "chunk_%03d.webm"
    cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-i", str(webm_path),
        "-map", "0:a",
        "-c:a", "libopus",
        "-b:a", "32k",
        "-vbr", "on",
        "-f", "segment",
        "-segment_time", str(segment_length),
        str(output_pattern)
    ]
    subprocess.run(cmd, capture_output=True)
    return sorted(output_dir.glob("chunk_*.webm"))


def recompress_webm_for_gemini(src: Path, dst: Path, audio_bitrate="16k"):
    """
    Gelen büyük WebM dosyasını:
    - Sadece AUDIO track'e indirger
    - Tek kanal (mono), 16 kHz
    - Opus codec, düşük bitrate (örn: 16k)
    olacak şekilde yeniden encode eder.
    Böylece 5 dakikalık kayıt ~1–2 MB civarına düşer.
    """
    ffmpeg_path = os.getenv("FFMPEG_PATH")
    if not ffmpeg_path:
        ffmpeg_path = shutil.which("ffmpeg")
    
    # Fallback to the known hardcoded path
    if not ffmpeg_path:
        hardcoded_path = r"C:\Users\user\Desktop\ffmpeg-2025-10-19-git-dc39a576ad-full_build\bin\ffmpeg.exe"
        if os.path.exists(hardcoded_path):
            ffmpeg_path = hardcoded_path
        else:
            ffmpeg_path = "ffmpeg"

    cmd = [
        ffmpeg_path,
        "-y",                      # var ise üstüne yaz
        "-i", str(src),

        # Sadece ilk audio track
        "-map", "0:a:0",

        # Video / subtitle / data tamamen kapalı
        "-vn",
        "-sn",
        "-dn",

        # Opus düşük bitrate
        "-c:a", "libopus",
        "-b:a", audio_bitrate,     # "16k" / "24k" / "32k"
        "-vbr", "on",
        "-application", "voip",
        "-ac", "1",                # mono
        "-ar", "16000",            # 16 kHz

        str(dst)
    ]

    print("[FFMPEG] WebM yeniden encode ediliyor (Gemini için düşük bitrate)...")
    print(" ".join(cmd))

    # Hata olursa exception fırlatsın ki logta görelim
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[ERROR] Recompress başarısız!")
        print("STDOUT:", result.stdout[:500])
        print("STDERR:", result.stderr[:500])
        # fallback: orijinal dosyayı kullan
        return False

    # Başarılı
    new_size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"[OK] Sıkıştırılmış WebM hazır: {new_size_mb:.2f} MB")
    return True


def transcribe_webm_segment(webm_path: Path, label: str, is_final: bool, speaker_hint: str = None, timeline_hint: str = None, platform: str = None):
    """
    Tek bir WebM segmenti için konuşmacı tanımlı transkripsiyon
    (eski transcribe_wav ile aynı mantık, sadece mime_type değişti)
    """
    with open(webm_path, "rb") as f:
        webm_bytes = f.read()

    audio_b64 = base64.b64encode(webm_bytes).decode("utf-8")
    audio_part = {"mime_type": "audio/webm", "data": audio_b64}

    participants_file = Path("current_meeting_participants.json")
    participant_names = []

    if participants_file.exists():
        try:
            data = json.loads(participants_file.read_text(encoding="utf-8"))
            participant_names = data.get("participants", [])
            print(f"[INFO] Transkripsiyon icin {len(participant_names)} katilimci bilgisi yuklendi")
        except Exception as e:
            print(f"[WARN] Katilimci bilgisi okunamadi: {e}")

    speaker_instruction = ""

    if timeline_hint:
        if participant_names:
            speaker_instruction += f"**KATILIMCI LİSTESİ:** {', '.join(participant_names)}\n"
        
        # MEET için HİBRİT YAKLAŞIM: Görsel ipucu + Ses analizi
        if platform == "meet":
            speaker_instruction += f"""
**GÖRSEL İPUÇLARI (Referans - Doğrulama Gerekebilir):**
Aşağıdaki görsel tespitler yapıldı ancak MUTLAK DEĞİLDİR:
{timeline_hint}

**HİBRİT DİARİZATİON TALİMATI:**
1. Yukarıdaki görsel ipuçlarını REFERANS olarak kullan
2. AYRICA ses karakteristiklerinden (ses tonu, tempo, aksan) konuşmacıları ayırt et
3. Eğer görsel ipucu ile ses analizi ÇELİŞİRSE, SES ANALİZİNE güven
4. Konuşmacı değişimlerinde ses tonu/tempo farklılıklarına dikkat et
5. Katılımcı listesindeki isimleri MUTLAKA kullan, "Konuşmacı 1" gibi genel etiketler kullanma
"""
        else:
            # Zoom/Teams için eski davranış (görsel tespite güven)
            speaker_instruction += f"""
**GÖRSEL ZAMAN ÇİZELGESİ (KESİN BİLGİ):**
Toplanti sirasindaki görsel tespitler aşağidadir. Lütfen bu akisi takip et:
{timeline_hint}

TALİMAT: Yukaridaki zaman çizelgesine bak. Ses kaydindaki konuşmalari bu sirayla eşleştir.
Örn: 00:10'da Ahmet konuşmaya başladiysa, o saniyedeki sesi Ahmet'e yaz.
"""
    elif speaker_hint:
        speaker_instruction = f"""
**BİLİNEN KONUŞMACI:** Bu segmentte konuşan kişi büyük ihtimalle: **{speaker_hint}**
Lütfen transkriptte konuşmayı bu kişiye atfet.
"""
    elif participant_names and len(participant_names) > 0:
        names_list = ", ".join(participant_names)
        speaker_instruction = f"""
**KATILIMCI LİSTESİ:** Toplantıda şu kişiler var: {names_list}
Lütfen konuşmayı bu kişilerle eşleştirmeye çalış.
Eğer konuşmacı ismini söylerse (ör: "Ben Ahmet") veya başkası hitap ederse (ör: "Söz senin Ayşe") bu ipuçlarını KESİNLİKLE kullan.
AYRICA: Birisine soru sorulursa (ör: "Samet şu işi yaptın mı?") ve hemen ardından biri cevap verirse, o konuşan kişinin sorulan kişi (Samet) olduğunu varsay.
"""
    else:
        speaker_instruction = """
**TALİMAT:**
1. Konuşmacıları ayırt et (Speaker Diarization).
2. **ÖNEMLİ:** Konuşma içeriğindeki ipuçlarını (ör: "Ben Oktay", "Merhaba Ali bey") kullanarak gerçek isimleri bul.
3. İsim bulamazsan 'Konuşmacı 1', 'Konuşmacı 2' etiketlerini kullan.
"""

    prompt = f"""
Bu bir Türkçe toplantı ses kaydıdır. Lütfen konuşmacı diarization (konuşmacı ayrımı) yaparak transkript oluştur.

{speaker_instruction}


**KRİTİK - SESSİZLİK KONTROLÜ:**
- Eğer ses kaydında HİÇ KONUŞMA YOKSA veya sadece arka plan gürültüsü varsa, SADECE "[KONUŞMA YOK]" yaz ve başka hiçbir şey yazma.
- HALLÜSINASYON YAPMA! Eğer bir konuşma duymuyorsan, içerik UYDURMA.
- Sessizlik, arka plan müziği veya belirsiz sesler varsa sadece "[KONUŞMA YOK]" döndür.

**ÖNEMLİ:**
- Zaman etiketi EKLEME
- Dolgu kelimelerini (eee, ııı, hmmm) temizle
- Sadece transkript döndür, açıklama yapma
- Her konuşma bloğunu yeni satırda başlat
- **KRİTİK:** ASLA "Siz:", "Sen:", "Ben:", "Konuşmacı:" gibi genel etiketler kullanma.
- KESİNLİKLE "Bilinmeyen Konuşmacı" etiketini kullanma. Eğer ismi bilmiyorsan, listeden en mantıklı kişiyi ata veya "Konuşmacı X" de.
- "Siz" kelimesini konuşmacı adı olarak ASLA kullanma.
- Müzik veya gürültü varsa [MÜZİK] veya [GÜRÜLTÜ] yaz.
"""

    model = genai.GenerativeModel(MODEL_NAME)

    max_retries = 5
    base_delay = 30  # saniye

    for attempt in range(max_retries):
        try:
            resp = model.generate_content(
                [prompt, audio_part],
                safety_settings={
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                }
            )

            try:
                transcript_text = resp.text
            except ValueError:
                # Eger response blocked veya bos ise bu hata duser
                print(f"[WARN] Model response text erisilemedi. Feedback: {resp.prompt_feedback}")
                transcript_text = ""

            # HAYALET TRANSKRİPT FİLTRESİ
            # Sessizlik veya anlamsız içerik kontrolü
            ghost_patterns = [
                "[SESSİZLİK]", "[sessizlik]", "[SILENCE]", "[silence]",
                "[MÜZİK]", "[müzik]", "[MUSIC]", "[music]",
                "[GÜRÜLTÜ]", "[gürültü]", "[NOISE]", "[noise]",
                "[KONUŞMA YOK]", "[konuşma yok]",
                "[BOŞ]", "[boş]", "[EMPTY]"
            ]
            
            clean_text = transcript_text
            for pattern in ghost_patterns:
                clean_text = clean_text.replace(pattern, "")
            clean_text = clean_text.strip()

            # Çok kısa veya boş ise atla (minimum 2 karakter)
            if len(clean_text) < 2:
                print(f"[INFO] Sessizlik/kısa içerik tespit edildi ({len(clean_text)} char) - Transkript oluşturulmadı.")
                return ""
            
            # Temizlenmiş metni kullan
            transcript_text = clean_text

            if participant_names:
                for name in participant_names:
                    name_lower = name.lower()
                    pattern = re.compile(rf'\b{re.escape(name_lower)}\b', re.IGNORECASE)
                    transcript_text = pattern.sub(name, transcript_text)

            print(f"[SUCCESS] Transkripsiyon başarılı (deneme {attempt + 1})")
            return transcript_text

        except Exception as e:
            error_str = str(e)

            # Günlük quota kontrolü
            if "current quota" in error_str.lower() or "billing" in error_str.lower():
                print("\n" + "="*60)
                print("[CRITICAL] GÜNLÜK QUOTA DOLDU!")
                print("="*60)
                print(f"[ERROR] Hata mesajı: {error_str[:200]}")
                print("\n[ÖNERİLER]:")
                print("  1.  Yarın saat 10:00'a kadar bekleyin (Türkiye saati)")
                print("  2.  Ücretli plana geçin: https://ai.google.dev/pricing")
                print("  3.  Fallback API kullanın (Whisper)")
                print("  4.  Kullanımı kontrol edin: https://ai.google.dev/usage")
                print("="*60 + "\n")

                # Retry yok
                return "[HATA] Günlük API quota doldu. Yarın tekrar deneyin."

            # RPM (rate limit) için exponential backoff
            if "429" in error_str and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)  # 30→60→120→240
                print(f"[QUOTA] Rate limit aşıldı, {delay}s bekleniyor...  (deneme {attempt+1}/{max_retries})")
                time.sleep(delay)
                continue

            # Diğer hatalar
            if attempt < max_retries - 1:
                print(f"[WARN] Hata: {error_str[:100]}")
                print(f"[RETRY] {base_delay}s sonra tekrar denenecek ({attempt+1}/{max_retries})")
                time.sleep(base_delay)
                continue
            else:
                print(f"[ERROR] Maksimum deneme sayısına ulaşıldı: {e}")
                return f"[HATA] Transkripsiyon yapılamadı: {error_str[:100]}"

    return "[HATA] Maksimum deneme sayısına ulaşıldı"


""" def transcribe_long_audio(webm_path: Path, label_prefix="segment"):
    print(f"[SPLIT] Dosya segmentlere bölünüyor: {webm_path}")

    with tempfile.TemporaryDirectory() as segdir:
        segdir = Path(segdir)
        segments = split_webm_ffmpeg(webm_path, segdir)
        text = ""
        total = len(segments)

        print(f"[SPLIT] ✓ {total} segment oluşturuldu")
        print(f"[INFO] Her segment yaklaşık 5 dakika")
        print("-" * 60)

        quota_exhausted = False
        segment_times = []
        successful_segments = 0

        for i, seg_path in enumerate(segments):
            # Quota dolmuşsa kalanları atla
            if quota_exhausted:
                print(f"[SKIP] Segment {i+1} atlanıyor (quota doldu)")
                text += f"\n\n[Segment {i+1}]\n[ATLANDI - Günlük quota doldu]"
                continue

            seg_start = time.time()
            seg_size_mb = seg_path.stat().st_size / (1024 * 1024)

            print(f"\n[SEGMENT {i+1}/{total}] İşleniyor...")
            print(f"[INFO] Boyut: {seg_size_mb:.2f} MB")
            if seg_size_mb < 0.01:
                print("[SKIP] Segment çok küçük (< 0.01 MB), atlanıyor")
                continue

            t = transcribe_webm_segment(seg_path, f"{label_prefix}-{i+1}", i == len(segments)-1)

            # Quota hatası kontrolü
            if "[HATA] Günlük API quota doldu" in t:
                quota_exhausted = True
                text += f"\n\n[Segment {i+1}]\n{t}"
                print(f"\n[STOP] Günlük quota doldu, kalan {total - i - 1} segment atlanıyor")
                break

            text += f"\n\n[Segment {i+1}]\n{t}"
            successful_segments += 1

            seg_duration = time.time() - seg_start
            segment_times.append(seg_duration)
            print(f"[OK] Segment {i+1} tamamlandı ({seg_duration:.1f}s)")

            # Segmentler arası kısa bekleme (rate limit için)
            if i < total - 1:
                print(f"[WAIT] Sonraki segment icin 5s bekleniyor...")
                time.sleep(5)

        # Özet istatistikler
        total_time = sum(segment_times)
        avg_time = total_time / len(segment_times) if segment_times else 0

        print("\n" + "="*60)
        if quota_exhausted:
            print(f"[WARNING] Günlük quota doldu!")
            print(f"[STATS] Başarılı: {successful_segments}/{total} segment")
            print(f"[STATS] Atlanan: {total - successful_segments} segment")
            print(f"[INFO] Kalan segmentler yarın transkribe edilebilir")
        else:
            print(f"[STATS] Tüm segmentler tamamlandı!")
            print(f"[STATS] Başarılı: {successful_segments}/{total} segment")

        print(f"[STATS] Toplam süre: {total_time:.1f}s ({total_time/60:.1f} dk)")
        print(f"[STATS] Ortalama segment süresi: {avg_time:.1f}s")
        print("="*60)

        return text.strip()
        """

@app.post("/transcribe")
async def transcribe_endpoint(audio: UploadFile = File(...)):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        webm = tmp / "x.webm"
        webm.write_bytes(await audio.read())

        text = transcribe_webm_segment(webm, "segment", True)
        text = clean_transcript(text)
        Path("latest_transcript.txt").write_text(text, encoding="utf-8")
        html_path = generate_meeting_report(text)
        return {
            "ok": True,
            "transcript": text,
            "html_path": html_path
        }



# =========================================================
# ZOOM BOT WebM → TRANSCRIBE (WAV YOK)
# =========================================================

def generate_timeline_hint(start_time: float, duration: float) -> str:
    """Speaker timeline'dan zaman çizelgesi oluşturur (JSONL ve JSON destekli)"""
    try:
        data = []
        
        # ÖNCE speaker_timeline.jsonl'ı dene (JSONL format - Zoom/Teams için)
        jsonl_path = Path("speaker_timeline.jsonl")
        if jsonl_path.exists():
            try:
                lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
                for line in lines:
                    if line.strip():
                        try:
                            entry = json.loads(line)
                            # JSONL formatında 'ts' kullanıyoruz
                            if "ts" in entry:
                                data.append({
                                    "timestamp": entry["ts"],
                                    "speakers": entry.get("speakers", [])
                                })
                        except: pass
                print(f"[TIMELINE] {len(data)} satır speaker_timeline.jsonl'dan okundu")
            except Exception as e:
                print(f"[WARN] JSONL okuma hatası: {e}")
        
        # Eğer JSONL boşsa, speaker_activity_log.json'ı dene (legacy)
        if not data:
            log_path = Path("speaker_activity_log.json")
            if log_path.exists():
                try:
                    json_data = json.loads(log_path.read_text(encoding="utf-8"))
                    if isinstance(json_data, list):
                        data = json_data
                        print(f"[TIMELINE] {len(data)} satır speaker_activity_log.json'dan okundu")
                except: pass
        
        if not data:
            return None
            
        end_time = start_time + duration
        relevant_logs = []
        last_speakers = None
        
        for entry in data:
            t = entry.get("timestamp", 0)
            
            # Sadece bu segmentin zaman aralığındaki loglar
            if start_time <= t <= end_time:
                speakers = entry.get("speakers", [])
                
                # Sadece konuşmacı değiştiyse listeye ekle (Dedup)
                if speakers and speakers != last_speakers:
                    rel_seconds = int(t - start_time)
                    if rel_seconds < 0: rel_seconds = 0
                    
                    m, s = divmod(rel_seconds, 60)
                    time_str = f"{m:02d}:{s:02d}"
                    relevant_logs.append(f"- {time_str}: {', '.join(speakers)}")
                    last_speakers = speakers
        
        if not relevant_logs:
            return None
            
        print(f"[TIMELINE] {len(relevant_logs)} görsel tespit eşleştirildi")
        return "\n".join(relevant_logs)
            
    except Exception as e:
        print(f"[WARN] Timeline hint hatası: {e}")
        return None

@app.post("/transcribe-webm")
async def transcribe_webm_endpoint(
    audio: UploadFile = File(...),
    speaker_name: str = Form(None),  # Legacy fallback
    start_time: str = Form(None),    # Yeni timestamp from recorder
    duration: str = Form(None),
    platform: str = Form(None)       # Platform: meet, zoom, teams
):
    """
    WebM/Opus dosyasını transkribe et (direkt WebM üzerinden)
    """
    print("\n" + "="*60)
    print(f"[API] /transcribe-webm endpoint çağrıldı. Speaker: {speaker_name}")
    print("="*60)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            webm = tmp / "zoom.webm"

            # WebM'i kaydet
            print("[UPLOAD] Dosya alınıyor...")
            content = await audio.read()
            webm.write_bytes(content)

            file_size_mb = len(content) / (1024 * 1024)
            print(f"[OK] WebM dosyası alındı: {file_size_mb:.2f} MB")

            if file_size_mb < 0.01:
                print("[ERROR] Dosya çok küçük!")
                return {
                    "ok": False,
                    "error": "WebM dosyası çok küçük (< 0.01 MB)"
                }

            print("\n" + "="*60)
            print("[STEP 1/2] TRANSKRİPSİYON BAŞLIYOR (WEBM)")
            print("="*60)

            # BYPASS: Recorder already encodes at 16k CBR, no need to recompress
            # (Recompression saves only ~80KB but wastes CPU)
            webm_for_model = webm
            print("[INFO] Using original WebM (recorder already optimized at 16k CBR)")

            # 2) Transkripsiyon
            transcript_start = time.time()
            # Timeline Hint Oluştur (Akıllı Diarization)
            timeline_hint = None
            if start_time and duration:
                try:
                    st_float = float(start_time)
                    dur_float = float(duration)
                    timeline_hint = generate_timeline_hint(st_float, dur_float)
                except ValueError:
                    pass

            # 2) Transkripsiyon
            transcript_start = time.time()
            text = transcribe_webm_segment(webm_for_model, "segment", True, speaker_hint=speaker_name, timeline_hint=timeline_hint, platform=platform)
            text = clean_transcript(text)

            transcript_duration = time.time() - transcript_start

            print(f"\n[SUCCESS] ✓ TRANSKRİPSİYON TAMAMLANDI ({transcript_duration:.1f}s)")
            print(f"[STATS] Karakter sayısı: {len(text):,}")

            if not text or len(text) < 10:
                print("[WARN] Transkript çok kısa!")
                return {
                    "ok": False,
                    "error": "Transkript oluşturulamadı veya çok kısa"
                }


                
            # 🔥 TRANSKRİPT DOSYASINI GARANTİLE
            # Önce dosya yolunu tanımla ve yoksa oluştur (Boş bile olsa)
            transcript_file = Path("latest_transcript.txt")
            if not transcript_file.exists():
                transcript_file.touch()

            # Eğer sessizlik döndüyse işlem yapma ama hata da verme
            if not text:
                print("[INFO] Sessizlik/Boş transkript - Kaydedilmedi.")
                return {
                    "ok": True,
                    "transcript": "",
                    "info": "Silence detected"
                }

            # Her segment geldiğinde, önceki transkriptin ÜSTÜNE EKLE (append), üzerine yazma!
            # (transcript_file yukarıda tanımlandı)
            
            if transcript_file.exists():
                # Mevcut transkripti oku
                existing_transcript = transcript_file.read_text(encoding="utf-8")
                
                # Yeni segment'i ekle (ayrıcı ile)
                # 🔥 DEDUPLICATION CHECK: Eğer yeni gelen metin, mevcut metnin son kısmında ZATEN varsa ekleme
                # Window size arttırıldı (1000 -> 15000) çünkü uzun segmentler tekrar edebiliyor
                check_len = 15000
                last_part = existing_transcript[-check_len:] if len(existing_transcript) > check_len else existing_transcript
                
                # Normalizasyon (boşlukları temizle, lowercase)
                norm_text = " ".join(text.lower().split())
                norm_last = " ".join(last_part.lower().split())
                
                # 1. Tam Kapsama Kontrolü (Yeni metin tamamen eski metnin içinde mi?)
                if norm_text in norm_last and len(norm_text) > 30:
                     print(f"[SKIP] Tekrarlayan içerik tespit edildi ({len(text)} chars) - EKLENMEDİ.")
                     return {
                        "ok": True, 
                        "transcript": existing_transcript, 
                        "info": "Duplicate content skipped"
                     }

                # 2. Overlap Kontrolü (Örn: Yeni metnin ilk %50'si eski metnin sonunda varsa)
                # Bu, parça parça tekrarı engeller
                msg_len = len(norm_text)
                if msg_len > 100:
                    first_half = norm_text[:int(msg_len/2)]
                    if first_half in norm_last:
                         print(f"[SKIP] Kısmi tekrar (%50 overlap) tespit edildi - EKLENMEDİ.")
                         return {
                            "ok": True, 
                            "transcript": existing_transcript, 
                            "info": "Partial duplicate skipped"
                         }

                combined_transcript = existing_transcript + "\n\n" + text
                
                print(f"[APPEND] Transkript birleştirildi (önceki: {len(existing_transcript)} → yeni: {len(combined_transcript)} karakter)")
            else:
                # İlk segment, direkt yaz
                combined_transcript = text
                print(f"[NEW] İlk transkript kaydedildi ({len(text)} karakter)")
            
            # Birleştirilmiş transkripti kaydet
            transcript_file.write_text(combined_transcript, encoding="utf-8")

            # ✅ RAPOR OLUŞTURMAYI KALDIRDIK!
            # Rapor sadece bot durdurulunca sistem.py tarafından oluşturulacak
            # Bu sayede her segment için değil, sadece EN SON 1 rapor olacak

            total_duration = transcript_duration
            print("\n" + "="*60)
            print("[DONE] Segment işlendi!")
            print("="*60)
            print(f"[STATS] Segment: {file_size_mb:.2f} MB")
            print(f"[STATS] Toplam transcript: {len(combined_transcript):,} karakter")
            print(f"[STATS] İşlem süresi: {total_duration:.1f}s")
            print("="*60 + "\n")

            return {
                "ok": True,
                "transcript": combined_transcript,
                "transcript_length": len(combined_transcript),
                "segment_length": len(text),
                "webm_size_mb": file_size_mb,
                "processing_time_seconds": total_duration
            }

    except Exception as e:
        print(f"\n[ERROR] İşlem hatası: {e}")
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(e)
        }

        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }


@app.post("/summary")
async def summarize():
    p = Path("latest_transcript.txt")
    if not p.exists():
        return {"ok": False, "error": "Transkript yok"}

    txt = p.read_text(encoding="utf-8")
    if not txt.strip():
        return {"ok": False, "error": "Transkript boş"}
    
    model = genai.GenerativeModel(MODEL_NAME)
    resp = model.generate_content("Toplantıyı maddeler halinde özetle:\n\n" + txt[-12000:])
    return {"ok": True, "summary": resp.text}

@app.post("/clear-worker-error")
async def clear_worker_error():
    """Worker status'taki error alanını temizle (Popup bir kere gösterildikten sonra)."""
    try:
        status_file = Path("data/worker_status.json")
        if status_file.exists():
            status = json.loads(status_file.read_text(encoding="utf-8"))
            if "error" in status:
                del status["error"]
                status_file.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
                print("[OK] Worker error temizlendi")
        return {"ok": True}
    except Exception as e:
        print(f"[ERROR] Worker error temizleme hatası: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/delete-meeting")
async def delete_meeting(payload: dict = Body(...)):
    """
    Toplantı silme endpointi.
    - Supabase'den kaydı siler
    - Diskte bulunan fiziksel dosyaları (PDF, TXT) siler
    """
    try:
        meeting_id = payload.get("meeting_id")
        user_id = payload.get("user_id")
        
        if not meeting_id or not user_id:
            return JSONResponse({"ok": False, "error": "Missing meeting_id or user_id"}, status_code=400)

        print(f"\n[DELETE] Toplantı silme isteği: {meeting_id} (User: {user_id})")

        # 1. Önce kayıt detaylarını çekelim (dosya yollarını öğrenmek için)
        # Direkt client kullanalım veya request yapalım. 
        # Server tarafında supabase client'ı 'db_utils' içinde veya burda tanımlı mı?
        # db_utils.py içinde global client yoksa, environment'dan alıp burda create edelim.
        
        from supabase import create_client, Client
        url: str = os.environ.get("SUPABASE_URL")
        # Service role key kullan (RLS bypass için)
        key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
        print(f"[DEBUG] Using key: {key[:20]}..." if key else "[DEBUG] No key found!")
        supabase: Client = create_client(url, key)

        # Meeting verisini çek
        print(f"[DEBUG] Querying: meeting_id={meeting_id}, user_id={user_id}")
        
        # Debug: Tüm kayıtları listele
        all_res = supabase.table("meetings").select("id, user_id, title").limit(10).execute()
        print(f"[DEBUG] Tablodaki kayıtlar: {[{'id': r.get('id'), 'user': r.get('user_id')[:8] if r.get('user_id') else 'N/A'} for r in all_res.data]}")
        
        res = supabase.table("meetings").select("*").eq("id", meeting_id).eq("user_id", user_id).execute()
        
        # Debug: Eğer bulunamazsa, sadece ID ile dene (user_id kontrolünü atla)
        if not res.data:
            print("[DEBUG] user_id eşleşmedi, sadece ID ile deneniyor...")
            res = supabase.table("meetings").select("*").eq("id", meeting_id).execute()
            if res.data:
                print(f"[DEBUG] Meeting bulundu ama user_id eşleşmiyor. DB user_id: {res.data[0].get('user_id')}")
        
        if not res.data:
            print(f"[DEBUG] Meeting hiç bulunamadı. Gelen ID: {meeting_id}")
            return JSONResponse({"ok": False, "error": "Meeting not found or access denied"}, status_code=404)

        meeting = res.data[0]
        report_path = meeting.get("report_path")
        transcript_path = meeting.get("transcript_path")

        # 2. Fiziksel Dosyaları Sil
        deleted_files = []
        
        # Helper silme fonksiyonu (Web URL -> Local Path çevirme basitçe)
        # Not: Veritabanında "/reports/xxx.pdf" şeklinde kayıtlı. 
        # Bizim mount ettiğimiz dizin "temp_reports".
        
        def delete_local_file(web_path):
            if not web_path: return
            # web_path: /reports/Meeting_Rapor_XYZ.pdf
            # local: temp_reports/Meeting_Rapor_XYZ.pdf
            
            filename = web_path.split("/")[-1]
            local_path = Path("temp_reports") / filename
            
            if local_path.exists():
                try:
                    local_path.unlink()
                    deleted_files.append(filename)
                    print(f"[DELETE] Dosya silindi: {local_path}")
                except Exception as e:
                    print(f"[WARN] Dosya silinemedi: {e}")
            else:
                print(f"[DELETE] Dosya diskte bulunamadı: {local_path}")

        delete_local_file(report_path)
        delete_local_file(transcript_path)

        # 3. Supabase Kaydını Sil
        del_res = supabase.table("meetings").delete().eq("id", meeting_id).execute()
        
        print(f"[DELETE] DB kaydı silindi. ID: {meeting_id}")

        return {
            "ok": True, 
            "deleted_files": deleted_files,
            "db_deleted": True
        }

    except Exception as e:
        print(f"[ERROR] Silme hatası: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# =========================================================
# BOT TASK SYSTEM
# =========================================================
BOT_TASK_FILE = Path("data/bot_task.json")
BOT_COMMAND_FILE = Path("data/bot_command.json")

def parse_zoom_link(meeting_input: str):
    """
    Zoom meeting ID ve password'u parse et
    
    Desteklenen formatlar:
    - Zoom URL: https://zoom.us/j/1234567890?pwd=abc123
    - Meeting ID: 123 456 7890
    - Meeting ID + Parola: 123 456 7890 Parola: abc123
    """
    meeting_input = meeting_input.strip()
    meeting_id = ""
    pwd = ""

    print(f"[DEBUG] Parse input: {meeting_input[:100]}")  

    if "zoom.us" in meeting_input or "zoommtg://" in meeting_input:
        try:
            url = urlparse(meeting_input)
            qs = parse_qs(url.query)
            pwd = qs.get("pwd", [""])[0]

            parts = url.path.split("/")
            for i, p in enumerate(parts):
                if p in ("j", "join") and i+1 < len(parts):
                    meeting_id = "".join(ch for ch in parts[i+1] if ch.isdigit())
                    break
                    
            print(f"[DEBUG] URL parse - ID: {meeting_id}, PWD: {pwd}")
        except Exception as e:
            print(f"[WARN] URL parse hatasi: {e}")
    
    if not meeting_id:
        if "Toplantı Kimliği:" in meeting_input or "Meeting ID:" in meeting_input:
            parts = re.split(r'Toplantı Kimliği:|Meeting ID:', meeting_input, flags=re.IGNORECASE)
            if len(parts) >= 2:
                id_section = parts[1].split("Parola:")[0].split("Password:")[0]
                meeting_id = "".join(ch for ch in id_section if ch.isdigit())
        
        if not meeting_id:
            meeting_id = "".join(ch for ch in meeting_input if ch.isdigit())
        
        if "Parola:" in meeting_input or "Password:" in meeting_input:
            parts = re.split(r'Parola:|Password:', meeting_input, flags=re.IGNORECASE)
            if len(parts) >= 2:
                pwd_section = parts[1].strip()
                pwd = re.split(r'\s+|---', pwd_section)[0].strip()

    if not meeting_id or len(meeting_id) < 9:
        print(f"[ERROR] Gecersiz Meeting ID: '{meeting_id}' (uzunluk: {len(meeting_id)})")
        return None, None

    if len(meeting_id) > 11:
        meeting_id = meeting_id[:11]

    print(f"[PARSE OK] Meeting ID: {meeting_id}")
    print(f"[PARSE OK] Password: {pwd if pwd else '(yok)'}")
    
    return meeting_id, pwd

# =========================================================
# START BOT
# =========================================================
@app.post("/start-bot")
async def start_bot(payload: dict = Body(...)):
    """
    Multi-platform bot başlatıcı (Zoom / Teams / Meet)
    
    Body:
        platform: "zoom" | "teams" | "meet" (default: "zoom")
        meeting_url: Toplantı linki veya ID
        title: Toplantı başlığı (opsiyonel)
    """
    platform = payload.get("platform", "zoom").lower()
    meeting_url = payload.get("meeting_url", "").strip()
    title = payload.get("title", "").strip()
    user_id = payload.get("user_id", "").strip() # Frontend'den gelecek
    manual_password = payload.get("password", "").strip() # YENI: Manuel şifre
    
    # Platform kontrolü
    if platform not in ["zoom", "teams", "meet"]:
        return {"ok": False, "error": f"Desteklenmeyen platform: {platform}"}
    
    if not meeting_url:
        return {"ok": False, "error": "meeting_url boş olamaz"}
    
    # Bot ismi sabit
    bot_name = "Sesly Bot"
    


    # Eski verileri temizle (Stale transcript önlemek için)
    try:
        Path("latest_transcript.txt").unlink(missing_ok=True)
        Path("live_transcript_cache.json").unlink(missing_ok=True)
        
        # Temp reports temizle
        temp_dir = Path("temp_reports")
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            temp_dir.mkdir()
            
        print("[CLEANUP] Eski transkript ve raporlar temizlendi.")
    except Exception as e:
        print(f"[WARN] Temizlik hatası: {e}")

    # Platform'a göre task oluştur
    task = {
        "active": True,
        "platform": platform,
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "title": title or f"{platform.capitalize()} Toplantısı",
        "user_id": user_id,
        "timestamp": time.time()
    }
    
    # Zoom için ek parsing
    if platform == "zoom":
        meeting_id, pwd = parse_zoom_link(meeting_url)
        if not meeting_id:
            return {"ok": False, "error": "Zoom Meeting ID bulunamadı"}
        
        task["meeting_id"] = meeting_id
        # Manuel şifre varsa onu kullan, yoksa linkten geleni (pwd) kullan
        task["passcode"] = manual_password if manual_password else pwd
    else:
        # Teams ve Meet için meeting_url yeterli
        task["meeting_id"] = ""
        task["passcode"] = ""
    
    # Task'i kaydet (data/ klasöründe)
    BOT_TASK_FILE.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    
    print(f"[{platform.upper()}] Yeni görev oluşturuldu:", task)
    
    return {
        "ok": True,
        "platform": platform,
        "meeting_url": meeting_url,
        "bot_id": task.get("meeting_id", meeting_url[:20]),
        "message": f"{platform.capitalize()} toplantısına katılma görevi oluşturuldu"
    }

# =========================================================
# BOT STATUS
# =========================================================
@app.get("/bot-status")
async def bot_status():
    """
    Multi-platform bot durumu (Zoom / Teams / Meet)
    
    Returns:
        task: Aktif görev bilgisi
        worker: Worker durumu
    """
    try:
        # Task bilgisini oku
        if not BOT_TASK_FILE.exists():
            return {"task": {"active": False}, "worker": {}}

        task = json.loads(BOT_TASK_FILE.read_text(encoding="utf-8"))

        # Worker status'u oku (data/ klasöründen)
        worker_status_file = Path("data/worker_status.json")
        if worker_status_file.exists():
            worker = json.loads(worker_status_file.read_text(encoding="utf-8"))
            # STALE CHECK KALDIRILDI: Kullanıcı isteği üzerine.
            # Worker bir yerde takılsa bile UI "Running" kalsın.
        else:
            worker = {"running": False, "recording": False}

        # Platform bilgisini ekle
        platform = task.get("platform", "zoom")
        
        # Transkript kontrolü
        transcript_file = Path("latest_transcript.txt")
        has_transcript = False
        if transcript_file.exists():
            try:
                # Sadece var olması yetmez, içi dolu olmalı
                content = transcript_file.read_text(encoding="utf-8").strip()
                if len(content) > 10:  # En az 10 karakter olsun
                    has_transcript = True
            except:
                pass

        return {
            "task": task,
            "worker": {
                "platform": platform,
                "running": worker.get("running", False),
                "recording": worker.get("recording", False),
                "status_message": worker.get("status_message", ""),
                "paused": worker.get("paused", False),
                "transcript_ready": has_transcript
            }
        }

    except Exception as e:
        return {"task": {"active": False}, "worker": {}, "error": str(e)}

# =========================================================
# BOT COMMAND SYSTEM
# =========================================================
def save_bot_command(command: str, data: dict = None):
    cmd = {
        "command": command,
        "timestamp": time.time(),
        "data": data or {},
        "processed": False
    }
    BOT_COMMAND_FILE.write_text(json.dumps(cmd, ensure_ascii=False), encoding="utf-8")


@app.post("/bot-command")
async def bot_command(payload: dict = Body(...)):
    command = payload.get("command")
    
    if command not in ["pause", "resume", "stop", "summary"]:
        return {"ok": False, "error": "Geçersiz komut"}
    
    if command == "summary":
        p = Path("latest_transcript.txt")
        if not p.exists() or not p.read_text(encoding="utf-8").strip():
            return {"ok": False, "error": "Henüz transkript yok"}
        
        txt = p.read_text(encoding="utf-8")
        model = genai.GenerativeModel(MODEL_NAME)
        
        try:
            prompt = f"""
            Aşağıdaki toplantı transkriptini analiz et ve profesyonel bir "Ara Özet Raporu" oluştur.
            
            Rapor Formatı şu şekilde olmalı:
            
            📋 **TOPLANTI ÖZETİ**
            
            **📌 Gündem/Konu:**
            (Toplantının ana konusunu 1 cümle ile yaz)
            
            **🗣️ Konuşulan Ana Başlıklar:**
            * (Madde madde önemli tartışma noktaları)
            * ...
            
            **✅ Alınan Kararlar (Varsa):**
            * (Varsa netleşen kararlar, yoksa "Henüz karar alınmadı" yaz)
            
            **📝 Aksiyonlar/Görevler (Varsa):**
            * (Kim ne yapacak? Örn: "Ahmet: Raporu hazırlayacak")
            
            ---
            **Transkript:**
            {txt[-15000:]}
            """
            
            resp = model.generate_content(prompt)
            return {"ok": True, "summary": resp.text}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    save_bot_command(command)
    
    # STOP KOMUTU GELDİYSE: Worker kendi raporunu oluşturacak, burada YAPMA!
    # NOT: Önceden burada generate_meeting_report() çağrılıyordu ama bu
    # worker'daki rapor üretimiyle çakışarak ÇİFT RAPOR oluşturuyordu.
    # Rapor üretimi sadece worker'da yapılmalı (teams_web_worker.py / zoom_web_worker.py)
    if command == "stop":
        print("[STOP] Komut alındı. Rapor worker tarafından oluşturulacak.")
    
    messages = {
        "pause": "Kayıt duraklatma komutu gönderildi",
        "resume": "Kayıt devam ettirme komutu gönderildi",
        "stop": "Bot durdurma komutu gönderildi"
    }
    
    return {"ok": True, "message": messages.get(command, "Komut gönderildi")}

@app.post("/force-reset")
async def force_reset():
    """
    Sistemi zorla sıfırla - Tüm işlemleri durdur ve temizle
    Kullanım: Toplantı sonrası sistem kilitlendiyse
    """
    print("\n" + "="*60)
    print("[API] FORCE RESET çağrıldı")
    print("="*60)
    
    try:
        command = {
            "command": "force_reset",
            "timestamp": time.time(),
            "data": {},
            "processed": False
        }
        BOT_COMMAND_FILE.write_text(
            json.dumps(command, ensure_ascii=False),
            encoding="utf-8"
        )
        print("[OK] Worker'a force_reset komutu gönderildi")
        
        # ZORLA KAPATMADAN ÖNCE: Kurtarabildiğin veriyi kurtar
        try:
             p = Path("latest_transcript.txt")
             if p.exists():
                 text = p.read_text(encoding="utf-8").strip()
                 if len(text) > 50:
                     print(f"[RESET] Sıfırlama öncesi veri kurtarılıyor... ({len(text)} karakter)")
                     report_path, report_url = generate_meeting_report(text)
                     if report_path and report_url:
                         save_to_supabase(report_path, report_url, text)
        except Exception as e:
            print(f"[ERROR] Reset raporlama hatası: {e}")

        files_to_clean = [
            "data/bot_task.json",
            "data/bot_command.json",
            "data/worker_status.json",
            "participants.json",
            "current_meeting_participants.json",
            "speaker_activity_log.json",
            "live_transcript_cache.json",
            "latest_transcript.txt"
        ]
        
        cleaned_count = 0
        for filename in files_to_clean:
            filepath = Path(filename)
            if filepath.exists():
                try:
                    filepath.unlink()
                    cleaned_count += 1
                    print(f"[CLEAN] {filename} silindi")
                except Exception as e:
                    print(f"[WARN] {filename} silinemedi: {e}")
        
        empty_task = {
            "active": False,
            "meeting_id": "",
            "passcode": "",
            "bot_name": "Sesly Bot",  # SABİT DEĞER
            "timestamp": time.time()
        }
        BOT_TASK_FILE.write_text(
            json.dumps(empty_task, ensure_ascii=False),
            encoding="utf-8"
        )
        print("[OK] bot_task.json sıfırlandı")
        
        reset_status = {
            "zoom_running": False,
            "recording": False,
            "paused": False,
            "status_message": "Sistem sıfırlandı - Yeni toplantı için hazır",
            "timestamp": time.time()
        }
        Path("data/worker_status.json").write_text(
            json.dumps(reset_status, ensure_ascii=False),
            encoding="utf-8"
        )
        print("[OK] worker_status.json güncellendi")
        
        import psutil
        killed_procs = 0
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or [])
                
                if any(script in cmdline for script in [
                    'zoom_bot_recorder.py',
                    'zoom_vision_monitor.py'
                ]):
                    print(f"[KILL] Process durduruluyor: {proc.info['name']} (PID: {proc.pid})")
                    proc.kill()
                    killed_procs += 1
            except:
                pass
        
        if killed_procs > 0:
            print(f"[OK] {killed_procs} Python process durduruldu")
        
        try:
            os.system("taskkill /F /IM Zoom.exe 2>nul")
            print("[OK] Zoom kapatıldı")
        except:
            pass
        
        print("\n" + "="*60)
        print("[SUCCESS] Force reset tamamlandı")
        print(f"[STATS] {cleaned_count} dosya temizlendi")
        print(f"[STATS] {killed_procs} process durduruldu")
        print("="*60 + "\n")
        
        return {
            "ok": True,
            "message": "Sistem zorla sıfırlandı",
            "cleaned_files": cleaned_count,
            "killed_processes": killed_procs,
            "status": "ready"
        }
        
    except Exception as e:
        print(f"[ERROR] Force reset hatası: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }


@app.get("/bot-command-status")
async def bot_command_status():
    if not BOT_COMMAND_FILE.exists():
        return {"command": None}
    
    try:
        cmd = json.loads(BOT_COMMAND_FILE.read_text(encoding="utf-8"))
        return cmd
    except:
        return {"command": None}
# =========================================================
# DOWNLOAD REPORT
# =========================================================
@app.get("/download-report")
async def download_report():
    """En son PDF raporunu indir (eski endpoint - yeni /download-pdf kullanın)"""
    # Yeni endpoint'e yönlendir
    return await download_pdf()


@app.get("/live-transcript")
async def get_live_transcript():
    """Canlı transkript cache'ini döndür"""
    cache_file = Path("live_transcript_cache.json")
    
    if not cache_file.exists():
        return {"ok": False, "error": "Henuz transkript yok"}
    
    try:
        data = json.loads(cache_file.read_text(encoding='utf-8'))
        return {
            "ok": True,
            "segments": data.get("segments", []),
            "total_blocks": data.get("total_blocks", 0),
            "last_update": data.get("last_update", 0),
            "recording_start": data.get("recording_start", "")
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/latest-pdf")
async def get_latest_pdf():
    """En yeni Raporu (PDF veya HTML) döndür"""
    try:
        temp_dir = Path("temp_reports")
        if not temp_dir.exists():
            return {"ok": False, "error": "Rapor dizini bulunamadı"}
        
        # Hem PDF hem HTML ara
        files = list(temp_dir.glob("Toplanti_Raporu_*"))
        
        # Sadece .pdf ve .html al
        valid_files = [f for f in files if f.suffix in ['.pdf', '.html']]
        
        if not valid_files:
            return {"ok": False, "error": "Rapor bulunamadı"}
            
        # En yeniye göre sırala
        latest_file = sorted(valid_files, key=lambda x: x.stat().st_mtime, reverse=True)[0]
        
        return {
            "ok": True, 
            "pdf_path": str(latest_file),
            "type": "html" if latest_file.suffix == '.html' else "pdf"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/download-pdf")
async def download_pdf():
    """En yeni Raporu indir"""
    try:
        temp_dir = Path("temp_reports")
        if not temp_dir.exists():
            return JSONResponse(status_code=404, content={"ok": False, "error": "Rapor dizini yok"})
        
        files = list(temp_dir.glob("Toplanti_Raporu_*"))
        valid_files = [f for f in files if f.suffix in ['.pdf', '.html']]
        
        if valid_files:
            # En yenisi
            latest = sorted(valid_files, key=lambda x: x.stat().st_mtime, reverse=True)[0]
            
            media_type = "text/html" if latest.suffix == ".html" else "application/pdf"
            
            return FileResponse(
                path=str(latest),
                media_type=media_type,
                filename=latest.name,
                headers={
                    "Content-Disposition": f'attachment; filename="{latest.name}"'
                }
            )
        
        return JSONResponse(status_code=404, content={"ok": False, "error": "Rapor bulunamadı"})
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)}
        )

@app.get("/download-transcript")
async def download_transcript():
    """En yeni transkripti indir"""
    try:
        transcript_file = Path("latest_transcript.txt")
        if not transcript_file.exists():
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": "Transkript bulunamadı"}
            )
        
        return FileResponse(
            path=str(transcript_file),
            media_type="text/plain",
            filename="transcript.txt",
            headers={
                "Content-Disposition": 'attachment; filename="transcript.txt"'
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)}
        )


# Static files (CSS, JS, images)
app.mount("/assets", StaticFiles(directory="web_arayuz/assets"), name="assets")
app.mount("/sesly_logo", StaticFiles(directory="web_arayuz/sesly_logo"), name="logos")

# HTML sayfa route'ları
@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/admin")
async def admin_page(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/meetings")
async def meetings_page(request: Request):
    return templates.TemplateResponse("meetings.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/calendar")
async def calendar_page(request: Request):
    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/dashboard")
async def dashboard_page(request: Request):
    return templates.TemplateResponse("user-dashboard.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/profile")
async def user_profile(request: Request):
    return templates.TemplateResponse("user-profile.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.get("/meeting-detail")
async def meeting_detail(request: Request):
    return templates.TemplateResponse("meeting-detail.html", {
        "request": request,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_KEY
    })

@app.post("/delete-account")
async def delete_account_endpoint(payload: dict = Body(...)):
    user_id = payload.get("user_id")
    if not user_id:
        return {"ok": False, "error": "User ID gerekli"}

    success = delete_user_account(user_id)
    if success:
        return {"ok": True, "message": "Hesap silindi"}
    else:
        return {"ok": False, "error": "Silme işlemi başarısız"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",  
        host="127.0.0.1",
        port=9000,
        reload=True
    )


# çalıştırmak için: server.py çalıştır yeni terminalde sistem.py çalıştır
# python sistem.py