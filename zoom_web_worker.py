import json
import time
import asyncio
import subprocess
import traceback
from pathlib import Path
from zoom_web_client import ZoomWebBot
import logging

# Platform abstraction
from platform_utils import IS_WINDOWS, IS_LINUX, setup_display

# Linux'ta display ayarla
setup_display()

# Windows-only imports (conditional)
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    try:
        import win32gui
        import win32con
        import win32process
        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
        win32gui = None
else:
    HAS_WIN32 = False
    win32gui = None
    ctypes = None
    wintypes = None

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Logger Setup with Rotating Handler
from logging.handlers import RotatingFileHandler
Path("logs").mkdir(exist_ok=True)

_file_handler = RotatingFileHandler(
    "logs/zoom_web_worker.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding='utf-8'
)
_file_handler.setFormatter(logging.Formatter('%(asctime)s - [ZOOM-WEB-WORKER] %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [ZOOM-WEB-WORKER] %(message)s',
    handlers=[_file_handler, logging.StreamHandler()]
)
logger = logging.getLogger("ZoomWebWorker")

BOT_TASK_FILE = Path("data/bot_task.json")
WORKER_STATUS_FILE = Path("data/worker_status.json")

RECORDER_SCRIPT = "zoom_bot_recorder.py"



if HAS_WIN32:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    GetWindowThreadProcessId.restype = wintypes.DWORD
    
    AttachThreadInput = user32.AttachThreadInput
    AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    AttachThreadInput.restype = wintypes.BOOL
    
    GetForegroundWindow = user32.GetForegroundWindow
    GetForegroundWindow.restype = wintypes.HWND
    
    SetForegroundWindow_API = user32.SetForegroundWindow
    SetForegroundWindow_API.argtypes = [wintypes.HWND]
    SetForegroundWindow_API.restype = wintypes.BOOL
    
    BringWindowToTop_API = user32.BringWindowToTop
    BringWindowToTop_API.argtypes = [wintypes.HWND]
    BringWindowToTop_API.restype = wintypes.BOOL
    
    ShowWindow_API = user32.ShowWindow
    ShowWindow_API.argtypes = [wintypes.HWND, ctypes.c_int]
    ShowWindow_API.restype = wintypes.BOOL
    
    SwitchToThisWindow = user32.SwitchToThisWindow
    SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    SwitchToThisWindow.restype = None
    
    SetActiveWindow_API = user32.SetActiveWindow
    SetActiveWindow_API.argtypes = [wintypes.HWND]
    SetActiveWindow_API.restype = wintypes.HWND
    
    SetFocus_API = user32.SetFocus
    SetFocus_API.argtypes = [wintypes.HWND]
    SetFocus_API.restype = wintypes.HWND
    
    GetCurrentThreadId = kernel32.GetCurrentThreadId
    GetCurrentThreadId.restype = wintypes.DWORD
    
    AllowSetForegroundWindow = user32.AllowSetForegroundWindow
    AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    AllowSetForegroundWindow.restype = wintypes.BOOL
    
    ASFW_ANY = -1


def find_browser_window(title_keywords=None):
    """Tarayıcı penceresini bulur. Sadece browser process'lerini döndürür."""
    if not HAS_WIN32:
        return None
    
    if title_keywords is None:
        title_keywords = ["Zoom", "zoom.us", "wc/", "Web'de Zoom"]
    
    # Bu kelimeleri içeren pencereler ATLANACAK (web arayüzü vs.)
    EXCLUDE_KEYWORDS = ["sesly", "toplantı botu", "panel", "bot panel", "localhost"]
    
    BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "chromium.exe", "opera.exe", "brave.exe"}
    candidates = []
    
    def enum_callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        
        title_lower = title.lower()
        
        # EXCLUDE kontrolü - bu pencereler atlanacak
        if any(ex.lower() in title_lower for ex in EXCLUDE_KEYWORDS):
            return True
        
        # Title match
        if not any(kw.lower() in title_lower for kw in title_keywords):
            return True
        
        # Process kontrolü
        _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            if HAS_PSUTIL:
                proc = psutil.Process(window_pid)
                proc_name = proc.name().lower()
                if proc_name in BROWSER_PROCESSES:
                    candidates.append((hwnd, title, proc_name))
            else:
                candidates.append((hwnd, title, "unknown"))
        except:
            pass
        return True
    
    win32gui.EnumWindows(enum_callback, None)
    
    if not candidates:
        return None
    
    # Öncelik: Zoom > diğer
    for hwnd, title, _ in candidates:
        if "zoom" in title.lower():
            return hwnd
    
    return candidates[0][0]


def force_foreground(hwnd):
    """Windows Foreground Lock'u bypass ederek pencereyi zorla öne getirir."""
    if not HAS_WIN32 or not hwnd:
        return False
    
    if not win32gui.IsWindow(hwnd):
        return False
    
    try:
        # Tüm processlere izin ver
        AllowSetForegroundWindow(ASFW_ANY)
        
        # Thread bilgileri
        foreground_hwnd = GetForegroundWindow()
        foreground_thread = 0
        if foreground_hwnd:
            pid = wintypes.DWORD()
            foreground_thread = GetWindowThreadProcessId(foreground_hwnd, ctypes.byref(pid))
        
        target_pid = wintypes.DWORD()
        target_thread = GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
        current_thread = GetCurrentThreadId()
        
        # Thread'leri bağla
        attached_fg = False
        attached_tgt = False
        
        if foreground_thread and foreground_thread != current_thread:
            attached_fg = AttachThreadInput(current_thread, foreground_thread, True)
        if target_thread and target_thread != current_thread:
            attached_tgt = AttachThreadInput(current_thread, target_thread, True)
        
        try:
            # Alt key trick - Foreground lock'u kır
            user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            time.sleep(0.01)
            user32.keybd_event(0x12, 0, 2, 0)  # Alt up
            time.sleep(0.01)
            
            # Çoklu yöntem
            SwitchToThisWindow(hwnd, True)
            SetForegroundWindow_API(hwnd)
            BringWindowToTop_API(hwnd)
            SetActiveWindow_API(hwnd)
            SetFocus_API(hwnd)
            
        finally:
            if attached_fg:
                AttachThreadInput(current_thread, foreground_thread, False)
            if attached_tgt:
                AttachThreadInput(current_thread, target_thread, False)
        
        time.sleep(0.05)
        return GetForegroundWindow() == hwnd
        
    except Exception as e:
        logger.error(f"[FOCUS] force_foreground error: {e}")
        return False


def ensure_maximized(hwnd):
    """Pencerenin kesinlikle maximize olmasını sağlar."""
    if not HAS_WIN32 or not hwnd:
        return False
    
    try:
        placement = win32gui.GetWindowPlacement(hwnd)
        current_state = placement[1]
        
        if current_state != win32con.SW_SHOWMAXIMIZED:
            # Önce RESTORE, sonra MAXIMIZE
            ShowWindow_API(hwnd, win32con.SW_RESTORE)
            time.sleep(0.1)
            ShowWindow_API(hwnd, win32con.SW_MAXIMIZE)
            time.sleep(0.05)
        
        placement = win32gui.GetWindowPlacement(hwnd)
        return placement[1] == win32con.SW_SHOWMAXIMIZED
    except Exception as e:
        logger.error(f"[FOCUS] ensure_maximized error: {e}")
        return False


def bring_chromium_to_front():
    """Chromium/Chrome penceresini bulup öne getirir (BULLETPROOF versiyon - --app mode destekli)."""
    if not HAS_WIN32:
        logger.warning("[FOCUS] pywin32 yüklü değil")
        return
    
    logger.info("[FOCUS] Pencere arama başladı...")
    
    for attempt in range(5):
        # 1. Pencereyi bul (--app modunda farklı başlıklar olabilir)
        # --app modunda: sadece domain veya meeting ID görünebilir
        hwnd = find_browser_window(title_keywords=["Zoom", "zoom.us", "wc/", "us05web", "Meeting", "Web'de Zoom"])
        
        if not hwnd:
            logger.info(f"[FOCUS] Pencere bulunamadı, bekleniyor... (deneme {attempt+1}/5)")
            time.sleep(0.2)
            continue
        
        title = win32gui.GetWindowText(hwnd)
        logger.info(f"[FOCUS] HEDEF PENCERE: '{title}' (HWND: {hwnd})")
        
        # 2. Önce MAXIMIZE
        max_result = ensure_maximized(hwnd)
        logger.info(f"[FOCUS] Maximize: {'✓' if max_result else '⚠'}")
        
        # 3. Foreground'a getir
        fg_result = force_foreground(hwnd)
        
        if fg_result:
            logger.info(f"[FOCUS] ✅ Pencere öne getirildi (deneme {attempt+1})")
            return
        
        logger.info(f"[FOCUS] ⚠ Deneme {attempt+1} başarısız, tekrar deneniyor...")
        time.sleep(0.5)
    
    logger.warning("[FOCUS] ❌ Tüm denemeler başarısız")

def update_status(**kwargs):
    """worker_status.json dosyasını güncelle."""
    status = {
        "running": False,
        "recording": False,
        "paused": False,
        "status_message": "",
        "platform": "zoom",
        "timestamp": time.time(),
    }

    if WORKER_STATUS_FILE.exists():
        try:
            old = json.loads(WORKER_STATUS_FILE.read_text(encoding="utf-8"))
            status.update(old)
        except Exception:
            pass

    status.update(kwargs)
    
    # running key'i sistem genelinde kullanılıyor
    # if "running" not in status and "zoom_running" in kwargs:
    #      status["running"] = kwargs["zoom_running"]

    try:
        WORKER_STATUS_FILE.write_text(
            json.dumps(status, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"Status update error: {e}")

async def run_zoom_web_task(meeting_url, bot_name="Sesly Bot", password=None):
    """Zoom Web görevini yürütür."""
    bot = None
    recorder_proc = None

    try:
        # Cleanup
        BOT_COMMAND_FILE = Path("data/bot_command.json")
        STOP_SIGNAL_FILE = Path("stop_recording.signal")
        
        if BOT_COMMAND_FILE.exists():
            try: BOT_COMMAND_FILE.unlink()
            except: pass

        if STOP_SIGNAL_FILE.exists():
            try: STOP_SIGNAL_FILE.unlink()
            except: pass

        # Veri temizliği
        files_to_clean = ["latest_transcript.txt", "current_meeting_participants.json", "speaker_timeline.jsonl"]
        for fname in files_to_clean:
            f = Path(fname)
            if f.exists():
                try: f.unlink()
                except: pass

        # 1. Botu Başlat
        logger.info(f"Zoom WEB görevi başlıyor: {meeting_url}")
        update_status(running=True, status_message="Zoom tarayıcı açılıyor...")
        
        bot = ZoomWebBot(meeting_url, bot_name=bot_name, password=password)
        await bot.start()
        
        # PENCERE ODAKLA (İlk açılış)
        time.sleep(1)
        bring_chromium_to_front()

        # 2. Toplantıya Katıl
        update_status(status_message="Toplantıya giriliyor...")
        joined = await bot.join_meeting()
        
        # PENCERE ODAKLA (Katılım sonrası)
        time.sleep(0.1)
        bring_chromium_to_front()
        
        if not joined:
            # join_meeting() artık bekleme odasını içerde işliyor
            # False dönerse ya timeout olmuş ya da hata var
            logger.error("Toplantıya katılınamadı.")
            update_status(
                running=False, 
                status_message="Katılım başarısız!", 
                error="Toplantıya katılınamadı. Link geçersiz veya toplantı bekleme odası zaman aşımına uğradı."
            )

            await bot.close()
            sys.exit(1) # Worker'ı hata koduyla kapat ki sistem anlasın/takılmasın

        logger.info("Toplantıya giriş başarılı.")
        update_status(status_message="Toplantıda - Hazırlık yapılıyor...")

        # POST-JOIN ACTIONS
        await asyncio.sleep(0.1)
        
        # 1. PENCERE ODAKLA
        logger.info("Pencere öne getiriliyor (POST-JOIN)...")
        try:
            bring_chromium_to_front()
            # Toolbar uyandırma
            try:
                await bot.page.mouse.move(500, 500)
                await asyncio.sleep(0.5)
                await bot.page.mouse.move(500, 600)
            except: pass
            await asyncio.sleep(1) # Render için kısa bekle
        except Exception as e:
            logger.warning(f"⚠ Pencere öne getirme hatası: {e}")
        
        # 2. CHAT MESAJI GÖNDER (Önce Mesaj)
        try:
            intro_msg = "Merhaba! 👋 Ben Sesly Bot. Bu toplantıyı kaydediyorum ve transkript oluşturuyorum. 🤖"
            success = await bot.send_chat_message(intro_msg)
            
            if success:
                logger.info("✓ Giriş mesajı gönderildi")
                
                # Chat'i Kapat (Hızlı)
                await asyncio.sleep(0.5)
                await bot.close_chat_panel()
                logger.info("✓ Chat paneli kapatıldı")
            else:
                logger.warning("⚠ Mesaj gönderilemedi")
        except Exception as e:
            logger.error(f"Mesaj gönderme hatası: {e}")
        
        # 3. KATILIMCI PANELİNİ AÇ
        try:
            await asyncio.sleep(0.5)
            success = await bot.open_participants_panel()
            
            if success:
                logger.info("✓ Katılımcı paneli açıldı")
            else:
                logger.warning("⚠ Katılımcı paneli açılamadı")
        except Exception as e:
            logger.error(f"Katılımcı paneli açma hatası: {e}")
        
        # 4. KAYDI BAŞLAT (Her şey hazır olunca)
        # Katılımcı listesi açıkken başlatıyoruz ki konuşmacı tespiti net olsun
        logger.info("Recorder başlatılıyor...")
        try:
            recorder_proc = subprocess.Popen(["python", RECORDER_SCRIPT, "--platform", "zoom"])
            update_status(recording=True, status_message="🔴 Kayıt Alınıyor")
        except Exception as e:
            logger.error(f"Recorder hatası: {e}")
            
        await asyncio.sleep(1)


        # 4. Döngü
        logger.info("Toplantı izleniyor...")
        speaker_check_interval = 0.5
        last_participant_log = 0  # Son katılımcı log zamanı
        
        while True:
            # A. Task İptali Kontrolü - KALDIRILDI!
            # Bu kontrol gereksiz ve sorun yaratıyor:
            # - Worker subprocess olarak çalışıyor, sistem.py tarafından başlatılıyor
            # - Stop komutu bot_command.json ile geliyor (B bloğu)
            # - Server bot_task.json'ı worker çalışırken tekrar yaratıyor
            # - Bu da "active: false" ile yaratılırsa worker hemen çıkıyor (YANLIŞ!)
            # ÇÖZ ÜM: Bu kontrolü tamamen kaldır, sadece bot_command.json'a bak
            
            # if BOT_TASK_FILE.exists():
            #     try:
            #         task = json.loads(BOT_TASK_FILE.read_text(encoding="utf-8"))
            #         if not task.get("active", False):
            #             logger.info("Görev pasife çekildi (Task file).")
            #             break
            #     except: 
            #         pass

            # B. Komut Kontrolü (Stop)
            if BOT_COMMAND_FILE.exists():
                try:
                    cmd_data = json.loads(BOT_COMMAND_FILE.read_text(encoding="utf-8"))
                    if not cmd_data.get("processed"):
                        cmd = cmd_data.get("command")
                        if cmd == "stop":
                            logger.info("STOP komutu alındı.")
                            cmd_data["processed"] = True
                            BOT_COMMAND_FILE.write_text(json.dumps(cmd_data), encoding="utf-8")
                            break
                except: pass

            # C. Toplantı Bitti mi? (YENİ)
            if await bot.check_meeting_ended():
                logger.info("Toplantı bitişi tespit edildi.")
                # Geçersiz toplantı mı kontrol et
                if bot.end_reason and bot.end_reason != "normal":
                    update_status(
                        running=False,
                        error=bot.end_reason
                    )
                break

            # D. Konuşmacı Tespiti (DOM polling)
            try:
                speakers = await bot.get_active_speakers()
                
                # FALLBACK: Eğer aktif konuşmacı bulunamazsa, tüm katılımcıları al
                all_participants = []
                if not speakers:
                    try:
                        all_participants = await bot.get_all_participants()
                        if all_participants and (time.time() - last_participant_log > 60):
                            logger.info(f"📋 Katılımcılar ({len(all_participants)}): {', '.join(all_participants[:5])}...")
                            last_participant_log = time.time()
                    except: pass
                
                if speakers:
                    logger.info(f"🗣️ Konuşanlar: {', '.join(speakers)}")
                    
                    # JSON'a kaydet (current state)
                    data = {
                        "participants": speakers, 
                        "active_speakers": speakers,
                        "timestamp": time.time(), 
                        "method": "zoom-web-dom"
                    }
                    try:
                        Path("current_meeting_participants.json").write_text(
                            json.dumps(data, ensure_ascii=False), encoding="utf-8"
                        )
                    except: pass
                    
                    # Speaker activity log (JSON array - legacy)
                    try:
                        log_entry = {
                            "timestamp": time.time(),
                            "platform": "zoom",
                            "speakers": speakers,
                            "method": "dom-based"
                        }
                    
                        # Append to activity log (legacy JSON)
                        activity_log = Path("speaker_activity_log.json")
                        if activity_log.exists():
                            try:
                                logs = json.loads(activity_log.read_text(encoding="utf-8"))
                            except:
                                logs = []
                        else:
                            logs = []
                    
                        logs.append(log_entry)
                        activity_log.write_text(
                            json.dumps(logs, ensure_ascii=False, indent=2),
                            encoding="utf-8"
                        )
                    except Exception as e:
                        logger.error(f"Activity log hatası: {e}")
                    
                    # TIMELINE JSONL (Yeni - transkript eşleştirme için)
                    try:
                        from datetime import datetime
                        timeline_entry = {
                            "ts": time.time(),
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "speakers": speakers
                        }
                        timeline_file = Path("speaker_timeline.jsonl")
                        with open(timeline_file, "a", encoding="utf-8") as tf:
                            tf.write(json.dumps(timeline_entry, ensure_ascii=False) + "\n")
                    except Exception as e:
                        logger.error(f"Timeline JSONL hatası: {e}")
                
                # Katılımcı listesini her durumda kaydet (transkript için context)
                elif all_participants:
                    data = {
                        "participants": all_participants,
                        "active_speakers": [],
                        "timestamp": time.time(),
                        "method": "zoom-web-participant-list"
                    }
                    try:
                        Path("current_meeting_participants.json").write_text(
                            json.dumps(data, ensure_ascii=False), encoding="utf-8"
                        )
                    except: pass

                        
            except Exception as e:
                logger.error(f"Speaker loop hatası: {e}")

            # E. Status Update (Heartbeat)
            update_status(running=True, recording=recorder_proc is not None)

            await asyncio.sleep(speaker_check_interval)

    except Exception as e:
        logger.error(f"Genel hata: {e}")
        traceback.print_exc()
    
    finally:
        # Temizlik
        update_status(status_message="Kapatılıyor...", recording=False)
        
        # 1. Kaydı Durdur (Graceful)
        if recorder_proc:
            logger.info("Recorder durduruluyor (Graceful)...")
            Path("stop_recording.signal").touch()
            try:
                recorder_proc.wait(timeout=60) # Upload süresi için 60sn
                logger.info("Recorder başarıyla kapandı.")
            except:
                logger.warning("Recorder zorla kapatılıyor...")
                recorder_proc.kill()
        
        # 2. Browser'ı Kapat
        if bot:
            await bot.close()

        # 3. Rapor Oluştur (YENİ)
        logger.info("Rapor oluşturuluyor...")
        update_status(status_message="Rapor hazırlanıyor...")
        try:
            # Rapor scriptini çalıştır -u unbuffered
            # Rapor script dosyasını import etmek yerine subprocess ile çalıştırıyoruz ki clean env olsun
            result = subprocess.run(
                ["python", "-u", "rapor.py"], 
                capture_output=True, 
                text=True,
                encoding='utf-8',
                check=False
            )
            
            if result.returncode == 0:
                logger.info("Rapor başarıyla oluşturuldu.")
                logger.info(result.stdout)
                
                # Temizlik
                logger.info("Geçici dosyalar temizleniyor...")
                cleanup_files = [
                    "current_meeting_participants.json",
                    "stop_recording.signal"
                ]
                for f in cleanup_files:
                     try: Path(f).unlink(); logger.info(f"  ✓ {f} silindi") 
                     except: pass
            else:
                logger.error(f"Rapor hatası: {result.stderr}")

        except Exception as e:
            logger.error(f"Rapor oluşturma hatası: {e}")

        update_status(running=False, status_message="Hazır")
        logger.info("Görev tamamlandı.")
        
        # Task'i pasife çek (UI güncellemesi için KRITIK)
        if BOT_TASK_FILE.exists():
            try:
                t = json.loads(BOT_TASK_FILE.read_text("utf-8"))
                t["active"] = False
                BOT_TASK_FILE.write_text(json.dumps(t, indent=2), "utf-8")
                logger.info("✓ bot_task.json pasife çekildi")
            except:
                pass

if __name__ == "__main__":
    import sys
    
    url = ""
    name = "Sesly Bot"
    
    if len(sys.argv) > 1:
        url = sys.argv[1]
    if len(sys.argv) > 2:
        name = sys.argv[2]
        
    password = None
    if len(sys.argv) > 3:
        password = sys.argv[3]
        
    if url:
        # Run async task
        try:
            asyncio.run(run_zoom_web_task(url, name, password))
        except KeyboardInterrupt:
            pass
    else:
        print("Kullanım: python zoom_web_worker.py <meeting_url> [bot_name] [password]")
