import sys
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

from logger_config import setup_logger
logger = setup_logger(__name__, "recorder.log")

import subprocess
import requests
import tempfile
import time
import psutil
import json
from pathlib import Path

# Platform abstraction
from platform_utils import (
    IS_WINDOWS, IS_LINUX, 
    get_audio_device, get_audio_device_for_ffmpeg, 
    get_ffmpeg_path, setup_display
)

# Linux'ta display'i ayarla
setup_display()

from dotenv import load_dotenv
load_dotenv(override=True)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("[WARN] GEMINI_API_KEY bulunamadı! Transkripsiyon devre dışı.")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Cross-platform audio device
VAC_DEVICE_NAME = get_audio_device()

# Cross-platform FFmpeg path
FFMPEG_PATH = get_ffmpeg_path()

# Docker'da worker→api iletişimi: servis adı "api" kullanılmalı
api_host = os.getenv("API_HOST", "127.0.0.1")  # Docker: "api", Local: "127.0.0.1"
api_port = os.getenv("API_PORT", os.getenv("PORT", "9000"))
SERVER_URL = f"http://{api_host}:{api_port}/transcribe-webm"

VISION_MONITOR_ENABLED = False

logger.info("[RECORDER] Sesly Bot - WebM/Opus kaydedici başlatıldı...")
logger.info(f"[CONFIG] Device: {VAC_DEVICE_NAME}")
logger.info(f"[CONFIG] Format: WebM (Opus codec)")
logger.info(f"[CONFIG] Vision Monitor: {'AKTIF' if VISION_MONITOR_ENABLED else 'KAPALI'}")

# ------------------------------------------------------------
# SEGMENT KLASÖRÜ ve PATTERN
# ------------------------------------------------------------
segment_dir = Path(tempfile.gettempdir()) / "zoom_segments"
segment_dir.mkdir(exist_ok=True)

chunk_pattern = str(segment_dir / "chunk_%03d.webm")
logger.info(f"[INFO] Segment klasörü: {segment_dir}")

ffmpeg_process = None
recording_active = True
cleanup_done = False
recording_start_time = None
uploaded_chunks = set()


def get_current_speaker():
    """Vision monitor veya Worker'dan güncel konuşmacıyı al"""
    try:
        speaker_log_file = Path("speaker_activity_log.json")

        if not speaker_log_file.exists():
            return None

        data = json.loads(speaker_log_file.read_text(encoding='utf-8'))
        
        # DÜZELTME: Farklı formatları destekle
        # Format 1: Liste (Meet/Zoom worker'ın yazdığı)
        if isinstance(data, list) and len(data) > 0:
            # Son kaydı al
            last_entry = data[-1]
            # Meet/Zoom: 'speakers' key'i
            # Teams: 'current_speakers' key'i
            speakers = last_entry.get('speakers') or last_entry.get('current_speakers', [])
            if speakers and len(speakers) > 0:
                return speakers[0]
        
        # Format 2: Dict (eski format)
        elif isinstance(data, dict):
            current_speakers = data.get('speakers') or data.get('current_speakers', [])
            if current_speakers and len(current_speakers) > 0:
                return current_speakers[0]

        return None

    except Exception as e:
        logger.debug(f"Speaker log okuma hatası: {e}")
        return None





# ============================================================
# FFMPEG İLE WebM SEGMENT KAYIT
# ============================================================

def start_ffmpeg_recording():
    """
    ffmpeg ile VAC cihazından segment bazlı WebM/Opus kayıt başlat

    Returns:
        subprocess.Popen: ffmpeg process
    """
    global recording_start_time
    
    # ------------------------------------------------------------
    # 🔥 AGRESIF TEMİZLİK: Eski segment'leri zorla temizle
    # ------------------------------------------------------------
    old_segments = list(segment_dir.glob("*.webm"))
    
    if old_segments:
        logger.info(f"[CLEANUP] {len(old_segments)} eski segment bulundu, temizleniyor...")
        
        # Eski ffmpeg process'lerini öldür
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] and 'ffmpeg' in proc.info['name'].lower():
                        # Segment klasörüne yazıyorsa öldür
                        if proc.info['cmdline'] and any(str(segment_dir) in str(arg) for arg in proc.info['cmdline']):
                            logger.info(f"[KILL] Eski ffmpeg process öldürülüyor (PID: {proc.info['pid']})")
                            proc.kill()
                            time.sleep(0.5)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.info(f"[WARN] Process cleanup hatası: {e}")
        
        # Şimdi dosyaları sil
        cleaned = 0
        for old in old_segments:
            try:
                old.unlink()
                cleaned += 1
            except Exception as e:
                # Son çare: Windows komutunu kullan
                try:
                    # subprocess zaten global import edilmiş, tekrar import etme!
                    subprocess.run(['cmd', '/c', 'del', '/F', '/Q', str(old)], 
                                 capture_output=True, timeout=2)
                    cleaned += 1
                except Exception:
                    logger.info(f"[WARN] {old.name} silinemedi, göz ardı edilecek")
        
        logger.info(f"[CLEANUP] {cleaned}/{len(old_segments)} eski segment temizlendi")

    logger.info("\n" + "=" * 60)
    logger.info("[FFMPEG] Segment bazlı WebM kayıt başlatılıyor...")
    logger.info("=" * 60)

    # Platform-specific FFmpeg komutu
    if IS_WINDOWS:
        # Windows: DirectShow + VB-Cable
        cmd = [
            FFMPEG_PATH,
            "-f", "dshow",
            "-rtbufsize", "1G",
            "-thread_queue_size", "4096",
            "-use_wallclock_as_timestamps", "1",
            "-i", f"audio={VAC_DEVICE_NAME}",
        ]
    else:
        # Linux: PulseAudio virtual sink
        cmd = [
            FFMPEG_PATH,
            "-f", "pulse",
            "-i", "virtual_mic.monitor",  # docker-entrypoint.sh'da oluşturuluyor
        ]
    
    # Common encoding options
    cmd.extend([
        "-vn", "-sn", "-dn",        # Video/Subtitle/Data OFF

        # Opus 16k CBR
        "-c:a", "libopus",
        "-b:a", "16k",
        "-vbr", "off",
        "-compression_level", "10",
        "-application", "voip",
        "-ac", "1",
        "-ar", "16000",

        # Timestamp fix
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts",
        "-af", "aresample=async=1", # Audio sync fix

        # Segmentation
        "-f", "segment",
        "-segment_time", "300", # 5 dakika
        "-break_non_keyframes", "1",
        "-reset_timestamps", "1",
        "-segment_format", "webm",
        
        chunk_pattern
    ])

    logger.info("[CMD] ffmpeg komutu:")
    logger.info(f"  {' '.join(cmd)}")
    logger.info("-" * 60)

    try:
        # ffmpeg stdout'u tamamen kapat, sadece HATALARI kaydet
        log_file = open(Path("logs/ffmpeg_debug.log"), "w", encoding="utf-8")

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,   # ← stdout artık asla yazmıyor
            stderr=log_file,             # ← sadece HATALAR yazılıyor
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        )

        recording_start_time = time.time()
        time.sleep(3)


        if process.poll() is None:
            logger.info("[SUCCESS] ✓ ffmpeg segment kaydı başladı!")
            logger.info(f"[INFO] Process ID: {process.pid}")
            logger.info("=" * 60 + "\n")
            return process
        else:
            logger.info("[ERROR] ffmpeg hemen kapandı!")
            return None

    except Exception as e:
        logger.info(f"[ERROR] ffmpeg başlatma hatası: {e}")
        return None


def stop_ffmpeg_recording(process):
    """
    ffmpeg kaydını durdur (Windows için güvenli kapanış)
    """
    if process is None or process.poll() is not None:
        logger.info("[WARN] ffmpeg zaten durmuş")
        return False

    logger.info("\n" + "=" * 60)
    logger.info("[FFMPEG] Kayıt durduruluyor...")
    logger.info("=" * 60)

    # Mevcut segment'leri logla
    try:
        existing_segments = list(segment_dir.glob("chunk_*.webm"))
        total_size = sum(s.stat().st_size for s in existing_segments) / (1024 * 1024)
        logger.info(f"[INFO] Kapatılmadan önce {len(existing_segments)} segment mevcut ({total_size:.2f} MB)")
    except Exception:
        pass

    try:
        logger.info("[ACTION] ffmpeg'e 'q' komutu gönderiliyor (graceful shutdown)...")
        
        # ffmpeg'e stdin üzerinden 'q' gönder (düzgün kapanış)
        try:
            process.stdin.write(b'q')
            process.stdin.flush()
            logger.info("[SENT] 'q' komutu gönderildi")
        except (BrokenPipeError, ValueError, OSError) as e:
            logger.info(f"[WARN] stdin write hatası: {e}, SIGTERM kullanılacak")
            process.terminate()
        except Exception as e:
            logger.info(f"[ERROR] Beklenmeyen stdin hatası: {e}, SIGTERM kullanılacak")
            process.terminate()

        # 🔥 KRİTİK: Timeout'u 60 saniyeye çıkar (segment'ler kapatılsın)
        logger.info("[WAIT] ffmpeg segment'leri kapatıyor (max 60s)...")
        process.wait(timeout=60)

        logger.info("[SUCCESS] ✓ ffmpeg düzgün şekilde kapandı")
        
        # Kapandıktan sonra segment sayısını tekrar logla
        try:
            final_segments = list(segment_dir.glob("chunk_*.webm"))
            final_size = sum(s.stat().st_size for s in final_segments) / (1024 * 1024)
            logger.info(f"[INFO] Kapandıktan sonra {len(final_segments)} segment ({final_size:.2f} MB)")
        except Exception:
            pass
        
        return True

    except subprocess.TimeoutExpired:
        logger.info("[TIMEOUT] ffmpeg 60 saniyede kapanmadı → kill() çağırılıyor")
        process.kill()
        try:
            process.wait(timeout=5)
        except Exception:
            pass
        return False

    except Exception as e:
        logger.info(f"[ERROR] ffmpeg durdurma hatası: {e}")
        try:
            process.kill()
        except Exception:
            pass
        return False


# ============================================================
# LIVE UPLOAD WORKER
# ============================================================

def get_audio_duration(path: Path) -> float:
    """ffprobe ile ses süresini saniye olarak al"""
    try:
        ffprobe_path = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe") if IS_WINDOWS else FFMPEG_PATH.replace("ffmpeg", "ffprobe")
        cmd = [
            ffprobe_path, "-v", "quiet", "-print_format", "json",
            "-show_format", str(path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        info = json.loads(result.stdout or "{}")
        return float(info.get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0

def upload_single_segment(seg_path: Path) -> bool:
    """Tek bir segmenti sunucuya yükle"""
    global uploaded_chunks
    
    if seg_path.name in uploaded_chunks:
        return True

    # Validasyon
    if not is_valid_chunk(seg_path):
        return False

    size_mb = seg_path.stat().st_size / (1024 * 1024)
    file_mtime = seg_path.stat().st_mtime
    
    # Süreyi ve Başlangıç Zamanını Hesapla
    duration = get_audio_duration(seg_path)
    start_time = file_mtime - duration if duration > 0 else 0
    
    # "Speaker Timeline" log dosyasından (speaker_activity_log.json)
    # Geçici logic: Server tarafında daha detaylı yapılacak ama burada da basit bir check kalsın
    detected_speaker = None
    try:
        log_file = Path("speaker_activity_log.json") # DÜZELTME: Doğru dosya ismi
        if log_file.exists():
            data = json.loads(log_file.read_text(encoding="utf-8"))
            # Data bir list olmalı
            if isinstance(data, list):
                # Bu dosyanın zamanına en yakın logu bul
                closest_diff = 9999
                for entry in data[-50:]: # Son 50 loga bak
                    try:
                        t = entry.get("timestamp", 0)
                        speakers = entry.get("speakers", [])
                        # Log zamanı, dosyanın başlangıç ve bitişi arasında mı?
                        # Veya bitişine yakın mı?
                        if abs(t - file_mtime) < 10: # 10 saniye tolerans
                            detected_speaker = speakers[0] if speakers else None
                            break
                    except: pass
    except Exception: pass

    # Platform tespiti (meet, zoom, teams)
    platform = None
    try:
        participants_file = Path("current_meeting_participants.json")
        if participants_file.exists():
            pdata = json.loads(participants_file.read_text(encoding="utf-8"))
            platform = pdata.get("platform")
    except: pass

    logger.info(f"[LIVE-UPLOAD] {seg_path.name} ({size_mb:.2f} MB) gönderiliyor... (Start: {start_time:.0f}, Platform: {platform})")

    with open(seg_path, "rb") as f:
        files = {"audio": (seg_path.name, f, "audio/webm")}
        # METADATA GÖNDER
        data = {
            "start_time": str(start_time),  # String olarak gönder
            "duration": str(duration)
        }
        if detected_speaker:
            data["speaker_name"] = detected_speaker
        if platform:
            data["platform"] = platform
            
        try:
            r = requests.post(SERVER_URL, files=files, data=data, timeout=300)
            if r.status_code == 200:
                logger.info(f"[SUCCESS] {seg_path.name} yüklendi!")
                uploaded_chunks.add(seg_path.name)
                return True
            else:
                logger.info(f"[ERROR] {seg_path.name} HTTP {r.status_code}")
        except Exception as e:
            logger.info(f"[ERROR] Upload hatası: {e}")
            
    return False
            
    return False

def process_live_queue():
    """Biten segmentleri bul ve hemen yükle"""
    global segment_dir
    
    try:
        # Tüm segmentleri al, isme göre sırala (tarih sırası)
        all_segments = sorted(segment_dir.glob("chunk_*.webm"))
        
        # DEBUG: Segment sayısını logla
        if len(all_segments) > 0:
            try: logger.debug(f"[DEBUG] Live Check: {len(all_segments)} segment bulundu: {[s.name for s in all_segments]}")
            except Exception: pass

        # Eğer 2'den az dosya varsa (biri yazılıyor), işlem yapma
        if len(all_segments) < 2:
            return

        # SON dosya hariç diğerleri bitmiş demektir
        # Çünkü ffmpeg sırayla yazar (001, 002...)
        # En sonuncusu (aktif olan) hariç hepsini yükle
        finished_segments = all_segments[:-1]
        
        for seg in finished_segments:
            if seg.name not in uploaded_chunks:
                success = upload_single_segment(seg)
                if success:
                    # Yüklendiyse sil (yer kaplamasın)
                    try:
                        seg.unlink()
                        logger.info(f"[CLEAN] {seg.name} silindi (Live Mode)")
                    except Exception: pass
                    
    except Exception as e:
        logger.info(f"[WARN] Live queue hatası: {e}")

# ============================================================
# FINAL WebM SEGMENTLERİNİ GÖNDERME
# ============================================================

def is_valid_chunk(path: Path) -> bool:
    """
    WebM segmentinin bozuk / boş olup olmadığını kontrol eder.
    - Çok küçük dosyaları,
    - Süresi çok kısa olanları,
    - Paket (cluster) sayısı çok az olanları eler.
    """
    # 1) Boyut kontrolü - 🔥 GEVŞETME: 50 KB → 20 KB (kısa segment'ler için)
    size_kb = path.stat().st_size / 1024
    if size_kb < 20:  # 20 KB altı (yaklaşık 10 saniye)
        logger.info(f"[SKIP] {path.name} → Çok küçük ({size_kb:.1f} KB)")
        return False

    # 2) ffprobe ile duration kontrolü
    try:
        ffprobe_path = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe") if IS_WINDOWS else FFMPEG_PATH.replace("ffmpeg", "ffprobe")
        cmd = [
            ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        info = json.loads(result.stdout or "{}")

        duration = float(info.get("format", {}).get("duration", 0.0))

        # 🔥 GEVŞETME: 0.5s → 0.3s
        if duration < 0.3:
            # WebM/Opus segment dosyalarında ffprobe duration yanlış dönebilir
            # Dosya boyutu > 100KB ise yine de gönder (gerçek ses var)
            if size_kb > 100:
                logger.info(f"[WARN] {path.name} → ffprobe duration={duration:.3f}s ama boyut={size_kb:.0f}KB, yine de gönderilecek")
            else:
                logger.info(f"[SKIP] {path.name} → Duration çok kısa ({duration:.3f} sn)")
                return False

        # 3) Paket (cluster) sayısı kontrolü
        streams = info.get("streams", [])
        if streams:
            stream0 = streams[0]
            if "nb_read_packets" in stream0:
                packets = int(stream0["nb_read_packets"])
                if packets < 2:
                    logger.info(f"[SKIP] {path.name} → Bozuk WebM (paket sayısı çok az: {packets})")
                    return False

    except subprocess.TimeoutExpired:
        logger.info(f"[WARN] {path.name} → ffprobe timeout, yine de gönderilecek")
        return True  # 🔥 Timeout durumunda skip etme
    except Exception as e:
        logger.info(f"[WARN] {path.name} → ffprobe hatası: {e}, yine de gönderilecek")
        return True  # 🔥 Hata durumunda skip etme (false positive önleme)

    return True

def send_final_webm():
    """Segmentleri backend'e gönder"""
    global recording_active, ffmpeg_process, cleanup_done, recording_start_time

    logger.info("\n" + "=" * 60)
    logger.info("[FINALIZE] Segmentler backend'e gönderiliyor...")
    logger.info("=" * 60)

    if cleanup_done:
        logger.info("[INFO] Cleanup zaten yapılmış, tekrar çalıştırılmayacak")
        return

    cleanup_done = True
    recording_active = False



    # 2. ffmpeg'i durdur
    if ffmpeg_process:
        stop_ffmpeg_recording(ffmpeg_process)
        ffmpeg_process = None

    # 3.  SADECE YENİ OLUŞTURULAN SEGMENT'LERİ LİSTELE
    # Kayıt başladıktan SONRA oluşturulan dosyaları al
    all_segments = sorted(segment_dir.glob("chunk_*.webm"))
    
    if recording_start_time:
        # Sadece kayıt başladıktan sonra değiştirilmiş dosyaları al
        segments = []
        for seg in all_segments:
            file_mtime = seg.stat().st_mtime
            if file_mtime >= recording_start_time:
                segments.append(seg)
            else:
                logger.info(f"[SKIP-OLD] {seg.name} eski kayıttan (mtime: {file_mtime:.0f} < start: {recording_start_time:.0f})")
        
        logger.info(f"[FILTER] {len(all_segments)} dosyadan {len(segments)} yeni segment seçildi")
    else:
        segments = all_segments
        logger.info(f"[WARN] recording_start_time yok, tüm dosyalar gönderilecek")

    if not segments:
        logger.info("[ERROR] Hiç segment bulunamadı!")
        return

    logger.info(f"[INFO] {len(segments)} segment bulundu")
    
    logger.info("\n" + "-" * 60)
    logger.info("[STATS] Segment Detayları:")
    logger.info("-" * 60)
    
    total_size_bytes = 0
    total_duration = 0.0
    
    for idx, seg in enumerate(segments, start=1):
        size_mb = seg.stat().st_size / (1024 * 1024)
        total_size_bytes += seg.stat().st_size
        
        # Süreyi al
        try:
            ffprobe_path = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe") if IS_WINDOWS else FFMPEG_PATH.replace("ffmpeg", "ffprobe")
            cmd = [
                ffprobe_path, "-v", "quiet", "-print_format", "json",
                "-show_format", str(seg)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            info = json.loads(result.stdout or "{}")
            duration = float(info.get("format", {}).get("duration", 0.0))
            total_duration += duration
            logger.info(f"  [{idx}] {seg.name}: {size_mb:.2f} MB, {duration:.1f}s")
        except Exception:
            logger.info(f"  [{idx}] {seg.name}: {size_mb:.2f} MB, (süre belirlenemedi)")
    
    logger.info("-" * 60 + "\n")

    # 4. Timeline Verisini Oku (Teams için Speaker Match)
    timeline_data = []
    try:
        timeline_file = Path("speaker_timeline.jsonl")
        if timeline_file.exists():
            lines = timeline_file.read_text(encoding="utf-8").strip().split("\n")
            for line in lines:
                try:
                    timeline_data.append(json.loads(line))
                except Exception: pass
            logger.info(f"[TIMELINE] {len(timeline_data)} satır konuşmacı geçmişi yüklendi")
    except Exception as e:
        logger.info(f"[WARN] Timeline okuma hatası: {e}")

    # 5. Her segmenti sırayla backend'e gönder
    sent_count = 0
    skipped_count = 0
    
    for idx, seg in enumerate(segments, start=1):
        # Zaten yüklendiyse geç (Live mode yüklemiştir)
        if seg.name in uploaded_chunks:
            continue
            
        success = upload_single_segment(seg)
        if success:
            sent_count += 1
        else:
            skipped_count += 1

    # ÖZET İSTATİSTİK
    logger.info("\n" + "=" * 60)
    logger.info(f"[FINALIZE] Kalan segmentler tamamlandı. (Toplam gönderilen: {len(uploaded_chunks)})")
    logger.info("=" * 60 + "\n")

    # 6. Kalan dosyaları temizle
    try:
        for seg in segments:
            try:
                if seg.exists(): seg.unlink()
            except Exception: pass
        logger.info("[CLEANUP] Tüm segment dosyaları temizlendi")
    except Exception as e:
        logger.info(f"[WARN] Segment temizleme hatası: {e}")

    logger.info("[DONE] Tüm segmentler işlendi!")
    
    # WORKER'A BAŞARI DURUMU BİLDİR
    try:
        status_data = {
            "success": True,
            "backend_success": True,
            "segments_sent": sent_count,
            "segments_skipped": skipped_count,
            "timestamp": time.time()
        }
        Path("recorder_status.json").write_text(
            json.dumps(status_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("[STATUS] recorder_status.json oluşturuldu (worker bildirimi)")
    except Exception as e:
        logger.info(f"[WARN] Status dosyası yazılamadı: {e}")
# ============================================================
# MAIN - KAYDI BAŞLAT
# ============================================================

logger.info("[START] WebM kaydı başlatılıyor...")

try:


    ffmpeg_process = start_ffmpeg_recording()

    if ffmpeg_process is None:
        logger.info("[CRITICAL] ffmpeg başlatılamadı!")
        sys.exit(1)

    logger.info("[RECORDING] ✓ Zoom sesi kaydediliyor (WebM/Opus, segmentli)...")
    logger.info("[INFO] Ses kaydı devam ediyor")
    logger.info("-" * 60)

    last_status_print = time.time()

    while recording_active:
        try:
            # STOP SİNYALİ KONTROLÜ (Worker'dan gelen durdur komutu)
            stop_signal = Path("stop_recording.signal")
            if stop_signal.exists():
                logger.info("\n[SIGNAL] Stop sinyali alındı, kayıt sonlandırılıyor...")
                try:
                    stop_signal.unlink()  # Sinyali temizle
                except Exception:
                    pass
                recording_active = False
                break

            # ffmpeg hala çalışıyor mu?
            if ffmpeg_process.poll() is not None:
                logger.info("\n[CRITICAL] ffmpeg kapandı!")
                recording_active = False
                break

            time.sleep(1)

            # Her 60 saniyede durum raporu
            current_time = time.time()
            if current_time - last_status_print >= 60:
                if recording_start_time:
                    duration_min = (current_time - recording_start_time) / 60

                    segment_files = list(segment_dir.glob("chunk_*.webm"))
                    total_size_mb = sum(
                        f.stat().st_size for f in segment_files
                    ) / (1024 * 1024) if segment_files else 0.0

                    logger.info(
                        f"[STATUS] Kayıt devam: {duration_min:.1f} dk, "
                        f"toplam {total_size_mb:.2f} MB, {len(segment_files)} segment"
                    )

                if recording_start_time:
                    duration_min = (current_time - recording_start_time) / 60
                    logger.info(f"[STATUS] Kayıt devam ediyor ({duration_min:.1f} dk)... Live Upload Aktif")
                
                last_status_print = current_time

            #  LIVE UPLOAD CHECK (Her 5 saniyede bir)
            if int(current_time) % 5 == 0:
                 process_live_queue()

        except Exception as loop_error:
            logger.info(f"\n[LOOP ERROR] {type(loop_error).__name__}: {loop_error}")
            time.sleep(1)

    logger.info("\n[INFO] Kayıt döngüsünden çıkıldı")
    logger.info("[FINAL] Kayıt döngüsü bitti, segmentler backend'e gönderiliyor...")
    send_final_webm()

except KeyboardInterrupt:
    logger.info("\n[STOP] Ctrl+C alındı")
    send_final_webm()

except Exception as e:
    logger.info(f"\n[CRITICAL] RECORDER ÇÖKTÜ: {e}")
    import traceback
    traceback.print_exc()
    send_final_webm()
    sys.exit(1)

finally:
    if recording_start_time:
        total_duration = (time.time() - recording_start_time) / 60
        logger.info(f"\n[RECORDER-EXIT] Toplam süre: {total_duration:.1f} dakika")
