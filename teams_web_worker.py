
import json
import time
import asyncio
import subprocess
import traceback
from pathlib import Path
from teams_web_client import TeamsWebBot
import logging

# Logger with Rotating Handler
from logging.handlers import RotatingFileHandler
Path("logs").mkdir(exist_ok=True)

_file_handler = RotatingFileHandler(
    "logs/teams_worker.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding='utf-8'
)
_file_handler.setFormatter(logging.Formatter('%(asctime)s - [TEAMS-WORKER] %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [TEAMS-WORKER] %(message)s',
    handlers=[_file_handler, logging.StreamHandler()]
)
logger = logging.getLogger("TeamsWorker")

BOT_TASK_FILE = Path("data/bot_task.json")
WORKER_STATUS_FILE = Path("data/worker_status.json")

# Script Paths
RECORDER_SCRIPT = "zoom_bot_recorder.py"
RAPOR_SCRIPT = "rapor.py"

def update_status(**kwargs):
    """worker_status.json dosyasını güncelle."""
    status = {
        "running": False,  # FIXED: zoom_running -> running
        "recording": False,
        "paused": False,
        "status_message": "",
        "platform": "teams",
        "timestamp": time.time(),
    }

    if WORKER_STATUS_FILE.exists():
        try:
            old = json.loads(WORKER_STATUS_FILE.read_text(encoding="utf-8"))
            status.update(old)
        except Exception:
            pass

    status.update(kwargs)
    try:
        WORKER_STATUS_FILE.write_text(
            json.dumps(status, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"Status update error: {e}")

async def run_teams_task(meeting_url):
    """Teams görevini yürütür."""
    bot = None
    recorder_proc = None

    try:
        BOT_COMMAND_FILE = Path("data/bot_command.json")
        STOP_SIGNAL_FILE = Path("stop_recording.signal")
        
        if BOT_COMMAND_FILE.exists():
            try:
                BOT_COMMAND_FILE.unlink()
                logger.info("Eski bot komut dosyası temizlendi.")
            except: pass

        if STOP_SIGNAL_FILE.exists():
            try:
                STOP_SIGNAL_FILE.unlink()
                logger.info("Eski stop signal dosyası temizlendi.")
            except: pass

        # Data dosyalarını temizle (Transkript, Katılımcılar vb.)
        files_to_clean = [
            "latest_transcript.txt", 
            "speaker_activity_log.json", 
            "current_meeting_participants.json"
        ]
        for fname in files_to_clean:
            f = Path(fname)
            if f.exists():
                try:
                    f.unlink()
                    logger.info(f"Eski veri dosyası temizlendi: {fname}")
                except: pass

        # 1. Botu Başlat
        logger.info(f"Teams görevi başlıyor: {meeting_url}")
        update_status(running=True, status_message="Teams (Web) başlatılıyor...")
        
        bot = TeamsWebBot(meeting_url, bot_name="Sesly Bot")
        await bot.start()

        # 2. Toplantıya Katıl
        update_status(status_message="Toplantıya katılıyor...")
        joined = await bot.join_meeting()
        
        if not joined:
            logger.error("Toplantıya katılınamadı.")
            update_status(
                running=False,
                status_message="Katılım başarısız!", 
                error="Teams toplantısına katılınamadı. Link geçersiz veya bekleme odası zaman aşımına uğradı."
            )
            return
        
        logger.info("Toplantıya giriş başarılı.")
        update_status(status_message="Toplantıda - Kayıt başlıyor...")

        # 3. Kaydı Başlat (Subprocess)
        # Browser sesi sistem sesine (VB-Cable) gideceği için recorder bunu yakalar.
        logger.info("Recorder başlatılıyor...")
        
        # Timeline dosyasını temizle (yeni toplantı için)
        try:
            Path("speaker_timeline.jsonl").write_text("", encoding="utf-8")
            logger.info("Speaker timeline temizlendi.")
        except: pass
        
        try:
            # Recorder script'ini ayrı process olarak çalıştır
            # --platform teams argümanını ekle
            recorder_proc = subprocess.Popen(["python", RECORDER_SCRIPT, "--platform", "teams"])
            update_status(recording=True, status_message="🔴 Kayıt Alınıyor")
        except Exception as e:
            logger.error(f"Recorder hatası: {e}")

        # 4. Chat Mesajı Gönder (Opsiyonel)
        await asyncio.sleep(5)
        await bot.send_message("Merhaba! Ben Sesly Bot 🤖 Bu toplantıyı kaydediyorum.")
        await asyncio.sleep(2)
        
        # 4a. Katılımcı Listesini Aç (Dinleme moduna hazırlık)
        logger.info("Katılımcı listesi açılıyor...")
        await bot.open_participants_list() # FIX: get_participants değil, open_participants_list!


        # 5. Döngü: Toplantı Bitene Kadar Bekle
        logger.info("Toplantı izleniyor...")
        while True:
            # Task iptal edildi mi kontrol et (Dosyadan)
            if BOT_TASK_FILE.exists():
                try:
                    task = json.loads(BOT_TASK_FILE.read_text(encoding="utf-8"))
                    if not task.get("active", False):
                        logger.info("Görev iptal edildi.")
                        break
                except:
                    pass

            # Komut kontrolü (Stop/Pause)
            BOT_COMMAND_FILE = Path("data/bot_command.json")
            if BOT_COMMAND_FILE.exists():
                try:
                    cmd_data = json.loads(BOT_COMMAND_FILE.read_text(encoding="utf-8"))
                    # Process edilmemiş ve 'stop' komutu ise
                    if not cmd_data.get("processed", False) and cmd_data.get("command") == "stop":
                         logger.info("🛑 STOP komutu alındı. Çıkış yapılıyor...")
                         # Processed olarak işaretle
                         cmd_data["processed"] = True
                         BOT_COMMAND_FILE.write_text(json.dumps(cmd_data), encoding="utf-8")
                         break
                except: pass

            # Toplantı bitti mi?
            if await bot.check_meeting_ended():
                logger.info("Toplantı bitişi tespit edildi.")
                # Geçersiz toplantı mı kontrol et
                if bot.end_reason and bot.end_reason != "normal":
                    update_status(
                        running=False,
                        error=bot.end_reason
                    )
                break
                
            # --- KONUŞMACI TAKİBİ ---
            try:
                active_speakers = await bot.get_participants()
                if active_speakers:
                    # Loglama (JSON)
                    log_data = {
                        "timestamp": time.time(),
                        "platform": "teams",
                        "speakers": active_speakers  # Zoom ile aynı format
                    }
                    try:
                        # 1. Log History (Recorder bunu okur) - LIST FORMAT (Append)
                        activity_log = Path("speaker_activity_log.json")
                        logs = []
                        if activity_log.exists():
                            try:
                                logs = json.loads(activity_log.read_text(encoding="utf-8"))
                                if not isinstance(logs, list): logs = []
                            except: logs = []
                        
                        logs.append(log_data)
                        activity_log.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
                        
                        # 2. Timeline Append (Geçmiş) - Yedek olarak kalsın (opsiyonel)
                        # ...
                        
                        # 3. Current Snapshot (UI/Backend integration)
                        Path("current_meeting_participants.json").write_text(json.dumps(active_speakers, ensure_ascii=False), encoding="utf-8")
                    except: pass
            except Exception as e:
                pass
                
            # Heartbeat (UI Active kalsın diye)
            update_status(zoom_running=True, running=True, recording=recorder_proc is not None)

            await asyncio.sleep(2)

    except Exception as e:
        logger.error(f"Görev hatası: {traceback.format_exc()}")
    
    finally:
        # Temizlik
        update_status(status_message="Kapatılıyor...", recording=False)
        
        if recorder_proc:
            logger.info("Recorder durduruluyor (Graceful)...")
            # Signal dosyası oluştur (Recorder bunu bekliyor)
            Path("stop_recording.signal").touch()
            
            try:
                # Recorder'ın işini bitirmesini bekle (Upload vs.)
                # Normalde chunk'lar anlık gider, sadece son parçayı bekleriz.
                # Yine de kullanıcı isteği üzerine güvenli marj: 60 saniye.
                recorder_proc.wait(timeout=60)
                logger.info("Recorder başarıyla kapandı.")
            except subprocess.TimeoutExpired:
                logger.error("Recorder zaman aşımına uğradı, zorla kapatılıyor.")
                recorder_proc.kill()
            except Exception as e:
                logger.error(f"Recorder durdurma hatası: {e}")
                recorder_proc.kill()
        
        if bot:
            await bot.close()

        logger.info("Rapor oluşturuluyor...")
        update_status(status_message="Rapor hazırlanıyor...")
        
        # Rapor scriptini çalıştır ve çıktıyı yakala
        try:
            result = subprocess.run(
                ["python", "-u", RAPOR_SCRIPT], 
                capture_output=True, 
                text=True,
                encoding='utf-8',
                check=False
            )
            
            if result.returncode == 0:
                logger.info("Rapor başarıyla oluşturuldu.")
                logger.info(result.stdout)
                
                # Rapor teslim edildi, log dosyalarını temizle
                logger.info("Geçici dosyalar temizleniyor...")
                cleanup_files = [
                    "speaker_timeline.jsonl",
                    "speaker_activity_log.json",
                    "latest_transcript.txt",
                    "current_meeting_participants.json",
                    "speaker_realtime_stats.json",
                    "debug_speaker_detection.txt",
                    "ws_speaker_debug.json"
                ]
                
                for filename in cleanup_files:
                    try:
                        file_path = Path(filename)
                        if file_path.exists():
                            file_path.unlink()
                            logger.info(f"  ✓ {filename} silindi")
                    except Exception as e:
                        logger.debug(f"  ✗ {filename} silinemedi: {e}")
                
                logger.info("Temizlik tamamlandı.")
                
            else:
                logger.error(f"Rapor oluşturma hatası (Kod {result.returncode}):")
                logger.error(result.stderr)
                logger.error(result.stdout)
                
        except Exception as e:
            logger.error(f"Rapor script çalıştırma hatası: {e}")
        
        update_status(running=False, status_message="Görev Tamamlandı")
        
        # Task'i pasife çek
        if BOT_TASK_FILE.exists():
            try:
                t = json.loads(BOT_TASK_FILE.read_text("utf-8"))
                t["active"] = False
                BOT_TASK_FILE.write_text(json.dumps(t, indent=2), "utf-8")
            except:
                pass

async def main():
    logger.info("🤖 Teams Web Worker Başlatıldı")
    while True:
        if not BOT_TASK_FILE.exists():
            await asyncio.sleep(2)
            continue

        try:
            task = json.loads(BOT_TASK_FILE.read_text(encoding="utf-8"))
            if task.get("active") and task.get("platform") == "teams":
                url = task.get("meeting_url")
                if url:
                    await run_teams_task(url)
        except Exception as e:
            logger.error(f"Loop hatası: {e}")
            await asyncio.sleep(2)
        
        await asyncio.sleep(2)

if __name__ == "__main__":
    import sys
    try:
        if len(sys.argv) > 1:
            # Tek seferlik görev (Subprocess modu)
            url = sys.argv[1]
            if url:
                asyncio.run(run_teams_task(url))
        else:
            # Döngü modu (Standalone)
            asyncio.run(main())
    except KeyboardInterrupt:
        pass
