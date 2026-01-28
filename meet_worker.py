
import json
import time
import asyncio
import subprocess
import traceback
from pathlib import Path
from meet_web_client import MeetWebBot
import logging

# Logger with Rotating Handler
from logging.handlers import RotatingFileHandler
Path("logs").mkdir(exist_ok=True)

_file_handler = RotatingFileHandler(
    "logs/meet_worker.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding='utf-8'
)
_file_handler.setFormatter(logging.Formatter('%(asctime)s - [MEET-WORKER] %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [MEET-WORKER] %(message)s',
    handlers=[_file_handler, logging.StreamHandler()]
)
logger = logging.getLogger("MeetWorker")

BOT_TASK_FILE = Path("data/bot_task.json")
BOT_COMMAND_FILE = Path("data/bot_command.json")
WORKER_STATUS_FILE = Path("data/worker_status.json")

# Script Paths
RECORDER_SCRIPT = "zoom_bot_recorder.py"
RAPOR_SCRIPT = "rapor.py"

def update_status(**kwargs):
    """worker_status.json dosyasını güncelle."""
    status = {
        "running": False,
        "recording": False,
        "paused": False,
        "status_message": "",
        "platform": "meet",
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

async def run_meet_task(meeting_url):
    """Meet görevini yürütür."""
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

        # Data dosyalarını temizle
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
        logger.info(f"Meet görevi başlıyor: {meeting_url}")
        update_status(running=True, status_message="Meet (Web) başlatılıyor...")
        
        bot = MeetWebBot(meeting_url, bot_name="Sesly Bot")
        await bot.start()

        # 2. Toplantıya Katıl
        update_status(status_message="Toplantıya katılıyor...")
        joined = await bot.join_meeting()
        
        if not joined:
            logger.error("Toplantıya katılınamadı.")
            update_status(
                running=False,
                status_message="Katılım başarısız!", 
                error="Google Meet toplantısına katılınamadı. Link geçersiz veya bekleme odası zaman aşımına uğradı."
            )
            return
        
        logger.info("Toplantıya giriş başarılı.")
        update_status(status_message="Toplantıda - Kayıt başlıyor...")

        # 2.1 POPUP KAPATMA (Anladım, Got it vb.)
        try:
            await bot._dismiss_popups()
        except: pass


        # 2.2 KATILIMCI LİSTESİNİ ÇEK (Panel'den)
        try:
            logger.info("Katılımcı listesi panelden çekiliyor...")
            participants = await bot.get_all_participants_from_panel()
            if participants:
                # JSON dosyasına kaydet (platform bilgisiyle)
                participant_data = {
                    "participants": participants,
                    "platform": "meet"
                }
                Path("current_meeting_participants.json").write_text(
                    json.dumps(participant_data, ensure_ascii=False), 
                    encoding="utf-8"
                )
                logger.info(f"✅ {len(participants)} katılımcı kaydedildi: {participants}")
        except Exception as e:
            logger.warning(f"Katılımcı listesi alınamadı: {e}")

        # 3. Kaydı Başlat
        logger.info("Recorder başlatılıyor...")
        
        # Timeline ve transcript dosyalarını temizle (yeni göreve hazırlan)
        try:
            Path("speaker_timeline.jsonl").write_text("", encoding="utf-8")
            Path("latest_transcript.txt").write_text("", encoding="utf-8")
            logger.info("Timeline ve transcript temizlendi (yeni görev).")
        except: pass
        
        try:
            # Recorder script'ini ayrı process olarak çalıştır
            recorder_proc = subprocess.Popen(["python", RECORDER_SCRIPT, "--platform", "meet"])
            update_status(recording=True, status_message="🔴 Kayıt Alınıyor")
        except Exception as e:
            logger.error(f"Recorder hatası: {e}")

        # 4. Giriş mesajı
        await asyncio.sleep(5)
        try:
            welcome_msg = "Merhaba, ben Sesly Asistan. Toplantınızı not almak için buradayım."
            await bot.send_message(welcome_msg)
            logger.info("Giriş mesajı gönderildi.")
        except Exception as e:
            logger.warning(f"Giriş mesajı gönderilemedi: {e}")
        
        # 4.1 POPUP TEKRAR KONTROL (Mesaj sonrası yeni popup çıkabilir)
        try:
            await bot._dismiss_popups()
        except: pass
        
        # 4.2 CANLI ALTYAZIYI AÇ (Mesaj gönderdikten sonra)
        await asyncio.sleep(2)
        try:
            logger.info("Canlı altyazı açılıyor...")
            caption_enabled = await bot.enable_captions()
            if not caption_enabled:
                logger.warning("⚠️ Altyazı açılamadı - toplantı ayarlarından kapalı olabilir")
            # DİL DEĞİŞTİRME DEVRE DIŞI - Altyazıyı kapatıyordu
            # Google hesap ayarlarından varsayılan dil Türkçe yapılmalı
        except Exception as e:
            logger.warning(f"Altyazı açılamadı: {e}")
        
        # 5. Katılımcı panelini aç (konuşmacı tespiti için)
        try:
            await bot.open_participants_panel()
        except Exception as e:
            logger.warning(f"Katılımcı paneli açılamadı: {e}")

        # 5. Döngü: Toplantı Bitene Kadar Bekle
        logger.info("Toplantı izleniyor...")
        speaker_check_interval = 0.5  # 500ms - daha hassas konuşmacı tespiti
        participant_refresh_interval = 60  # 60 saniyede bir katılımcı listesini güncelle
        caption_check_interval = 15  # 15 saniyede bir altyazı kontrolü
        last_participant_refresh = time.time()
        last_caption_check = time.time()
        
        while True:
            # Task iptal edildi mi kontrol et
            if BOT_TASK_FILE.exists():
                try:
                    task = json.loads(BOT_TASK_FILE.read_text(encoding="utf-8"))
                    if not task.get("active", False):
                        logger.info("Görev iptal edildi.")
                        break
                except:
                    pass

            # Komut kontrolü (Stop/Pause)
            if BOT_COMMAND_FILE.exists():
                try:
                    cmd_data = json.loads(BOT_COMMAND_FILE.read_text(encoding="utf-8"))
                    if not cmd_data.get("processed", False) and cmd_data.get("command") == "stop":
                         logger.info("🛑 STOP komutu alındı. Çıkış yapılıyor...")
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
                
            # --- PERİYODİK KATILIMCI LİSTESİ GÜNCELLEMESİ ---
            if time.time() - last_participant_refresh > participant_refresh_interval:
                try:
                    logger.info("📋 Katılımcı listesi güncelleniyor...")
                    new_participants = await bot.get_all_participants_from_panel()
                    if new_participants:
                        participant_data = {
                            "participants": new_participants,
                            "platform": "meet"
                        }
                        Path("current_meeting_participants.json").write_text(
                            json.dumps(participant_data, ensure_ascii=False), 
                            encoding="utf-8"
                        )
                        logger.info(f"✅ Katılımcı listesi güncellendi: {len(new_participants)} kişi")
                    last_participant_refresh = time.time()
                except Exception as e:
                    logger.debug(f"Katılımcı güncelleme hatası: {e}")
            
            # --- PERİYODİK ALTYAZI KONTROLÜ KALDIRILDI ---
            # Altyazı başlangıçta 1 kez açılıyor, sonra dokunulmayacak
            # (toggle butonu açık altyazıyı kapatıyordu)
                
            # --- KONUŞMACI TAKİBİ ---
            try:
                active_speakers = await bot.get_participants()
                if active_speakers:
                    # Loglama (JSON)
                    log_data = {
                        "timestamp": time.time(),
                        "platform": "meet",
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
                        
                        # 2. Timeline Append (Geçmiş)
                        # ...

                        # 3. Current Snapshot (UI/Backend integration)
                        # Platform bilgisi eklendi - Hibrit diarization için
                        participant_data = {
                            "participants": active_speakers,
                            "platform": "meet"
                        }
                        Path("current_meeting_participants.json").write_text(
                            json.dumps(participant_data, ensure_ascii=False), 
                            encoding="utf-8"
                        )
                    except Exception as e:
                        logger.debug(f"Timeline write error: {e}")
                        
            except Exception as e:
                logger.debug(f"Speaker detection error: {e}")
                
            # Heartbeat (UI Active kalsın diye)
            update_status(running=True, recording=recorder_proc is not None)

            await asyncio.sleep(speaker_check_interval)

    except Exception as e:
        logger.error(f"Görev hatası: {traceback.format_exc()}")
    
    finally:
        # Temizlik
        update_status(status_message="Kapatılıyor...", recording=False)
        
        # ÖNCE toplantıdan çık (kullanıcı hemen görsün)
        if bot:
            logger.info("Toplantıdan çıkılıyor...")
            await bot.close()
            logger.info("✅ Toplantıdan çıkıldı.")
        
        # SONRA recorder'ı durdur (arka planda bekleyebilir)
        if recorder_proc:
            logger.info("Recorder durduruluyor (Graceful)...")
            Path("stop_recording.signal").touch()
            
            try:
                recorder_proc.wait(timeout=20)  # 20 saniye bekle
                logger.info("Recorder başarıyla kapandı.")
            except subprocess.TimeoutExpired:
                logger.warning("Recorder zaman aşımına uğradı, zorla kapatılıyor.")
                recorder_proc.kill()
            except Exception as e:
                logger.error(f"Recorder durdurma hatası: {e}")
                recorder_proc.kill()

        logger.info("Rapor oluşturuluyor...")
        update_status(status_message="Rapor hazırlanıyor...")
        
        # Rapor scriptini çalıştır
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
                
                # Geçici dosyaları temizle (latest_transcript.txt HARİÇ - backend kullanıyor)
                logger.info("Geçici dosyalar temizleniyor...")
                cleanup_files = [
                    "speaker_timeline.jsonl",
                    "speaker_activity_log.json",
                    # "latest_transcript.txt",  # KALSIN - backend kullanıyor
                    "current_meeting_participants.json",
                    "speaker_realtime_stats.json",
                    "debug_meet_speaker_detection.txt",
                    "ws_meet_debug.json"
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
        
        update_status(
            running=False, 
            recording=False,
            paused=False,
            platform="", # Platformu temizle ki UI ana ekrana dönsün
            status_message="Hazır" # "Görev Tamamlandı" yerine "Hazır"
        )
        
        # Task'i pasife çek
        if BOT_TASK_FILE.exists():
            try:
                t = json.loads(BOT_TASK_FILE.read_text("utf-8"))
                t["active"] = False
                BOT_TASK_FILE.write_text(json.dumps(t, indent=2), "utf-8")
            except:
                pass

async def main():
    logger.info("🤖 Meet Web Worker Başlatıldı")
    while True:
        if not BOT_TASK_FILE.exists():
            await asyncio.sleep(2)
            continue

        try:
            task = json.loads(BOT_TASK_FILE.read_text(encoding="utf-8"))
            if task.get("active") and task.get("platform") == "meet":
                url = task.get("meeting_url")
                if url:
                    await run_meet_task(url)
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
                asyncio.run(run_meet_task(url))
        else:
            # Döngü modu (Standalone)
            asyncio.run(main())
    except KeyboardInterrupt:
        pass
