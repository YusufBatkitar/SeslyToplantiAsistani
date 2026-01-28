import sys
import os
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"

import json
import time
from pathlib import Path
import subprocess


# Rapor için
try:
    from rapor import generate_meeting_report
    RAPOR_AVAILABLE = True
    print("[IMPORT] ✓ Rapor modulu yüklendi")
except ImportError as e:
    RAPOR_AVAILABLE = False
    print(f"[IMPORT] ⚠ Rapor modulu yüklenemedi: {e}")

import psutil

# ---- Zoom için ---
import shutil

# ---- Zoom için Web Modu Kullanılıyor ----
# Legacy EXE fonksiyonları kaldırıldı.



# ==========================================
# ORTAK DOSYALAR
# ==========================================
BOT_TASK_FILE = Path("data/bot_task.json")
BOT_COMMAND_FILE = Path("data/bot_command.json")
RECORDER_PATH = str(Path(__file__).parent / "zoom_bot_recorder.py")  # Recorder script yolu
WORKER_STATUS = Path("data/worker_status.json")


# ==========================================
# ORTAK FONKSİYONLAR
# ==========================================

def save_worker_status(
    platform: str,
    running: bool = False,
    recording: bool = False,
    status_msg: str = "",
    paused: bool = False,
    silent: bool = False,
):
    """
    worker_status.json dosyasına durum yaz
    platform: "zoom" | "teams" | "meet"
    """
    data = {
        "platform": platform,
        "running": running,
        "recording": recording,
        "paused": paused,
        "status_message": status_msg,
        "timestamp": time.time(),
    }
    WORKER_STATUS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if not silent:
        print(f"[STATUS][{platform}] running={running}, recording={recording}, msg={status_msg}")


def load_task():
    """
    bot_task.json'dan aktif görevi oku.
    Zoom / Teams / Meet fark etmeksizin tek yerden okuyor.
    """
    if not BOT_TASK_FILE.exists():
        return None

    try:
        data = json.loads(BOT_TASK_FILE.read_text(encoding="utf-8"))
        if not data.get("active"):
            return None
        platform = (data.get("platform") or "zoom").lower()
        if platform not in ("zoom", "teams", "meet"):
            return None
        return data
    except Exception as e:
        print(f"[ERROR] Görev okuma hatası: {e}")
        return None


# reset_task() FONKSİYONU KALDIRILDI
# Bu fonksiyon bot_task.json'ı "active": false ile oluşturuyordu
# Worker başlamadan önce bu dosya varsa, worker hemen çık diyordu
# Artık her yerde dosyayı direkt siliyoruz, bu fonksiyona gerek yok

# def reset_task():
#     """bot_task.json'ı sıfırla (tüm platformlar için)"""
#     empty_task = {
#         "active": False,
#         "meeting_id": "",
#         "passcode": "",
#         "meeting_url": "",
#         "platform": "",
#         "bot_name": "Sesly Bot",
#         "timestamp": time.time(),
#     }
#     BOT_TASK_FILE.write_text(json.dumps(empty_task, ensure_ascii=False), encoding="utf-8")




def cleanup_files(keep_pdfs=True, close_zoom=False, verbose=True, delete_task_file=True):
    """
    Geçici dosyaları temizle
    """
    if verbose:
        print("\n" + "=" * 60)
        print("[CLEANUP] Sistem temizleniyor...")
        print("=" * 60)
    
    # Geçici dosyalar
    files_to_clean = [
        BOT_COMMAND_FILE,
        Path("participants.json"),
        Path("speaker_activity_log.json"),
        Path("live_transcript_cache.json"),
        Path("latest_transcript.txt"),
        Path("recorder_status.json")
    ]
    
    # PDF korunacaksa WORKER_STATUS ve current_meeting_participants.json'ı da temizle
    if keep_pdfs:
        files_to_clean.extend([
            WORKER_STATUS,
            Path("current_meeting_participants.json")
        ])
    
    for file in files_to_clean:
        try:
            if file.exists():
                file.unlink()
                if verbose:
                    print(f"[OK] ✓ {file.name} silindi")
        except Exception as e:
                if verbose:
                    print(f"[WARN] {file.name} silinemedi: {e}")

    # ZOOM SEGMENTS KLASÖRÜNÜ TEMİZLE
    try:
        import tempfile
        segment_dir = Path(tempfile.gettempdir()) / "zoom_segments"
        if segment_dir.exists():
            for item in segment_dir.glob("*"):
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except Exception:
                    pass
            if verbose:
                print(f"[CLEANUP] Segment klasörü temizlendi: {segment_dir}")
        else:
            segment_dir.mkdir(parents=True, exist_ok=True)

        # STALE zoom_meeting_temp.webm TEMİZLİĞİ
        stale_webm = Path(tempfile.gettempdir()) / "zoom_meeting_temp.webm"
        if stale_webm.exists():
            stale_webm.unlink()
            if verbose:
                print("[CLEANUP] Eski zoom_meeting_temp.webm silindi")

    except Exception as e:
        if verbose:
            print(f"[WARN] Segment temizliği hatası: {e}")
    
    # Zoom kontrolü
    if close_zoom:
        try:
            zoom_procs = [p for p in psutil.process_iter(['name'])
                          if 'zoom' in p.info['name'].lower()]
            if zoom_procs:
                if verbose:
                    print(f"[ACTION] Zoom kapatılıyor...")
                os.system("taskkill /F /IM Zoom.exe 2>nul")
                time.sleep(2)
        except Exception:
            pass
    
    # BOT_TASK.JSON'ı SİL (reset_task yerine)
    # reset_task() "active": false yazıyor ve worker hemen çıkıyor!
    # Bunun yerine dosyayı tamamen silelim
    try:
        if delete_task_file and BOT_TASK_FILE.exists():
            BOT_TASK_FILE.unlink()
            if verbose:
                print("[CLEANUP] bot_task.json silindi")
    except Exception as e:
        if verbose:
            print(f"[WARN] bot_task.json silinemedi: {e}")
    
    save_worker_status(
        "zoom",
        running=False,
        recording=False,
        status_msg="Sistem hazır",
        paused=False
    )
    
    if verbose:
        print("[SUCCESS] ✓ Temizlik tamamlandı")


def check_bot_command():
    """
    bot_command.json'dan pause/resume/stop/force_reset gibi komutları kontrol et.
    Komut bir kere okununca processed=true yapar.
    """
    if not BOT_COMMAND_FILE.exists():
        return None

    try:
        cmd = json.loads(BOT_COMMAND_FILE.read_text(encoding="utf-8"))
        if cmd.get("processed"):
            return None

        cmd["processed"] = True
        BOT_COMMAND_FILE.write_text(json.dumps(cmd, ensure_ascii=False), encoding="utf-8")
        command = cmd.get("command")
        print(f"[KOMUT] {command}")
        return command
    except Exception as e:
        print(f"[ERROR] Komut okuma: {e}")
        return None


def start_recorder(platform: str):
    """Tüm platformlar için aynı recorder scripti (zoom_bot_recorder.py) - ENHANCED"""
    print(f"[RECORDER][{platform}] Ses kaydedici başlatılıyor...")
    
    # Log klasörünü kontrol et
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    try:
        if not Path(RECORDER_PATH).exists():
            print(f"[CRITICAL] Recorder dosyası bulunamadı: {RECORDER_PATH}")
            return None

        # 🔥 FAILSAFE: Eski transkripti burada da sil
        try:
            old_transcript = Path("latest_transcript.txt")
            if old_transcript.exists():
                old_transcript.unlink()
                print("[CLEANUP] Start öncesi eski transkript silindi.")
        except: pass

        log_path = (log_dir / f"recorder_output_{platform}.log").resolve()
        log_file = open(log_path, "w", encoding="utf-8")

        # Çalışma dizini script'in olduğu yer olsun
        cwd = str(Path(__file__).parent)
        
        cmd = [sys.executable, RECORDER_PATH]
        if platform == "teams":
             cmd.extend(["--platform", "teams"]) # Teams için argüman ekle

        print(f"[DEBUG] CWD: {cwd}")
        print(f"[DEBUG] CMD: {cmd}")
        print(f"[DEBUG] Log Path: {log_path}")

        process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore',
            cwd=cwd, # Çalışma dizinini sabitle
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        # Biraz daha uzun bekle
        print(f"[WAIT] Recorder ({platform}) başlatılıyor (5s)...")
        time.sleep(5)
        
        if process.poll() is None:
            print(f"[OK] Recorder ({platform}) çalışıyor! (PID: {process.pid})")
            save_worker_status(platform, running=True, recording=True, status_msg=f"Kayıt alınıyor ({platform})")
            return process
        
        # Hemen kapandı
        print(f"[FAIL] Recorder ({platform}) hemen kapandı!")
        log_file.close() 
        
        if log_path.exists():
            print("--------- RECORDER OUTPUT ---------")
            try:
                print(log_path.read_text(encoding="utf-8").strip() or "(boş çıktı)")
            except: pass
            print("-----------------------------------")

        save_worker_status(platform, running=True, recording=False, status_msg="⚠️ Kayıt başlatılamadı")
        return None

    except Exception as e:
        print(f"[ERROR][{platform}] Recorder başlatma hatası: {e}")
        import traceback
        traceback.print_exc()
        return None


# ==========================================
# ZOOM TARAFI
# ==========================================


def handle_zoom_task(task: dict):
    meeting_url = task.get("meeting_url") or ""
    bot_name = task.get("bot_name") or "Sesly Bot"
    passcode = task.get("passcode") or ""
    
    # ============================================================
    # ZOOM WEB CLIENT (PLAYWRIGHT) MODU
    # ============================================================
    print("\n" + "="*60)
    print("[ZOOM] GÖREV BAŞLATILIYOR (WEB CLIENT MODE)")
    print("="*60)
    print(f"[INFO] URL: {meeting_url}")
    print(f"[INFO] Bot: {bot_name}")
    print(f"[INFO] Passcode: {'******' if passcode else 'Yok'}")
    print("[INFO] Yeni Chromium tabanlı Zoom botu devreye giriyor.")
    print("[INFO] Daha iyi konuşmacı tespiti ve stabilite için.")
    print("="*60 + "\n")

    # STALE VERİ TEMİZLİĞİ
    try:
        # delete_task_file=False çünkü task daha yeni oluşturuldu!
        cleanup_files(keep_pdfs=True, close_zoom=True, verbose=True, delete_task_file=False) # Zoom.exe'yi kapat, web'den gireceğiz
        if Path("data/bot_command.json").exists(): Path("data/bot_command.json").unlink()
        if Path("stop_recording.signal").exists(): Path("stop_recording.signal").unlink()
    except Exception as e:
        print(f"[INIT ERROR] Temizlik hatası: {e}")

    # Worker script'i çalıştır
    worker_script = str(Path(__file__).parent / "zoom_web_worker.py")
    
    try:
        # subprocess.run ile bloklayarak çalıştırıyoruz (sistem.py bu görevi bekleyecek)
        # 3. Argüman olarak passcode gönderiyoruz
        cmd = [sys.executable, worker_script, meeting_url, bot_name, passcode]
        print(f"[EXEC] {cmd}")
        
        result = subprocess.run(
            cmd,
            cwd=str(Path(__file__).parent),
            text=True
        )
        
        if result.returncode != 0:
            print(f"[ERROR] Worker hata koduyla döndü: {result.returncode}")
            save_worker_status("zoom", running=False, recording=False, status_msg="Worker hatası")
        else:
            print("[SUCCESS] Worker başarıyla tamamlandı.")
            save_worker_status("zoom", running=False, recording=False, status_msg="Görev tamamlandı")
            
    except KeyboardInterrupt:
        print("\n[STOP] Kullanıcı durdurdu.")
    except Exception as e:
        print(f"[ERROR] Worker çalıştırma hatası: {e}")
        save_worker_status("zoom", running=False, recording=False, status_msg="Sistem hatası")
    
    
    # Görevi sıfırla (DOSYAYI SİL, reset_task kullanma!)
    # reset_task() "active": false yazıyor, worker çalışırken bu dosya varsa hemen çıkıyor!
    try:
        if BOT_TASK_FILE.exists():
            BOT_TASK_FILE.unlink()
            print("[CLEANUP] bot_task.json silindi (görev bitti)")
    except Exception as e:
        print(f"[WARN] bot_task.json silinemedi: {e}")



# ==========================================
# TEAMS TARAFI
# ==========================================




def handle_teams_task(task: dict):
    meeting_url = (task.get("meeting_url") or "").strip()
    if not meeting_url:
        print("[TEAMS] Toplantı linki yok, görev atlandı.")
        # reset_task() yerine dosyayı sil
        try:
            if BOT_TASK_FILE.exists():
                BOT_TASK_FILE.unlink()
        except: pass
        return

    print(f"[TEAMS] Toplantı için Web Worker başlatılıyor: {meeting_url}")
    save_worker_status("teams", running=True, recording=False, status_msg="Teams Web Worker Başlatılıyor...")

    # Yeni Web Worker'ı subprocess olarak başlat
    # Bu worker kendi içinde: Join -> Record -> Wait -> Report yapar
    try:
        cmd = ["python", "teams_web_worker.py", meeting_url]
        subprocess.run(cmd, check=False)
        print("[TEAMS] Web Worker görevi tamamladı.")
    except Exception as e:
        print(f"[ERROR] Teams worker hatası: {e}")
        save_worker_status("teams", running=False, recording=False, status_msg=f"Worker hatası: {e}")
    
    # reset_task() yerine dosyayı sil
    try:
        if BOT_TASK_FILE.exists():
            BOT_TASK_FILE.unlink()
    except: pass
    return




# ==========================================
# MEET TARAFI
# ==========================================
# Meet artık meet_worker.py tarafından yönetiliyor (Teams pattern)


def handle_meet_task(task: dict):
    """Google Meet toplantısına katıl (meet_web_worker.py kullanarak)"""
    
    meeting_url = (task.get("meeting_url") or "").strip()
    if not meeting_url:
        print("[MEET] Toplantı linki yok, görev atlandı.")
        # reset_task() yerine dosyayı sil
        try:
            if BOT_TASK_FILE.exists():
                BOT_TASK_FILE.unlink()
        except: pass
        return

    print(f"[MEET] Toplantı için Web Worker başlatılıyor: {meeting_url}")
    save_worker_status("meet", running=True, recording=False, status_msg="Meet Web Worker Başlatılıyor...")

    # Meet Web Worker'ı subprocess olarak başlat
    # Bu worker kendi içinde: Join -> Record -> Wait -> Report yapar
    try:
        cmd = ["python", "meet_worker.py", meeting_url]
        subprocess.run(cmd, check=False)
        print("[MEET] Web Worker görevi tamamladı.")
    except Exception as e:
        print(f"[ERROR] Meet worker hatası: {e}")
        save_worker_status("meet", running=False, recording=False, status_msg=f"Worker hatası: {e}")
    
    # reset_task() yerine dosyayı sil
    try:
        if BOT_TASK_FILE.exists():
            BOT_TASK_FILE.unlink()
    except: pass
    return



# ==========================================
# ANA MAIN LOOP
# ==========================================

def main():
    print("=" * 60)
    print("[SİSTEM] Zoom + Teams + Meet birleşik worker başlatıldı.")
    
    # ✅ Gerekli klasörleri oluştur
    print("[SETUP] Gerekli klasörler kontrol ediliyor...")
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    Path("temp_reports").mkdir(exist_ok=True)
    print("[SETUP] ✓ Klasörler hazır (logs, data, temp_reports)")
    
    print("[SİSTEM] Eski görevler temizleniyor...")
    # reset_task() KALDIRILDI - dosyayı active:false ile yaratıyor, sorun çıkarıyor
    try:
        if BOT_TASK_FILE.exists():
            BOT_TASK_FILE.unlink()
            print("[SİSTEM] bot_task.json silindi")
    except: pass
    print("[SİSTEM] bot_task.json izleniyor, platforma göre bot devreye girecek...")
    print("=" * 60)

    while True:
        task = load_task()
        if not task:
            time.sleep(1)
            continue

        platform = (task.get("platform") or "zoom").lower()
        print("\n" + "=" * 60)
        print(f"[SİSTEM] Yeni görev algılandı! Platform = {platform}")
        print("=" * 60)

        if platform == "zoom":
            handle_zoom_task(task)
        elif platform == "teams":
            handle_teams_task(task)
        elif platform == "meet":
            handle_meet_task(task)
        else:
            print(f"[SİSTEM] Desteklenmeyen platform: {platform}")
            try:
                if BOT_TASK_FILE.exists():
                    BOT_TASK_FILE.unlink()
            except: pass

        print("[SİSTEM] Görev bitti, yeni görev bekleniyor...")
        time.sleep(2)


if __name__ == "__main__":
    try:
        import pywinauto  # Teams için gerekli
    except ImportError:
        print("[WARN] pywinauto yok, Teams desteği çalışmayabilir. Kur: pip install pywinauto pywin32")

    try:
        main()
    except KeyboardInterrupt:
        print("\n[SİSTEM] Çıkış yapılıyor...")
