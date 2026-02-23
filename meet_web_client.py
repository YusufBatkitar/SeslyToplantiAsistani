
import asyncio
import time
import json
import logging
import os
import signal
from pathlib import Path
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Platform abstraction
from platform_utils import IS_WINDOWS, IS_LINUX, get_chrome_options_for_platform, setup_display

# Linux'ta display ayarla
setup_display()

# Logger Setup
logger = logging.getLogger("MeetWebClient")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[MEET-SELENIUM] %(message)s'))
logger.addHandler(handler)

class MeetWebBot:
    def __init__(self, meeting_url, bot_name="Sesly Bot"):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self.driver = None
        
        # WebSocket speaker tracking (simulated)
        self.ws_active_speakers = []
        
        # Timeout takibi için
        self.waiting_start_time = None
        self.is_running = False
        self.end_reason = None  # Toplantı sona erme sebebi (normal/invalid link)

    async def start(self):
        """Selenium ve Chrome'u başlatır."""
        logger.info("undetected-chromedriver başlatılıyor...")
        
        # Chrome options
        options = uc.ChromeOptions()
        options.add_argument("--use-fake-ui-for-media-stream")
        options.add_argument("--disable-notifications")
        options.add_argument("--autoplay-policy=no-user-gesture-required")  # WebRTC için
        options.add_argument("--disable-infobars")
        
        # Platform-specific options
        if IS_LINUX:
            # Xvfb ile headful mod (speaker detection için headless kullanmıyoruz)
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
        
        # undetected-chromedriver başlat
        self.driver = uc.Chrome(options=options, use_subprocess=True)
        
        if not IS_LINUX:
            self.driver.maximize_window()
        
        # WebRTC Audio Track Injection (MEET AÇILMADAN ÖNCE)
        try:
            logger.info("WebRTC RTCPeerConnection override ekleniyor...")
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    window._meetPCs = [];
                    window._volumeData = {};
                    
                    const OriginalPC = window.RTCPeerConnection;
                    window.RTCPeerConnection = function(...args) {
                        const pc = new OriginalPC(...args);
                        window._meetPCs.push(pc);
                        return pc;
                    };
                    
                    // Volume analizi fonksiyonu
                    window.getMeetVolumes = () => {
                        const volumes = {};
                        window._meetPCs.forEach(pc => {
                            pc.getReceivers().forEach(r => {
                                if (r.track && r.track.kind === 'audio') {
                                    if (!r._analyser) {
                                        try {
                                            const ctx = new AudioContext();
                                            const src = ctx.createMediaStreamSource(new MediaStream([r.track]));
                                            const analyser = ctx.createAnalyser();
                                            analyser.fftSize = 512;
                                            src.connect(analyser);
                                            r._analyser = analyser;
                                            r._ctx = ctx;
                                        } catch(e) { return; }
                                    }
                                    const data = new Uint8Array(r._analyser.frequencyBinCount);
                                    r._analyser.getByteFrequencyData(data);
                                    const vol = data.reduce((a,b)=>a+b,0) / data.length;
                                    volumes[r.track.id] = vol;
                                }
                            });
                        });
                        return volumes;
                    };
                """
            })
            logger.info("✅ WebRTC injection başarılı")
        except Exception as e:
            logger.warning(f"CDP injection hatası: {e} (DOM fallback kullanılacak)")
        
        self.is_running = True
        
        # Pencereyi ÖNE GETİR (Teams pattern - 1 kez)
        try:
            await asyncio.sleep(1)  # Başlığın gelmesini bekle
            
            # ÖNCELİKLE: Web arayüzünü minimize et (asıl sorun bu!)
            self._minimize_web_interface()
            
            # Sonra Meet'i öne getir
            self._bring_to_front_force(target_title=["Meet", "Google Meet"])
        except Exception as e:
            logger.warning(f"Pencere öne getirme hatası: {e}")
        
        logger.info("Tarayıcı hazır, web arayüzü minimize edildi ve Meet öne getirildi.")
    
    def _minimize_web_interface(self):
        """Web arayüzü (127.0.0.1:19001) penceresini minimize eder."""
        try:
            import win32gui
            import win32con
            import win32process
            import psutil
            
            def callback(hwnd, windows):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    
                    title = win32gui.GetWindowText(hwnd)
                    _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                    
                    try:
                        proc_name = psutil.Process(window_pid).name().lower()
                    except:
                        proc_name = "unknown"
                    
                    # Browser process + Web arayüzü title kontrolü
                    if proc_name in ["chrome.exe", "msedge.exe", "firefox.exe", "opera.exe"]:
                        # 127.0.0.1:19001 veya "Toplantı Botu Kontrol Paneli" var mı?
                        if "127.0.0.1" in title or "19001" in title or "Toplantı Botu" in title or "SESLY" in title:
                            logger.info(f"Web arayüzü bulundu, minimize ediliyor: '{title}'")
                            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                            windows.append(hwnd)
                except:
                    pass
            
            hwnds = []
            win32gui.EnumWindows(callback, hwnds)
            
            if hwnds:
                logger.info(f"✅ {len(hwnds)} web arayüzü penceresi minimize edildi")
            else:
                logger.info("Web arayüzü penceresi bulunamadı (zaten kapalı olabilir)")
                
        except Exception as e:
            logger.warning(f"Web arayüzü minimize hatası: {e}")

    def _bring_to_front_force(self, pid=None, target_title=None):
        """Windows API kullanarak pencereyi zorla öne getirir (AttachThreadInput Hack)."""
        try:
            import win32gui
            import win32process
            import win32con
            import ctypes
            from ctypes import wintypes

            # Kullanıcı tanımlı başlıklar veya varsayılanlar
            search_titles = target_title if target_title else ["Google Meet", "Meet", "Google", "meet.google.com"]
            if isinstance(search_titles, str): search_titles = [search_titles]

            def callback(hwnd, windows):
                try:
                    if not win32gui.IsWindowVisible(hwnd): return
                    
                    title = win32gui.GetWindowText(hwnd)
                    _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                    
                    # Process Check
                    try:
                        import psutil
                        proc_name = psutil.Process(window_pid).name().lower()
                    except:
                        proc_name = "unknown"
                    
                    if proc_name not in ["chrome.exe", "msedge.exe", "chromium.exe", "opera.exe", "brave.exe"]:
                        return

                    # Match Logic
                    match = False
                    if pid and window_pid == pid: match = True
                    elif any(t.lower() in title.lower() for t in search_titles): match = True
                    
                    if match:
                        logger.info(f"[FOCUS CANDIDATE] '{title}' (PID: {window_pid})")
                        windows.append(hwnd)
                except: pass

            hwnds = []
            win32gui.EnumWindows(callback, hwnds)
            
            if not hwnds:
                logger.warning("⚠️ Chrome/Meet penceresi bulunamadı!")
                return

            target_hwnd = hwnds[0]
            logger.info(f"🎯 Hedef Pencere: {target_hwnd} - Focus Deneniyor...")

            # --- NUCLEAR FOCUS OPTION: AttachThreadInput ---
            try:
                user32 = ctypes.windll.user32
                
                # Mevcut foreground pencerenin thread ID'si
                foreground_hwnd = user32.GetForegroundWindow()
                foreground_thread_id = user32.GetWindowThreadProcessId(foreground_hwnd, None)
                
                # Bizim thread ID'miz
                current_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
                
                # Eğer farklıysa attach et
                if foreground_thread_id != current_thread_id:
                    user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)
                    
                    # Window operasyonları
                    win32gui.ShowWindow(target_hwnd, win32con.SW_MAXIMIZE)
                    win32gui.SetForegroundWindow(target_hwnd)
                    win32gui.SetFocus(target_hwnd)
                    
                    # Detach
                    user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)
                    logger.info("✅ AttachThreadInput ile focus alındı!")
                else:
                    # Zaten aynı thread (veya biziz), direkt getir
                    win32gui.ShowWindow(target_hwnd, win32con.SW_MAXIMIZE)
                    win32gui.SetForegroundWindow(target_hwnd)
                    logger.info("✅ Doğrudan focus alındı.")
                    
                # Ekstra Garanti: Topmost Toggle
                win32gui.SetWindowPos(target_hwnd, win32con.HWND_TOPMOST, 0,0,0,0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                win32gui.SetWindowPos(target_hwnd, win32con.HWND_NOTOPMOST, 0,0,0,0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)

            except Exception as e:
                logger.warning(f"Nuclear focus hatası: {e}")
                # Fallback
                try:
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(target_hwnd)
                except: pass

        except Exception as e:
            logger.warning(f"Genel focus hatası: {e}")

    def _check_stop_command(self):
        """stop komutu gelip gelmediğini kontrol eder."""
        try:
            cmd_path = Path("data/bot_command.json")
            if cmd_path.exists():
                data = json.loads(cmd_path.read_text(encoding="utf-8"))
                if data.get("command") == "stop" and not data.get("processed"):
                    logger.info("🛑 İşlem sırasında STOP komutu algılandı.")
                    return True
        except: pass
        return False

    async def _dismiss_popups(self):
        """Meet popup'larını kapatır (Anladım, Got it, Kapat, Dismiss vb.)."""
        try:
            # Popup butonları için aranacak metinler
            dismiss_texts = [
                "anladım", "anladim", "got it", "dismiss", "kapat", "close",
                "tamam", "ok", "understood", "i understand"
            ]
            
            # Tüm butonları tara
            all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
            
            for btn in all_buttons:
                try:
                    if not btn.is_displayed():
                        continue
                        
                    text = (btn.text or "").lower().strip()
                    aria_label = (btn.get_attribute("aria-label") or "").lower()
                    
                    for dismiss_text in dismiss_texts:
                        if dismiss_text in text or dismiss_text in aria_label:
                            logger.info(f"🔔 Popup kapatılıyor: '{btn.text or aria_label}'")
                            btn.click()
                            await asyncio.sleep(0.5)
                            return True
                except:
                    continue
                    
            # Div butonlarını da kontrol et (role=button)
            div_buttons = self.driver.find_elements(By.XPATH, "//div[@role='button']")
            for btn in div_buttons:
                try:
                    if not btn.is_displayed():
                        continue
                        
                    text = (btn.text or "").lower().strip()
                    aria_label = (btn.get_attribute("aria-label") or "").lower()
                    
                    for dismiss_text in dismiss_texts:
                        if dismiss_text in text or dismiss_text in aria_label:
                            logger.info(f"🔔 Popup kapatılıyor (div): '{btn.text or aria_label}'")
                            btn.click()
                            await asyncio.sleep(0.5)
                            return True
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Popup kapatma hatası: {e}")
        
        return False

    async def join_meeting(self):
        """Meet toplantısına katılım akışı."""
        try:
            # 0. Başta kontrol
            if self._check_stop_command(): return False

            # URL validation
            meeting_url = self.meeting_url
            if not meeting_url.startswith("http"):
                meeting_url = f"https://{meeting_url}"
                logger.info(f"URL düzeltildi: {meeting_url}")
            
            logger.info(f"Meet linki açılıyor: {meeting_url}")
            self.driver.get(meeting_url)
            
            await asyncio.sleep(3)
            if self._check_stop_command(): return False
            
            # 1. İsim girme
            try:
                logger.info("İsim alanı aranıyor...")
                name_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@type='text']"))
                )
                name_input.clear()
                name_input.send_keys(self.bot_name)
                logger.info(f"İsim girildi: {self.bot_name}")
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"İsim girme hatası (devam ediliyor): {e}")
            
            if self._check_stop_command(): return False

            # 2. ÖNCE Mikrofon ve Kamera Kapatma (HİBRİT YÖNTEM: Tıklama + Kısayol)
            try:
                logger.info("Mikrofon ve kamera kapatılıyor (Hibrit)...")
                await asyncio.sleep(2)
                
                # A. YÖNTEM: Butonlara Tıklama (Öncelikli)
                try:
                    # Mikrofon
                    mic_clicked = False
                    mics = self.driver.find_elements(By.XPATH, 
                        "//div[@role='button'][contains(@aria-label, 'ikrofon') or contains(@aria-label, 'icrophone')] | "
                        "//div[@role='button']//i[contains(text(), 'mic')] | "
                        "//div[@role='button']//*[@data-icon='microphone']"
                    )
                    for btn in mics:
                        try:
                            # Ana buton div'ini bul
                            p_btn = btn.find_element(By.XPATH, "./ancestor-or-self::div[@role='button']")
                            # Açık mı? (aria-pressed/data-is-muted kontrolü zor, direkt basalım veya label'a bakalım)
                            # Meet: "Turn off microphone" (Kapat) yazar eğer açıksa
                            label = (p_btn.get_attribute("aria-label") or "").lower()
                            if "kapat" in label or "turn off" in label:
                                p_btn.click()
                                logger.info("✅ Mikrofon tıklandı (Listeden)")
                                mic_clicked = True
                                await asyncio.sleep(0.5)
                                break
                        except: pass
                    
                    if not mic_clicked:
                        # Kısayol dene
                        logger.info("⚠️ Mikrofon butonu bulunamadı, CTRL+D deneniyor...")
                        self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.CONTROL, 'd')
                        await asyncio.sleep(1)

                    # Kamera
                    cam_clicked = False
                    cams = self.driver.find_elements(By.XPATH, 
                        "//div[@role='button'][contains(@aria-label, 'amera') or contains(@aria-label, 'ideo')] | "
                        "//div[@role='button']//i[contains(text(), 'videocam')] | "
                        "//div[@role='button']//*[@data-icon='camera']"
                    )
                    for btn in cams:
                        try:
                            p_btn = btn.find_element(By.XPATH, "./ancestor-or-self::div[@role='button']")
                            label = (p_btn.get_attribute("aria-label") or "").lower()
                            if "kapat" in label or "turn off" in label:
                                p_btn.click()
                                logger.info("✅ Kamera tıklandı (Listeden)")
                                cam_clicked = True
                                await asyncio.sleep(0.5)
                                break
                        except: pass
                        
                    if not cam_clicked:
                        logger.info("⚠️ Kamera butonu bulunamadı, CTRL+E deneniyor...")
                        self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.CONTROL, 'e')
                        await asyncio.sleep(1)

                except Exception as e:
                    logger.warning(f"Buton tıklama hatası: {e}")
                    # Hata olsa bile KISAYOL gönder (Yedek)
                    try:
                        body = self.driver.find_element(By.TAG_NAME, "body")
                        body.send_keys(Keys.CONTROL, 'd')
                        await asyncio.sleep(0.5)
                        body.send_keys(Keys.CONTROL, 'e')
                    except: pass

            except Exception as e:
                logger.warning(f"AV kapatma genel hatası: {e}")

            if self._check_stop_command(): return False
            
            # 3. SONRA Hoparlör → VB INPUT/CABLE INPUT seçimi
            try:
                logger.info("Hoparlör ayarı yapılıyor...")
                await asyncio.sleep(2)
                
                # Hoparlör dropdown butonunu bul
                all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                
                speaker_dropdown_clicked = False
                for btn in all_buttons:
                    try:
                        aria_label = (btn.get_attribute("aria-label") or "").lower()
                        if "hoparlör" in aria_label or "speaker" in aria_label:
                            btn.click()
                            logger.info(f"Hoparlör dropdown tıklandı: {aria_label}")
                            speaker_dropdown_clicked = True
                            await asyncio.sleep(2)
                            break
                    except:
                        continue
                
                if self._check_stop_command(): return False

                if speaker_dropdown_clicked:
                    # Dropdown açıldı - Bekle ve Ara
                    logger.info("Dropdown açıldı, seçeneklerin yüklenmesi bekleniyor...")
                    
                    options = []
                    # 5 saniye boyunca seçeneklerin gelmesini bekle
                    for _ in range(10):
                        if self._check_stop_command(): return False # Loop içinde kontrol
                        
                        options = self.driver.find_elements(By.XPATH, 
                            "//li[@role='option'] | //div[@role='option'] | //ul/li | //div[contains(@class, 'z80M1')]"
                        )
                        if options and len(options) > 0:
                            visible_options = [o for o in options if o.is_displayed()]
                            if visible_options:
                                options = visible_options
                                break
                        await asyncio.sleep(0.5)
                    
                    found = False
                    
                    if options:
                        logger.info(f"Ses seçenekleri ({len(options)}): {[o.text for o in options]}")
                        
                        # 1. Öncelik: Tam "Cable Input" araması ama "16" falan olmasın
                        for opt in options:
                            text = opt.text.lower()
                            if "cable input" in text and "16" not in text and not any(char.isdigit() for char in text):
                                logger.info(f"✅ Hoparlör (Temiz Cable Input) bulundu: {opt.text}")
                                opt.click()
                                found = True
                                await asyncio.sleep(1)
                                break
                        
                        # 2. Öncelik: "VB-Audio" ve "Input" (In16'yı elemek için - Rakam kontrolü ile)
                        if not found:
                            for opt in options:
                                text = opt.text.lower()
                                if "vb-audio" in text and "input" in text:
                                    if "16" in text or "in 16" in text: continue
                                    logger.info(f"✅ Hoparlör (VB-Audio Input) bulundu: {opt.text}")
                                    opt.click()
                                    found = True
                                    await asyncio.sleep(1)
                                    break
                                    
                        # 3. "onun altındakini seçmesi lazım" mantığı
                        if not found:
                             bad_index = -1
                             for i, opt in enumerate(options):
                                 if "16" in opt.text:
                                     bad_index = i
                                     break
                             
                             if bad_index != -1 and bad_index + 1 < len(options):
                                 target = options[bad_index + 1]
                                 logger.info(f"✅ '16' nın altındaki seçenek seçiliyor: {target.text}")
                                 target.click()
                                 found = True
                                 await asyncio.sleep(1)
                                 
                        if not found and options:
                             last_opt = options[-1]
                             logger.info(f"⚠️ Son seçenek seçiliyor: {last_opt.text}")
                             last_opt.click()
                             found = True
                             await asyncio.sleep(1)

                    else:
                        logger.warning("⚠️ Dropdown seçenekleri boş!")
                else:
                    logger.warning("⚠️ Hoparlör dropdown bulunamadı")
                    
            except Exception as e:
                logger.warning(f"Hoparlör ayarı hatası: {e}")
            
            await asyncio.sleep(2)
            if self._check_stop_command(): return False
            
            # 3. Join butonu
            try:
                logger.info("Join butonu aranıyor...")
                join_btn = None
                buttons = self.driver.find_elements(By.TAG_NAME, "button")
                
                for btn in buttons:
                    text = btn.text.lower()
                    aria_label = (btn.get_attribute("aria-label") or "").lower()
                    if any(keyword in text or keyword in aria_label for keyword in ["join", "katıl", "ask to join"]):
                        join_btn = btn
                        break
                
                if join_btn:
                    join_btn.click()
                    logger.info("✅ Join butonuna tıklandı")
                    await asyncio.sleep(5)
                else:
                    logger.error("Join butonu bulunamadı!")
                    return False
                
            except Exception as e:
                logger.error(f"Join butonu hatası: {e}")
                self.driver.save_screenshot("debug_meet_join_fail.png")
                return False
            
            # 4. Katılım doğrulama ve BEKLEME ODASI KONTROLÜ (10 Dakika Timeout)
            logger.info("Katılım durumu kontrol ediliyor (Bekleme Odası Timeout: 10dk)...")
            
            start_time = time.time()
            wait_timeout = 600  # 10 dakika (600 saniye)
            waiting_room_logged = False
            
            while True:
                current_time = time.time()
                elapsed = current_time - start_time
                
                if elapsed > wait_timeout:
                    logger.error("❌ Bekleme süresi (10dk) doldu! Toplantıya alınmadı, çıkılıyor.")
                    return False
                
                # Başarılı Katılım Kontrolü (KESİN KANIT: Chat veya Katılımcı Listesi)
                # Bekleme odasında da "Leave" butonu olabiliyor. O yüzden "Chat" veya "Kişiler" butonunu arayalım.
                try:
                    in_meeting_indicators = self.driver.find_elements(By.XPATH, 
                        "//button[contains(@aria-label, 'chat') or contains(@aria-label, 'sohbet')] | "
                        "//button[contains(@aria-label, 'participant') or contains(@aria-label, 'kişi')] | "
                        "//div[@role='button']//i[contains(text(), 'chat_bubble')] | "
                        "//div[@role='button']//i[contains(text(), 'people')]"
                    )
                    
                    if in_meeting_indicators:
                        # Görünür mü kontrol et
                        visible_btn = [btn for btn in in_meeting_indicators if btn.is_displayed()]
                        if visible_btn:
                            logger.info("✅ Toplantıya başarıyla katıldı! (Chat/Kişiler butonu görüldü)")
                            return True
                except: pass
                
                # Bekleme Odası Kontrolü
                try:
                    page_source = self.driver.page_source.lower()
                    waiting_texts = [
                        "düzenleyen kişi sizi görüşmeye alana kadar bekleyin",
                        "waiting for host to join",
                        "asking to join",
                        "katılma isteği gönderildi"
                    ]
                    
                    found_text = None
                    for text in waiting_texts:
                        if text in page_source:
                            found_text = text
                            break
                    
                    if found_text:
                        if not waiting_room_logged:
                            logger.info(f"⏳ Bekleme odası metni algılandı: '{found_text}'")
                            waiting_room_logged = True
                        
                        # STOP KOMUTU KONTROLÜ (Kritik)
                        # Eğer bu süreçte kullanıcı durdur derse çıkmalıyız.
                        if Path("data/bot_command.json").exists():
                            try:
                                cmd = json.loads(Path("data/bot_command.json").read_text("utf-8"))
                                if cmd.get("command") == "stop" and not cmd.get("processed"):
                                    logger.info("🛑 Bekleme sırasında STOP komutu algılandı.")
                                    return False
                            except: pass

                        # Her 30 saniyede bir log at
                        if int(elapsed) % 30 == 0:
                            logger.info(f"⏳ Bekleniyor... ({int(elapsed)}/{wait_timeout} sn)")
                            
                        await asyncio.sleep(1)
                        continue
                        
                except Exception as e:
                     pass
                
                # Diğer hata durumları (Toplantı bitti vs) kontrol edilebilir burada
                
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Join hatası: {e}")
            return False
                
        except Exception as e:
            logger.error(f"Join hatası: {e}")
            return False

    async def send_message(self, message):
        """Chat panelini açar ve mesaj gönderir - xdotool (sistem klavyesi)."""
        import re, subprocess, shutil
        from platform_utils import IS_LINUX

        if not message:
            return

        try:
            logger.info(f"Mesaj gönderiliyor: {message}")

            # Emoji'leri kaldır (xdotool ASCII dışını yanlış işleyebilir)
            clean_message = re.sub(
                r'[^\x00-\x7F\u00C0-\u024F\u011E\u011F\u0130\u0131\u015E\u015F\u00D6\u00F6\u00DC\u00FC\u00C7\u00E7]+',
                '', message
            ).strip()
            if not clean_message:
                clean_message = "Merhaba! Ben Sesly Bot. Bu toplantiyi kaydediyorum."

            # 1. Chat panelini aç
            chat_btn_clicked = False
            try:
                chat_btns = self.driver.find_elements(By.XPATH,
                    "//button[contains(@aria-label, 'chat') or contains(@aria-label, 'sohbet') or contains(@aria-label, 'Chat')]"
                )
                for btn in chat_btns:
                    if btn.is_displayed():
                        if btn.get_attribute("aria-pressed") != "true":
                            btn.click()
                            await asyncio.sleep(1.5)
                        chat_btn_clicked = True
                        break
            except Exception as e:
                logger.warning(f"Chat buton hatası: {e}")

            if not chat_btn_clicked:
                logger.warning("Chat butonu bulunamadı, input direkt aranıyor...")

            await asyncio.sleep(1)

            # 2. Mesaj alanını bul ve focus al
            input_selectors = [
                "textarea[placeholder*='Send']",
                "textarea[placeholder*='İlet']",
                "textarea[placeholder*='mesaj']",
                "textarea",
                "div[contenteditable='true'][data-placeholder]",
                "div[contenteditable='true']",
                "input[type='text']",
            ]

            message_input = None
            used_selector = None
            for selector in input_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for el in elements:
                        if el.is_displayed():
                            message_input = el
                            used_selector = selector
                            logger.info(f"Mesaj alanı bulundu: {selector}")
                            break
                    if message_input:
                        break
                except:
                    continue

            if not message_input:
                logger.error("❌ Mesaj alanı bulunamadı!")
                return

            # Focus
            message_input.click()
            await asyncio.sleep(0.5)

            # 3. Mesajı yaz — önce xdotool, sonra xclip, sonra send_keys fallback
            sent = False

            # STRATEJI 1: xdotool (Linux X11 sistem klavyesi — isTrusted:true)
            if IS_LINUX and shutil.which("xdotool"):
                try:
                    logger.info("xdotool ile mesaj yazılıyor (Meet)...")
                    # Önce mevcut içeriği temizle
                    message_input.send_keys(Keys.CONTROL, 'a')
                    await asyncio.sleep(0.2)
                    result = subprocess.run(
                        ["xdotool", "type", "--clearmodifiers", "--delay", "50", clean_message],
                        capture_output=True, text=True, timeout=30
                    )
                    logger.info(f"xdotool: rc={result.returncode}, err={result.stderr[:80]}")
                    await asyncio.sleep(0.5)
                    # Send_keys Enter ile gönder
                    message_input.send_keys(Keys.RETURN)
                    await asyncio.sleep(0.5)
                    sent = True
                    logger.info("✅ Mesaj gönderildi (xdotool + Enter).")
                except Exception as e:
                    logger.warning(f"xdotool hatası: {e}")

            # STRATEJI 2: xclip + Ctrl+V
            if not sent and IS_LINUX and shutil.which("xclip"):
                try:
                    logger.info("xclip ile clipboard yazılıyor (Meet)...")
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=clean_message.encode("utf-8"),
                        capture_output=True, timeout=10
                    )
                    await asyncio.sleep(0.3)
                    message_input.click()
                    await asyncio.sleep(0.2)
                    message_input.send_keys(Keys.CONTROL, 'a')
                    await asyncio.sleep(0.1)
                    message_input.send_keys(Keys.CONTROL, 'v')
                    await asyncio.sleep(0.5)
                    message_input.send_keys(Keys.RETURN)
                    sent = True
                    logger.info("✅ Mesaj gönderildi (xclip + Ctrl+V + Enter).")
                except Exception as e:
                    logger.warning(f"xclip hatası: {e}")

            # STRATEJI 3: send_keys fallback (textarea için yeterli olabilir)
            if not sent:
                try:
                    logger.info("send_keys fallback (Meet)...")
                    try:
                        message_input.clear()
                    except:
                        pass
                    message_input.send_keys(clean_message)
                    await asyncio.sleep(0.5)
                    message_input.send_keys(Keys.RETURN)
                    sent = True
                    logger.info("✅ Mesaj gönderildi (send_keys + Enter).")
                except Exception as e:
                    logger.error(f"send_keys hatası: {e}")

            if not sent:
                logger.error("❌ Tüm mesaj stratejileri başarısız!")

            # Chat panelini kapat
            await asyncio.sleep(1)
            try:
                for btn in self.driver.find_elements(By.XPATH,
                    "//button[contains(@aria-label, 'chat') or contains(@aria-label, 'sohbet')]"
                ):
                    if btn.is_displayed() and btn.get_attribute("aria-pressed") == "true":
                        btn.click()
                        logger.info("🔽 Chat paneli kapatıldı")
                        break
            except:
                pass

        except Exception as e:
            logger.error(f"Send message hatası: {e}")



    async def open_participants_panel(self):
        """Katılımcı panelini açar (Gelişmiş - Tüm konumlar: sağ üst, sağ alt, toolbar)."""
        logger.info("Katılımcı paneli aranıyor...")
        
        try:
            # ÖNCELİK 1: Sağ üstteki katılımcı sayısı butonu (Yeni Google Meet)
            # Bu buton genelde rakam içerir ve sağ üst köşede olur
            try:
                # TÜM butonları tara, sadece rakam içerenleri bul
                all_buttons = self.driver.find_elements(By.XPATH, "//button | //div[@role='button']")
                
                for btn in all_buttons:
                    try:
                        if not btn.is_displayed(): continue
                        
                        text = btn.text.strip()
                        # Sadece 1-3 haneli rakam (katılımcı sayısı)
                        if text.isdigit() and 1 <= len(text) <= 3:
                            # Konumu kontrol et - sağ tarafta mı?
                            location = btn.location
                            size = btn.size
                            window_width = self.driver.execute_script("return window.innerWidth;")
                            
                            # Sağ tarafta (%70'ten sonra) ve üstte (%30'dan önce)
                            if location['x'] > window_width * 0.6:
                                logger.info(f"✅ Sağ üst katılımcı butonu bulundu (sayı: {text}, konum: {location})")
                                btn.click()
                                await asyncio.sleep(1)
                                return True
                    except: continue
            except Exception as e:
                logger.debug(f"Sağ üst buton arama hatası: {e}")
            
            # ÖNCELİK 2: Genel Arama (XPATH - En Güçlü)
            # Hem button hem div[@role='button'] ara (Meet div kullanabiliyor)
            candidates = self.driver.find_elements(By.XPATH, "//button | //div[@role='button']")
            
            logger.info(f"Aday buton sayısı: {len(candidates)}")
            
            target_btn = None
            
            # Anahtar kelimeler (Türkçe/İngilizce) - Genişletilmiş
            keywords = ["participant", "katılımcı", "kişi", "people", "herkes", "show everyone", "all", "everyone"]
            
            for btn in candidates:
                try:
                    if not btn.is_displayed(): continue
                    
                    # Özellikleri al
                    aria_label = (btn.get_attribute("aria-label") or "").lower()
                    text = btn.text.lower()
                    tooltip = (btn.get_attribute("data-tooltip") or "").lower()
                    
                    # İkon metni kontrolü (Material Icons)
                    icon_text = ""
                    try:
                        icons = btn.find_elements(By.XPATH, ".//i | .//span[contains(@class,'icon') or contains(@class,'symbol')]")
                        for i in icons:
                            icon_text += i.text.lower()
                    except: pass
                    
                    # EŞLEŞME KONTROLÜ
                    is_match = False
                    
                    # 1. İsim/Label Eşleşmesi
                    if any(k in aria_label for k in keywords): is_match = True
                    if any(k in tooltip for k in keywords): is_match = True
                    
                    # 2. İkon Eşleşmesi (Material Icons: 'people', 'group')
                    if "people" in icon_text or "group" in icon_text or "supervised_user_circle" in icon_text:
                        is_match = True
                    
                    # 3. Sol Üst Köşe Kontrolü (Kullanıcı Raporu)
                    # Sol üstte "Toplantı ayrıntıları" veya "Kişiler" varsa önceliklendir
                    if is_match:
                        # Zaten basılı mı?
                        pressed = btn.get_attribute("aria-pressed")
                        if pressed == "true":
                            logger.info(f"ℹ️ Panel zaten açık: {aria_label}")
                            return True
                        
                        target_btn = btn
                        logger.info(f"✅ Buton bulundu: '{aria_label}' (Konum: {btn.location})")
                        break
                        
                except Exception as e:
                    continue
            
            if target_btn:
                try:
                    # Scroll to element (garanti olsun)
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", target_btn)
                    await asyncio.sleep(0.5)
                    target_btn.click()
                    await asyncio.sleep(1)
                    logger.info("✅ Katılımcı butonuna tıklandı.")
                    return True
                except Exception as e:
                    logger.error(f"Tıklama hatası: {e}")
                    
            logger.warning("⚠️ Katılımcı butonu bulunamadı!")
            return False
            
        except Exception as e:
            logger.error(f"Panel açma genel hatası: {e}")
            return False


    async def get_all_participants_from_panel(self):
        """
        Katılımcı panelinden TÜM katılımcı isimlerini çeker.
        Bu yöntem video tile'lardan daha güvenilir çünkü:
        - Tüm katılımcıları gösterir (ekranda görünmeyenler dahil)
        - Daha tutarlı DOM yapısı
        """
        try:
            # Önce paneli aç
            panel_opened = await self.open_participants_panel()
            if not panel_opened:
                logger.warning("Katılımcı paneli açılamadı")
                return []
            
            await asyncio.sleep(1)  # Panel yüklenmesini bekle
            
            # Panelden isimleri çek
            js_script = """
                const names = [];
                
                // Katılımcı paneli elementleri
                // Google Meet'te panel genelde sağ tarafta açılır
                const panelSelectors = [
                    // Katılımcı satırları
                    '[data-participant-id]',
                    '[data-requested-participant-id]',
                    'div[role="listitem"]',
                    'div[class*="participant"]',
                    // Panel içindeki isim elementleri
                    '[data-self-name]',
                    'span[class*="name"]'
                ];
                
                // Tüm selector'ları dene
                for (const sel of panelSelectors) {
                    const elements = document.querySelectorAll(sel);
                    elements.forEach(el => {
                        // İsmi çıkar
                        let name = '';
                        
                        // 1. data-self-name attribute
                        if (el.getAttribute('data-self-name')) {
                            name = el.getAttribute('data-self-name');
                        }
                        // 2. İç metin
                        if (!name) {
                            // İlk satır genelde isim
                            const text = el.innerText || el.textContent || '';
                            name = text.split('\\n')[0].trim();
                        }
                        // 3. aria-label
                        if (!name && el.getAttribute('aria-label')) {
                            name = el.getAttribute('aria-label').split(',')[0].trim();
                        }
                        
                        // Filtrele
                        if (!name || name.length > 50 || name.length < 2) return;
                        
                        const nameLower = name.toLowerCase();
                        
                        // Bot ve UI elementlerini atla
                        const excluded = [
                            'sesly', 'bot', 'meeting bot', 'toplantı botu',
                            'frame', 'pen_spark', 'localhost', 
                            'siz', 'you', 'sen', 'ben',
                            'katılımcı', 'participant', 'kişi', 'people',
                            'toplantı', 'meeting', 'google meet'
                        ];
                        if (excluded.some(ex => nameLower.includes(ex))) return;
                        
                        // Sayılar (zaman gibi) içerenleri atla
                        if (/\\d{2}:\\d{2}/.test(name)) return;
                        
                        // Tekrar kontrolü
                        if (!names.includes(name)) {
                            names.push(name);
                        }
                    });
                }
                
                return names;
            """
            
            participants = self.driver.execute_script(js_script)
            
            if participants and len(participants) > 0:
                logger.info(f"✅ Panel'den {len(participants)} katılımcı alındı: {participants}")
                self._cached_participants = participants
                return participants
            else:
                logger.warning("Panel'den katılımcı alınamadı")
            
            # Panel'i kapat (görüntü karışmasın)
            try:
                await self.close_participants_panel()
            except: pass
            
            return participants if participants else []
                
        except Exception as e:
            logger.error(f"Panel katılımcı listesi hatası: {e}")
            # Hata olsa bile paneli kapatmaya çalış
            try:
                await self.close_participants_panel()
            except: pass
            return []


    async def close_participants_panel(self):
        """Katılımcı panelini kapatır."""
        try:
            # Panel kapatma butonu (X butonu veya aynı butona tekrar tıklama)
            close_selectors = [
                # X butonu
                "//button[contains(@aria-label, 'Kapat') or contains(@aria-label, 'Close')]",
                # Panel içindeki X
                "//div[contains(@class, 'panel')]//button[contains(@aria-label, 'close')]",
            ]
            
            for selector in close_selectors:
                try:
                    btns = self.driver.find_elements(By.XPATH, selector)
                    for btn in btns:
                        if btn.is_displayed():
                            btn.click()
                            logger.info("🔽 Katılımcı paneli kapatıldı")
                            await asyncio.sleep(0.5)
                            return True
                except: continue
            
            # Alternatif: Aynı butona tekrar tıkla (toggle)
            await self.open_participants_panel()  # Bu toggle yapacak
            logger.info("🔽 Katılımcı paneli kapatıldı (toggle)")
            return True
            
        except Exception as e:
            logger.debug(f"Panel kapatma hatası: {e}")
            return False


    async def enable_captions(self):
        """Google Meet canlı altyazıyı açar."""
        try:
            # ÖNCE: Altyazı zaten açık mı kontrol et (DOM'da caption görünüyor mu?)
            try:
                caption_visible = self.driver.execute_script("""
                    // Altyazı metni görünüyor mu?
                    const captions = document.querySelectorAll('div[class*="caption"], div[class*="subtitle"]');
                    for (const c of captions) {
                        if (c.innerText && c.innerText.length > 5 && c.offsetParent !== null) {
                            return true;
                        }
                    }
                    return false;
                """)
                
                if caption_visible:
                    logger.info("ℹ️ Altyazı zaten açık (DOM kontrolü)")
                    # DİL SEÇİMİ DEVRE DIŞI - toggle sorunu yaratıyordu
                    # Manuel olarak Türkçe'ye çevrilmeli
                    return True
            except Exception as e:
                logger.debug(f"DOM caption kontrolü hatası: {e}")
            
            # Yöntem 1: Alt toolbar'daki CC butonu (çeşitli selector'lar)
            caption_selectors = [
                # aria-label ile
                "//button[contains(@aria-label, 'caption')]",
                "//button[contains(@aria-label, 'Caption')]",
                "//button[contains(@aria-label, 'altyazı')]",
                "//button[contains(@aria-label, 'Altyazı')]",
                "//button[contains(@aria-label, 'subtitle')]",
                "//button[contains(@aria-label, 'Subtitle')]",
                "//button[contains(@aria-label, 'CC')]",
                # data-tooltip ile
                "//button[contains(@data-tooltip, 'caption')]",
                "//button[contains(@data-tooltip, 'altyazı')]",
                # İkon içeren div/button
                "//button[.//i[contains(text(), 'closed_caption')]]",
                "//div[@role='button'][contains(@aria-label, 'caption')]",
            ]
            
            for selector in caption_selectors:
                try:
                    btns = self.driver.find_elements(By.XPATH, selector)
                    for btn in btns:
                        if btn.is_displayed():
                            # Zaten açık mı?
                            if btn.get_attribute("aria-pressed") != "true":
                                btn.click()
                                logger.info(f"✅ Canlı altyazı açıldı ({selector[:30]}...)")
                                await asyncio.sleep(1)
                            else:
                                logger.info("ℹ️ Canlı altyazı zaten açık")
                            return True
                except: continue
            
            # Yöntem 2: Keyboard shortcut (C tuşu)
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                # Önce body'ye focus
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.click()
                await asyncio.sleep(0.3)
                # C tuşuna bas
                actions = ActionChains(self.driver)
                actions.send_keys('c').perform()
                logger.info("✅ Canlı altyazı açıldı (C tuşu)")
                await asyncio.sleep(1)
                
                # Dil seçimini yap
                await self._set_caption_language_turkish()
                return True
            except Exception as e:
                logger.debug(f"C tuşu hatası: {e}")
            
            logger.warning("Canlı altyazı butonu bulunamadı")
            return False
            
        except Exception as e:
            logger.error(f"Altyazı açma hatası: {e}")
            return False


    async def _set_caption_language_turkish(self):
        """Altyazı dilini Türkçe'ye çevirir."""
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            
            # Altyazı container'ını bul
            caption_area = None
            try:
                caption_area = self.driver.find_element(By.CSS_SELECTOR, 
                    "div[class*='caption'], div[class*='subtitle']"
                )
            except:
                logger.debug("Altyazı alanı bulunamadı")
                return False
            
            if not caption_area:
                return False
            
            # 1. Mouse'u altyazı alanına götür
            actions = ActionChains(self.driver)
            actions.move_to_element(caption_area).perform()
            logger.info("🖱️ Mouse altyazı alanına götürüldü")
            await asyncio.sleep(1)
            
            # 2. Sol üstteki dil butonunu bul (globe + İngilizce yazısı)
            # Caption area'nın sol tarafında, üst kısmında olmalı
            dropdown_btn = None
            try:
                # Globe ikonu veya İngilizce yazısı olan butonu ara
                possible_btns = self.driver.find_elements(By.XPATH,
                    "//button[contains(., 'İngilizce') or contains(., 'English')]"
                )
                for btn in possible_btns:
                    if btn.is_displayed():
                        rect = btn.rect
                        # Sol üstte mi? (x < 300, y < 100)
                        if rect['x'] < 300 and rect['y'] < 100:
                            dropdown_btn = btn
                            break
            except:
                pass
            
            if dropdown_btn:
                dropdown_btn.click()
                logger.info("✅ Dil dropdown'u açıldı (buton)")
            else:
                # Alternatif: Caption area'nın sol üstüne tıkla
                x_offset = -caption_area.size['width'] // 2 + 80  # Sol taraf
                y_offset = -30  # Biraz yukarı
                actions = ActionChains(self.driver)
                actions.move_to_element_with_offset(caption_area, x_offset, y_offset).click().perform()
                logger.info("✅ Dil dropdown'u açıldı (koordinat)")
            
            await asyncio.sleep(1.5)
            
            # 3. Türkçe'yi bul ve tıkla
            try:
                # Listede Türkçe'yi ara
                turkish_option = self.driver.find_element(By.XPATH, 
                    "//*[text()='Türkçe' or contains(text(), 'Türkçe')]"
                )
                if turkish_option and turkish_option.is_displayed():
                    # Görünür değilse scroll yap
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", turkish_option)
                    await asyncio.sleep(0.3)
                    turkish_option.click()
                    logger.info("✅ Altyazı dili Türkçe olarak ayarlandı")
                    return True
            except Exception as e:
                logger.debug(f"Türkçe seçimi hatası: {e}")
            
            logger.warning("Türkçe dil seçeneği bulunamadı")
            return False
            
        except Exception as e:
            logger.debug(f"Dil ayarı hatası: {e}")
            return False


    def get_speaker_from_captions(self):
        """Canlı altyazıdan konuşmacı ismini okur."""
        try:
            # Google Meet altyazı DOM yapısı:
            # Alt kısımda isim + altyazı metni görünür
            js_script = """
                // Altyazı container'ları
                const captionSelectors = [
                    // Yeni Meet tasarımı
                    'div[class*="caption"]',
                    'div[class*="subtitle"]',
                    // Eski tasarım
                    'div[jsname][data-caption]',
                    // Genel arama
                    'div[style*="bottom"]'
                ];
                
                for (const sel of captionSelectors) {
                    const containers = document.querySelectorAll(sel);
                    for (const container of containers) {
                        const text = container.innerText || '';
                        
                        // "İsim\nMetin" formatı
                        const lines = text.split('\\n');
                        if (lines.length >= 2) {
                            const speakerName = lines[0].trim();
                            const captionText = lines.slice(1).join(' ').trim();
                            
                            // Geçerli bir isim mi?
                            if (speakerName.length >= 2 && speakerName.length <= 50) {
                                // Bot isimlerini filtrele
                                const lowerName = speakerName.toLowerCase();
                                if (!lowerName.includes('sesly') && !lowerName.includes('bot')) {
                                    return {
                                        speaker: speakerName,
                                        text: captionText,
                                        method: 'caption'
                                    };
                                }
                            }
                        }
                    }
                }
                
                return null;
            """
            
            result = self.driver.execute_script(js_script)
            if result:
                speaker_name = result['speaker']
                
                # DOĞRULAMA: Katılımcı listesiyle karşılaştır
                if hasattr(self, '_cached_participants') and self._cached_participants:
                    # İsim listede var mı? (büyük/küçük harf duyarsız)
                    for cached_name in self._cached_participants:
                        if cached_name.lower() == speaker_name.lower():
                            logger.info(f"🎤 Altyazı (doğrulandı): {cached_name}")
                            return cached_name  # Listedeki doğru ismi döndür
                        # Kısmi eşleşme (örn: "Yusuf" ile "Yusuf Batkitar")
                        if speaker_name.lower() in cached_name.lower() or cached_name.lower() in speaker_name.lower():
                            logger.info(f"🎤 Altyazı (kısmi eşleşme): {cached_name}")
                            return cached_name
                
                # Liste yoksa veya eşleşme yoksa direkt döndür
                logger.info(f"🎤 Altyazı: {speaker_name}")
                return speaker_name
            return None
            
        except Exception as e:
            logger.debug(f"Altyazı okuma hatası: {e}")
            return None

    async def get_participants(self):
        """
        Konuşan katılımcıları tespit eder.
        Öncelik 1: CANLI ALTYAZI (en güvenilir - Google'ın kendi tespiti)
        Öncelik 2: DOM Görsel Analiz (border/glow)
        """
        active_speakers = []
        all_participants = []
        
        # ÖNCELİK 1: CANLI ALTYAZI - En güvenilir yöntem
        # Google Meet altyazıda konuşmacı ismini gösteriyor
        try:
            caption_speaker = self.get_speaker_from_captions()
            if caption_speaker:
                logger.info(f"🎤 Altyazı ile tespit: {caption_speaker}")
                return [caption_speaker]
        except Exception as e:
            logger.debug(f"Altyazı tespiti hatası: {e}")
        
        # ÖNCELİK 2: DOM Görsel Analiz (TURUNCU HALKA + MAVİ BORDER)
        # Google Meet konuşan kişinin video tile'ına turuncu/mavi border koyar
        try:
            js_script = """
                const activeSpeakers = [];
                const allParticipants = [];
                
                // TÜM video tile'ları ve katılımcı container'ları
                const containers = document.querySelectorAll(
                    '[data-participant-id], ' +
                    'div[data-self-name], ' +
                    'div[jsname][data-requested-participant-id], ' +
                    'div[class*="participant"], ' +
                    'div[class*="video-tile"], ' +
                    'div[class*="avatar"]'
                );
                
                containers.forEach(container => {
                    // İsmi çıkar
                    let name = '';
                    
                    // 1. İsim alt div'den
                    const nameEl = container.querySelector('[data-self-name], [class*="name"], span');
                    if (nameEl) {
                        name = nameEl.innerText || nameEl.textContent || '';
                    }
                    
                    // 2. İlk satır (İsim genelde ilk satırda)
                    if (!name) {
                        name = container.innerText.split('\\n')[0];
                    }
                    
                    // 3. aria-label
                    if (!name && container.getAttribute("aria-label")) {
                        name = container.getAttribute("aria-label").split(',')[0];
                    }
                    
                    // İsmi temizle: Newline'ları kaldır, sadece ilk satırı al
                    name = name.split('\\n')[0].trim();
                    
                    // Filtrele
                    if (!name || name.length > 50) return;
                    if (name.match(/\\d{2}:\\d{2}/) || name.includes('Merhaba') || name.includes('keep')) return;
                    if (name.toLowerCase().includes('sesly')) return; // Bot'u atla
                    
                    // EXCLUDED İSİMLER: Gerçek katılımcı olmayan UI elementleri ve bot isimleri
                    const excludedNames = [
                        'frame', 'pen_spark', 'pen_spark_io', 'spark_io',
                        'sesly bot', 'sesly', 'toplantı botu', 'meeting bot',
                        'localhost', 'panel', 'bot panel', 'sesly asistan',
                        'google meet', 'meet', 'katılım isteği', 'join request'
                    ];
                    const nameLowerCheck = name.toLowerCase();
                    if (excludedNames.some(ex => nameLowerCheck === ex || nameLowerCheck.includes(ex))) return;
                    
                    // Google Meet UI metinlerini filtrele
                    const uiTexts = [
                        'yeniden kadraja al', 'reframe', 'sabitle', 'pin', 
                        'sessize al', 'mute', 'sesi aç', 'unmute',
                        'kaldır', 'remove', 'engelle', 'block',
                        'tam ekran', 'fullscreen', 'küçült', 'minimize',
                        'ayarlar', 'settings', 'daha fazla', 'more',
                        'detaylar', 'details', 'kapat', 'close',
                        'gizle', 'hide', 'göster', 'show',
                        'spotlight', 'grid', 'sidebar', 'tiles'
                    ];
                    const nameLower = name.toLowerCase();
                    if (uiTexts.some(ui => nameLower.includes(ui))) return;
                    
                    allParticipants.push(name);
                    
                    let isSpeaking = false;
                    let speakingMethod = '';
                    
                    // ========================================
                    // KONUŞMACI TESPİT YÖNTEMLERİ
                    // ========================================
                    
                    // Google Meet konuşan kişinin video tile'ına RENKLİ border koyar
                    // Renk değişebilir (turuncu, mavi, yeşil, mor vs.)
                    // Bu yüzden siyah/beyaz/gri HARİCİ her rengi kabul ediyoruz
                    
                    function isSpeakingBorder(colorStr) {
                        if (!colorStr) return false;
                        
                        const match = colorStr.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
                        if (!match) return false;
                        
                        const r = parseInt(match[1]);
                        const g = parseInt(match[2]);
                        const b = parseInt(match[3]);
                        
                        // Siyah: hepsi düşük
                        if (r < 30 && g < 30 && b < 30) return false;
                        
                        // Beyaz: hepsi yüksek
                        if (r > 225 && g > 225 && b > 225) return false;
                        
                        // Gri: r ≈ g ≈ b (fark < 30)
                        const maxDiff = Math.max(Math.abs(r-g), Math.abs(g-b), Math.abs(r-b));
                        if (maxDiff < 30) return false;
                        
                        // Renkli border! (turuncu, mavi, yeşil, mor, pembe vs.)
                        return true;
                    }
                    
                    // Box-shadow'da glow efekti var mı? (spread veya blur > 0)
                    function hasGlowEffect(shadowStr) {
                        if (!shadowStr || shadowStr === 'none') return false;
                        // Box-shadow format: [offset-x] [offset-y] [blur] [spread] [color]
                        // Blur veya spread varsa = glow efekti
                        const hasBlur = /\\dpx\\s+\\d+px\\s+\\d+px/.test(shadowStr);
                        const hasColor = isSpeakingBorder(shadowStr);
                        return hasBlur && hasColor;
                    }
                    
                    // Container'ın kendisini kontrol et
                    const containerStyle = window.getComputedStyle(container);
                    const containerBorderWidth = parseInt(containerStyle.borderWidth) || 0;
                    const containerBorderColor = containerStyle.borderColor || '';
                    const containerOutline = containerStyle.outline || '';
                    const containerBoxShadow = containerStyle.boxShadow || '';
                    
                    // 3+ pixel KALIN renkli border = konuşuyor (normal border 1-2px)
                    if (containerBorderWidth >= 3 && isSpeakingBorder(containerBorderColor)) {
                        isSpeaking = true;
                        speakingMethod = 'thick-border';
                    }
                    
                    // Outline kontrolü (genelde 2px+)
                    if (!isSpeaking && containerOutline && containerOutline !== 'none') {
                        // Outline width kontrolü
                        const outlineMatch = containerOutline.match(/(\\d+)px/);
                        const outlineWidth = outlineMatch ? parseInt(outlineMatch[1]) : 0;
                        if (outlineWidth >= 2 && isSpeakingBorder(containerOutline)) {
                            isSpeaking = true;
                            speakingMethod = 'outline';
                        }
                    }
                    
                    // Box-shadow kontrolü (GLOW efekti)
                    if (!isSpeaking && hasGlowEffect(containerBoxShadow)) {
                        isSpeaking = true;
                        speakingMethod = 'glow-effect';
                    }
                    
                    // Child elementleri de tara
                    if (!isSpeaking) {
                        const allElements = container.querySelectorAll('*');
                        for (const el of allElements) {
                            if (isSpeaking) break;
                            
                            const style = window.getComputedStyle(el);
                            const bw = parseInt(style.borderWidth) || 0;
                            const bc = style.borderColor || '';
                            const shadow = style.boxShadow || '';
                            
                            // Kalın border (3px+)
                            if (bw >= 3 && isSpeakingBorder(bc)) {
                                isSpeaking = true;
                                speakingMethod = 'child-thick-border';
                            }
                            // Glow efekti
                            if (!isSpeaking && hasGlowEffect(shadow)) {
                                isSpeaking = true;
                                speakingMethod = 'child-glow';
                            }
                        }
                    }
                    
                    // 3. SES DALGASI / EQUALİZER ANİMASYONU
                    // Mikrofon yanındaki dalga animasyonları
                    if (!isSpeaking) {
                        // Animasyonlu elementleri ara
                        const waveSelectors = [
                            // SVG ses dalgaları
                            'svg[class*="audio"]',
                            'svg[class*="wave"]',
                            'svg[class*="voice"]',
                            'svg[class*="sound"]',
                            // Animasyonlu divler (equalizer bars)
                            'div[style*="transform"]',
                            'div[style*="animation"]',
                            'div[class*="audio"]',
                            'div[class*="wave"]',
                            'div[class*="indicator"]',
                            // Canvas (ses görselleştirme)
                            'canvas',
                            // Genel animasyonlu elementler
                            '[class*="speaking"]',
                            '[class*="active-speaker"]',
                            '[data-is-speaking]'
                        ];
                        
                        for (const sel of waveSelectors) {
                            const waves = container.querySelectorAll(sel);
                            for (const wave of waves) {
                                // Görünür mü?
                                const style = window.getComputedStyle(wave);
                                if (style.display !== 'none' && style.visibility !== 'hidden') {
                                    // Animasyon var mı?
                                    const hasAnim = style.animation !== 'none' && style.animation !== '';
                                    const hasTransform = style.transform !== 'none' && style.transform !== '';
                                    
                                    if (hasAnim || hasTransform || wave.tagName === 'CANVAS') {
                                        isSpeaking = true;
                                        speakingMethod = 'wave-animation';
                                        break;
                                    }
                                }
                            }
                            if (isSpeaking) break;
                        }
                    }
                    
                    // 4. Aria-label kontrolü
                    if (!isSpeaking) {
                        const label = (container.getAttribute("aria-label") || "").toLowerCase();
                        if (label.includes("konuşuyor") || label.includes("speaking") || label.includes("presenting")) {
                            isSpeaking = true;
                            speakingMethod = 'aria-label';
                        }
                    }
                    
                    // 5. Class-based detection
                    // 5. Class-based detection (sadece spesifik class'lar)
                    if (!isSpeaking) {
                        const classes = container.className.toLowerCase();
                        // 'active' KALDIRILDI - çok genel, yanlış pozitif üretiyor
                        if (classes.includes('speaking') || classes.includes('talking')) {
                            isSpeaking = true;
                            speakingMethod = 'class';
                        }
                    }
                    
                    if (isSpeaking && !activeSpeakers.includes(name)) {
                        activeSpeakers.push(name);
                        console.log('[MEET-SPEAKER] ' + name + ' konuşuyor (' + speakingMethod + ')');
                    }
                });
                
                return {speakers: [...new Set(activeSpeakers)], all: [...new Set(allParticipants)]};
            """
            
            result = self.driver.execute_script(js_script)
            if result:
                all_participants = result.get('all', [])
                active_speakers = result.get('speakers', [])
                
                if active_speakers:
                    logger.info(f"✅ DOM: Konuşanlar: {active_speakers}")
                    
                # Cache all participants
                self._cached_participants = all_participants
                
                # DOM başarılı olduysa döndür
                if active_speakers:
                    return active_speakers
                    
        except Exception as e:
            logger.debug(f"DOM speaker detection error: {e}")
        
        # WebRTC yedek yöntemi KALDIRILDI
        # Neden: WebRTC ses seviyesi tespit ediyor ama KİMİN konuştuğunu bilemiyordu
        # Şimdi: DOM başarısız olursa Gemini ses analizi yapacak
            
        return active_speakers


    async def check_meeting_ended(self):
        """Toplantı bitti mi veya geçersiz mi kontrol eder."""
        try:
            # "You left the meeting" gibi mesajlar
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            
            # TOPLANTI BİTTİ MESAJLARI
            end_phrases = ["you left", "meeting has ended", "toplantıdan ayrıldınız", "toplantı sona erdi"]
            if any(phrase in body_text for phrase in end_phrases):
                logger.info("Toplantı bitiş mesajı tespit edildi")
                self.end_reason = "normal"
                return True
            
            # GEÇERSİZ/ESKİ LİNK MESAJLARI (YENİ!)
            invalid_phrases = [
                "invalid video call link",
                "check your meeting code",
                "this video call link is invalid",
                "meeting doesn't exist",
                "couldn't find the meeting",
                "video call has ended",
                "this call has ended",
                "not allowed to join",
                "geçersiz görüntülü arama bağlantısı",
                "toplantı kodu hatalı",
                "bu toplantı artık mevcut değil",
                "toplantı sona ermiş",
                "bu aramaya katılamazsınız",
                "geçersiz toplantı linki",
                "bu görüşme sona erdi",
            ]
            for phrase in invalid_phrases:
                if phrase in body_text:
                    logger.warning(f"⚠️ GEÇERSİZ MEET TOPLANTISI TESPİT EDİLDİ: {phrase}")
                    self.end_reason = f"Geçersiz Meet toplantısı: {phrase}"
                    return True
            
            # Participant sayısı kontrolü
            try:
                buttons = self.driver.find_elements(By.TAG_NAME, "button")
                for btn in buttons:
                    aria_label = (btn.get_attribute("aria-label") or "").lower()
                    if "participant" in aria_label:
                        text = btn.text
                        import re
                        match = re.search(r'(\d+)', text)
                        if match:
                            count = int(match.group(1))
                            if count <= 1:
                                if not self.waiting_start_time:
                                    self.waiting_start_time = time.time()
                                    logger.info("⚠️ Tek katılımcı algılandı, 5dk bekleme başlatıldı")
                                elif time.time() - self.waiting_start_time > 300:
                                    logger.info("⏰ 5 dakika tek katılımcı, toplantı bitiyor")
                                    return True
                            else:
                                if self.waiting_start_time:
                                    logger.info("✅ Yeni katılımcı geldi, sayaç sıfırlandı")
                                    self.waiting_start_time = None
            except:
                pass
                
        except Exception as e:
            logger.debug(f"Check meeting ended error: {e}")
        
        return False

    async def close(self):
        """Tarayıcıyı kapatır (Aggressive Cleanup)."""
        pid = None
        try:
            if self.driver:
                # PID'yi al
                try:
                    if hasattr(self.driver, 'service') and self.driver.service.process:
                        pid = self.driver.service.process.pid
                    elif hasattr(self.driver, 'browser_pid'): # uc specific
                         pid = self.driver.browser_pid
                except: pass
                
                # Normal kapatma denemesi
                logger.info("Chrome quit() çağrılıyor...")
                try:
                    self.driver.quit()
                except Exception as e:
                    logger.warning(f"Chrome normal kapanmadı: {e}")
                
                logger.info("Chrome kapatıldı (veya denendi).")
        except Exception as e:
            logger.warning(f"Close hatası: {e}")
        
        # Kesin temizlik (Zombi process kalmasın)
        if pid:
            try:
                import psutil
                if psutil.pid_exists(pid):
                    logger.warning(f"Chrome process ({pid}) hala aktif, zorla kapatılıyor...")
                    p = psutil.Process(pid)
                    p.kill()
                    logger.info(f"Process {pid} kill edildi.")
            except ImportError:
                # psutil yoksa os.kill dene
                try:
                    os.kill(pid, signal.SIGTERM) # Windows'ta bu terminate eder
                    logger.info(f"Process {pid} os.kill ile sonlandırıldı.")
                except: pass
            except Exception as e:
                logger.warning(f"Process kill hatası: {e}")
