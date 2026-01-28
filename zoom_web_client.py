
import asyncio
import traceback
import logging
import re
from playwright.async_api import async_playwright

try:
    import win32gui
    import win32con
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# Logger Setup
logger = logging.getLogger("ZoomWebClient")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[ZOOM-WEB] %(message)s'))
logger.addHandler(handler)

class ZoomWebBot:
    def __init__(self, meeting_url, bot_name="Sesly Bot", password=None):
        self.meeting_url = self._convert_to_web_url(meeting_url)
        self.bot_name = bot_name
        self.password = password
        
        logger.info(f"Orijinal URL Web Client formatına çevrildi: {self.meeting_url}")

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.is_running = False
        self._last_panel_check = 0  # Katılımcı paneli kontrolü için
        self.end_reason = None  # Toplantı sona erme sebebi (normal/invalid link)
        
        # Selectors (Zoom Web UI changes frequently, these are common patterns)
        self.selectors = {
            "launch_meeting_btn": "div[role='button']:has-text('Launch Meeting'), div[role='button']:has-text('Zoom Meetings adlı uygulamayı başlat'), div[role='button']:has-text('Toplantıyı Başlat')",
            "join_browser_link": "a:has-text('Join from Your Browser'), a:has-text('Tarayıcınızdan Katılın'), a:has-text('tarayıcıdan katıl')",
            "input_name": "input[id='inputname'], input[name='inputname'], input[id='input-name'], input[type='text']",
            "input_passcode": "input[id='inputpasscode'], input[name='inputpasscode'], input[id='input-passcode'], input[type='password']",
            "join_btn": "button:has-text('Join'):visible, button:has-text('Katıl'):visible, button[class*='preview-join-button']",
            "agree_terms_btn": "button:has-text('I Agree'), button:has-text('Kabul Ediyorum')",
            "join_audio_btn": "button:has-text('Join Audio by Computer'), button:has-text('Bilgisayarın Sesiyle Katıl')",
            "participants_btn": "button[aria-label*='Participants'], button[aria-label*='Katılımcılar']",
            "participants_list": "div[class*='participants-list']",
        }

    def _convert_to_web_url(self, url):
        """
        Zoom 'Launcher' URL'sini (j/...) direkt Web Client (wc/.../join) formatina çevirir.
        Böylece 'Zoom Açilsin mi?' popup'i ve 'Launch Meeting' butonlariyla uğraşmayiz.
        
        Input: https://us05web.zoom.us/j/123456789?pwd=abc
        Output: https://us05web.zoom.us/wc/123456789/join?pwd=abc
        """
        try:
            pattern = r"/j/(\d+)"
            match = re.search(pattern, url)
            
            if match:
                meeting_id = match.group(1)
                base_part = url.split("?")[0]
                query_part = ""
                if "?" in url:
                    query_part = "?" + url.split("?")[1]
                
                # Domain korunsun (us05web, zoom.us vs)
                domain_part = base_part.split("/j/")[0]
                
                new_url = f"{domain_part}/wc/{meeting_id}/join{query_part}"
                return new_url
            
            return url
        except Exception as e:
            logger.error(f"URL dönüşüm hatası: {e}")
            return url

    def browser_process_pid(self):
        """Playwright browser process ID'sini bulmaya çalışır."""
        try:
            return None  # Async API'de karmaşık, title-based fallback kullan
        except:
            return None

    def _bring_to_front_force(self, pid=None, target_title=None):
        """Windows API kullanarak pencereyi zorla öne getirir (BULLETPROOF)."""
        try:
            import ctypes
            from ctypes import wintypes
            import win32gui
            import win32con
            import win32process
            import time as _time
            
            try:
                import psutil
                HAS_PSUTIL = True
            except ImportError:
                HAS_PSUTIL = False

            # Windows API tanımlamaları
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            
            ASFW_ANY = -1
            user32.AllowSetForegroundWindow(ASFW_ANY)

            def find_browser_window():
                """Tarayıcı penceresini bulur."""
                keywords = target_title if target_title else ["Zoom", "Meeting", "zoom.us", "Sesly", "Chrome", "wc/"]
                if isinstance(keywords, str):
                    keywords = [keywords]
                
                BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "chromium.exe", "opera.exe", "brave.exe"}
                candidates = []
                
                def enum_callback(hwnd, _):
                    if not win32gui.IsWindowVisible(hwnd):
                        return True
                    
                    title = win32gui.GetWindowText(hwnd)
                    if not title:
                        return True
                    
                    title_lower = title.lower()
                    if not any(kw.lower() in title_lower for kw in keywords):
                        return True
                    
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
                
                for hwnd, title, _ in candidates:
                    if "zoom" in title.lower():
                        return hwnd
                
                return candidates[0][0]

            def force_foreground(hwnd):
                """Foreground lock bypass."""
                if not hwnd or not win32gui.IsWindow(hwnd):
                    return False
                
                try:
                    # Thread bilgileri
                    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
                    GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                    GetWindowThreadProcessId.restype = wintypes.DWORD
                    
                    foreground_hwnd = user32.GetForegroundWindow()
                    foreground_thread = 0
                    if foreground_hwnd:
                        _pid = wintypes.DWORD()
                        foreground_thread = GetWindowThreadProcessId(foreground_hwnd, ctypes.byref(_pid))
                    
                    target_pid = wintypes.DWORD()
                    target_thread = GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
                    current_thread = kernel32.GetCurrentThreadId()
                    
                    # Thread bağla
                    attached_fg = False
                    attached_tgt = False
                    
                    if foreground_thread and foreground_thread != current_thread:
                        attached_fg = user32.AttachThreadInput(current_thread, foreground_thread, True)
                    if target_thread and target_thread != current_thread:
                        attached_tgt = user32.AttachThreadInput(current_thread, target_thread, True)
                    
                    try:
                        # Alt key trick
                        user32.keybd_event(0x12, 0, 0, 0)
                        _time.sleep(0.01)
                        user32.keybd_event(0x12, 0, 2, 0)
                        _time.sleep(0.01)
                        
                        # Çoklu yöntem
                        user32.SwitchToThisWindow(hwnd, True)
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                        user32.SetActiveWindow(hwnd)
                        user32.SetFocus(hwnd)
                        
                    finally:
                        if attached_fg:
                            user32.AttachThreadInput(current_thread, foreground_thread, False)
                        if attached_tgt:
                            user32.AttachThreadInput(current_thread, target_thread, False)
                    
                    _time.sleep(0.05)
                    return user32.GetForegroundWindow() == hwnd
                    
                except Exception as e:
                    logger.error(f"force_foreground error: {e}")
                    return False

            def ensure_maximized(hwnd):
                """Maximize garantisi."""
                if not hwnd:
                    return False
                try:
                    placement = win32gui.GetWindowPlacement(hwnd)
                    if placement[1] != win32con.SW_SHOWMAXIMIZED:
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                        _time.sleep(0.1)
                        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                        _time.sleep(0.05)
                    return True
                except:
                    return False

            # Ana akış
            for attempt in range(5):
                hwnd = find_browser_window()
                
                if not hwnd:
                    logger.info(f"[FOCUS] Pencere bulunamadı (deneme {attempt+1}/5)")
                    _time.sleep(0.5)
                    continue
                
                title = win32gui.GetWindowText(hwnd)
                logger.info(f"[FOCUS] HEDEF: '{title}' (HWND: {hwnd})")
                
                # Önce maximize
                ensure_maximized(hwnd)
                
                # Sonra foreground
                if force_foreground(hwnd):
                    logger.info(f"[FOCUS] ✅ Pencere öne getirildi (deneme {attempt+1})")
                    return
                
                logger.info(f"[FOCUS] ⚠ Deneme {attempt+1} başarısız")
                _time.sleep(0.5)
            
            logger.warning("[FOCUS] ❌ Tüm denemeler başarısız")
            
        except Exception as e:
            logger.warning(f"Windows API focus hatası: {e}")

    async def start(self):
        """Playwright ve tarayıcıyı başlatır."""
        logger.info("Playwright başlatılıyor...")
        self.playwright = await async_playwright().start()
        
        # TAM EKRAN MODDA BAŞLAT
        import screeninfo
        try:
            # Birincil monitörün çözünürlüğünü al
            screen = screeninfo.get_monitors()[0]
            screen_width = screen.width
            screen_height = screen.height
            logger.info(f"Ekran çözünürlüğü: {screen_width}x{screen_height}")
        except:
            # Fallback
            screen_width = 1920
            screen_height = 1080
            logger.warning("Ekran çözünürlüğü alınamadı, varsayılan kullanılıyor")
        
        # Viewport için tarayıcı chrome'u (adres çubuğu vs.) hesaba kat
        # Alttaki toolbar görünsün diye yüksekliği düşür
        viewport_height = screen_height - 150  # Chrome UI + toolbar için boşluk
        
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=[
                "--use-fake-ui-for-media-stream",
                "--disable-notifications",
                "--start-maximized",  # Maximize başlat
                "--disable-infobars",  # "Chrome otomasyon kontrolünde" yazısını gizle
                "--disable-extensions",
                f"--window-size={screen_width},{screen_height}",
                "--force-device-scale-factor=1",  # Scale düzgün olsun
            ]
        )
        
        self.context = await self.browser.new_context(
            viewport={"width": screen_width, "height": viewport_height},
            permissions=["microphone", "camera"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            no_viewport=False  # Viewport'u aktif tut
        )
        
        self.page = await self.context.new_page()
        
        # 1. Otomatik İndirmeleri Engelle (Zoom Installer)
        self.page.on("download", lambda download: download.cancel())
        
        # 2. Gereksiz dosyaları engelle
        await self.page.route("**/*.{exe,msi,dmg,zip}", lambda route: route.abort())

        self.is_running = True

        # 3. Pencereyi ÖNE GETİR VE TAM EKRAN YAP
        try:
            await self.page.bring_to_front()
            
            # Kısa bekle, pencere oluşsun
            await asyncio.sleep(0.5)
            
            # OS-level pencere yönetimi
            self._bring_to_front_force()
            
            logger.info("✅ Pencere tam ekran yapıldı ve öne getirildi")
                
        except Exception as e:
            logger.warning(f"Pencere öne getirme hatası: {e}")
            pass
            
        logger.info("Tarayıcı hazır ve öne getirildi.")

    async def join_meeting(self):
        """Toplantıya katılma süreci."""
        if not self.page:
            return False
            
        try:
            # 1. URL'ye git
            logger.info(f"Toplantıya gidiliyor: {self.meeting_url}")
            await self.page.goto(self.meeting_url, timeout=60000)
            
            # 2. Sayfayı EN ÜSTE kaydır (Input alanını görmek için)
            await asyncio.sleep(1)
            try:
                await self.page.evaluate("window.scrollTo(0, 0)")
            except:
                pass
            
            # 3. Popup KAPATMA (Pencere öndeyken Escape bas)
            try:
                await asyncio.sleep(1) # Odaklanma sonrası kısa bekleme
                await self.page.keyboard.press("Escape")
            except:
                pass
            
            # OPTİMİZASYON: Eğer zaten /wc/ linki ile girdiysek direkt İSİM GİRME ekranındayızdır.
            # Boşuna "Launch Meeting" veya "Join from Browser" aramayalım.
            
            is_input_visible = False
            try:
                # 3 saniye içinde isim kutusu gelirse direkt oraya atla
                await self.page.wait_for_selector(self.selectors["input_name"], timeout=3000)
                is_input_visible = True
                logger.info("Doğrudan Web Client giriş ekranı tespit edildi.")
            except:
                pass

            if not is_input_visible:
                # 1. "Launch Meeting" sayfası ve "Join from Browser" hilesi
                # Sadece isim kutusu yoksa bu akışı işlet
                logger.info("Sayfa yüklendi. 'Launch Meeting' veya 'Join from Browser' aranıyor...")
                
                # Eğer direkt çıkarsa tıkla
                try:
                    await self.page.click(self.selectors["join_browser_link"], timeout=3000)
                    logger.info("Direkt 'Join from Browser' linkine tıklandı.")
                except:
                    # Çıkmadıysa Launch'a tıkla
                    logger.info("'Join from Browser' bulunamadı, 'Launch Meeting' deneniyor...")
                    launch_btns = await self.page.query_selector_all(self.selectors["launch_meeting_btn"])
                    if launch_btns:
                        await launch_btns[0].click()
                        await asyncio.sleep(2)
                        
                        # Şimdi tekrar ara
                        try:
                             await self.page.click(self.selectors["join_browser_link"], timeout=5000)
                             logger.info("İkinci denemede 'Join from Browser' tıklandı.")
                        except:
                            logger.error("'Join from Browser' linki çıkmadı!")
                            logger.error("'Join from Browser' linki çıkmadı!")
                            return False
            
            # 1.5 ŞİFRE EKRANI KONTROLÜ (Web Client bazen önce şifre sorar)
            if self.password:
                try:
                    # Hızlıca şifre kutusu var mı kontrol et (2sn)
                    pass_input = await self.page.wait_for_selector(self.selectors["input_passcode"], timeout=3000, state="visible")
                    if pass_input:
                        logger.info("🔑 Şifre ekranı tespit edildi, şifre giriliyor...")
                        await pass_input.fill(self.password)
                        await asyncio.sleep(0.5)
                        
                        # Şifre sonrası Join butonu olabilir, ona bas
                        try:
                            join_pass_btn = await self.page.wait_for_selector(self.selectors["join_btn"], timeout=2000)
                            if join_pass_btn:
                                await join_pass_btn.click()
                                logger.info("🔑 Şifre sonrası 'Join' butonuna basıldı.")
                        except: pass
                        
                        await asyncio.sleep(2) # Geçiş bekle
                except:
                    pass

            # 2. İsim Girme Ekranı
            logger.info("İsim girme ekranı bekleniyor...")
            await self.page.wait_for_selector(self.selectors["input_name"], timeout=30000)
            await self.page.fill(self.selectors["input_name"], self.bot_name)
            await asyncio.sleep(1)
            
            # SES AYARLARI - DEBUG + MUTE EKLENDİ
            logger.info("Ses ayarları yapılıyor...")
            
            try:
                await asyncio.sleep(1) # Başlangıç beklemesi (UI yüklenmesi için)
                
                # SCREENSHOT 1: Başlangıç (DISABLED)
                # try:
                #     await self.page.screenshot(path="debug_audio_01_start.png")
                # except: pass
                
                # 1. DROPDOWN AÇ - DAHA FAZLA SELECTOR
                logger.info("Audio dropdown açılıyor...")
                dropdown_opened = False
                
                audio_dropdown_selectors = [
                    "button[class*='arrowDown']",
                    "button[class*='arrow-down']", 
                    "button[aria-label*='Select a microphone']",
                    "button[aria-label*='Select a speaker']",
                    "button[aria-label*='audio settings']",
                    "xpath=//button[contains(@class, 'audio')]//following-sibling::button",
                    "xpath=//button[contains(@aria-label, 'audio')]",
                ]
                
                for i, selector in enumerate(audio_dropdown_selectors):
                    try:
                        # logger.info(f"  Dropdown selector {i+1}: {selector[:50]}")
                        dropdown = await self.page.wait_for_selector(selector, timeout=2000, state="visible")
                        if dropdown:
                            await dropdown.click()
                            logger.info("✓ Dropdown açıldı!")
                            await asyncio.sleep(1) # HIZLANDIRILDI: 2sn -> 1sn
                            dropdown_opened = True
                            
                            # SCREENSHOT 2: Dropdown açık (DISABLED)
                            # try:
                            #     await self.page.screenshot(path="debug_audio_02_dropdown_open.png")
                            # except: pass
                            break
                    except:
                        continue
                
                if not dropdown_opened:
                    logger.warning("⚠ Dropdown bulunamadı!")
                    # try:
                    #     await self.page.screenshot(path="debug_audio_FAIL_no_dropdown.png")
                    # except: pass
                
                # 2. CABLE INPUT SEÇ
                if dropdown_opened:
                    logger.info("CABLE Input seçiliyor...")
                    cable_selected = False
                    
                    cable_selectors = [
                        "string=CABLE Input (VB-Audio Virtual Cable)",
                        "string=CABLE Input",
                        "li:has-text('CABLE Input')",
                        "div:has-text('CABLE Input')",
                        "span:has-text('CABLE Input')"
                    ]
                    
                    for sel in cable_selectors:
                        try:
                            # Text tam eşleşme veya içerik
                            if "string=" in sel:
                                txt = sel.replace("string=", "")
                                item = self.page.get_by_text(txt, exact=True)
                                if await item.count() > 0:
                                    await item.first.click()
                                    cable_selected = True
                                    logger.info(f"✓ CABLE Input seçildi (get_by_text): {txt}")
                                    await asyncio.sleep(1) # HIZLANDIRILDI: 2sn -> 1sn
                                    break
                            else:
                                item = await self.page.wait_for_selector(sel, timeout=1000, state="visible")
                                if item:
                                    await item.click()
                                    cable_selected = True
                                    logger.info(f"✓ CABLE Input seçildi: {sel}")
                                    await asyncio.sleep(1) # HIZLANDIRILDI: 2sn -> 1sn
                                    break
                        except:
                            continue
                            
                    if not cable_selected:
                         logger.warning("⚠ CABLE Input listede bulunamadı!")
                         # try:
                         #     await self.page.screenshot(path="debug_audio_FAIL_no_cable.png")
                         # except: pass
                
                # 3. MUTE MİKROFON (Eğer açık ise)
                logger.info("Mikrofon kontrol ediliyor...")
                try:
                    # Mute düğmesini bul
                    mute_btn = None
                    try:
                        mute_btn = await self.page.wait_for_selector("button[aria-label*='Mute']", timeout=2000)
                    except:
                        # Belki zaten mute'dur, 'Unmute' yazar
                        pass
                        
                    if mute_btn:
                        # Butona bas
                        await mute_btn.click()
                        logger.info("✓ Mute butonuna basıldı")
                        await asyncio.sleep(0.5)
                    else:
                        logger.info("ℹ Mikrofon zaten mute olabilir veya buton bulunamadı.")
                        
                except Exception as e:
                    logger.warning(f"Mute işlemi hatası: {e}")

                # 4. VİDEOYU KAPAT (Eğer açık ise)
                logger.info("Video kontrol ediliyor...")
                try:
                    video_off = False
                    
                    # Video kapatma düğmesi selectorleri
                    video_off_selectors = [
                        "button[aria-label*='Stop Video']",
                        "button[aria-label*='Turn off camera']",
                        "button[aria-label*='Kamerayı kapat']",
                        "button[aria-label*='Video Durdur']",
                        "button[aria-label*='Videoyu Durdur']",
                        "button[class*='video'][class*='off']",
                        "button[class*='video'][class*='stop']",
                    ]
                    
                    for selector in video_off_selectors:
                        try:
                            video_btn = await self.page.wait_for_selector(selector, timeout=1500, state="visible")
                            if video_btn:
                                await video_btn.click()
                                logger.info(f"✓ Video kapatıldı ({selector})")
                                video_off = True
                                await asyncio.sleep(0.5)
                                break
                        except:
                            continue
                    
                    # Alternatif: Aria-label içinde 'video' ve 'on' geçen buton ara
                    if not video_off:
                        try:
                            all_btns = await self.page.query_selector_all("button")
                            for btn in all_btns:
                                aria = await btn.get_attribute("aria-label") or ""
                                aria_lower = aria.lower()
                                # "Start Video" => video kapalı, "Stop Video" => video açık
                                if "stop" in aria_lower and "video" in aria_lower:
                                    await btn.click()
                                    logger.info(f"✓ Video kapatıldı (fallback: {aria})")
                                    video_off = True
                                    break
                                elif "turn off" in aria_lower and ("video" in aria_lower or "camera" in aria_lower):
                                    await btn.click()
                                    logger.info(f"✓ Video kapatıldı (fallback: {aria})")
                                    video_off = True
                                    break
                        except:
                            pass
                    
                    if not video_off:
                        # Belki video zaten kapalıdır
                        logger.info("ℹ Video zaten kapalı olabilir veya buton bulunamadı.")
                        
                except Exception as e:
                    logger.warning(f"Video kapatma hatası: {e}")

            except Exception as e:
                logger.error(f"Ses ayarları hatası: {e}")
                import traceback
                traceback.print_exc()
            
            # Join Butonu
            logger.info("Join butonuna basılıyor...")
            join_btn = await self.page.wait_for_selector(self.selectors["join_btn"], state="visible")
            if join_btn:
                # Bazen Agree terms çıkar
                try:
                    agree_btn = await self.page.wait_for_selector(self.selectors["agree_terms_btn"], timeout=2000)
                    if agree_btn:
                        await agree_btn.click()
                except:
                    pass
                
                await join_btn.click()
            else:
                logger.error("Join butonu bulunamadı!")
                return False

            # 3. Bekleme Odası / Giriş Kontrolü
            logger.info("Toplantıya giriş bekleniyor...")
            
            # İlk birkaç saniye bekle, sayfa yüklensin
            await asyncio.sleep(3)
            
            # ÖNCE BEKLEME ODASI KONTROLÜ YAP!
            # (Footer elementi bekleme odasında da olabilir)
            content = await self.page.content()
            
            # Bekleme odası metinleri (Screenshot'tan)
            waiting_indicators = [
                "host has joined",
                "we've let them know",
                "you're here",
                "waiting for the host",
                "waiting room",
                "please wait",
                "bekle",
                "bekleme odası"
            ]
            
            content_lower = content.lower()
            is_waiting_room = any(indicator in content_lower for indicator in waiting_indicators)
            
            if is_waiting_room:
                logger.info("⏳ Bekleme Odası tespit edildi")
                logger.info("⏳ 10 Dakikalık bekleme süresi başlatılıyor...")
                
                # 10 DAKIKA BEKLEME DÖNGÜSÜ (Teams/Meet gibi)
                import time
                from pathlib import Path
                
                wait_start = time.time()
                wait_timeout = 600  # 10 dakika
                BOT_COMMAND_FILE = Path("data/bot_command.json")
                
                while True:
                    elapsed = time.time() - wait_start
                    
                    # Timeout kontrolü
                    if elapsed > wait_timeout:
                        logger.error("❌ Bekleme süresi (10dk) doldu!")
                        return False
                    
                    # İçeri alındık mı kontrol et
                    # ÇİFT KONTROL GEREKLİ:
                    # 1. Waiting text GÖRÜNÜR değil artık (gizli veya yok)
                    # 2. VE meeting toolbar görünür
                    try:
                        # 1. Waiting text hala GÖRÜNÜR mü kontrol et
                        current_content = await self.page.content()
                        
                        # Görünür waiting elementleri ara
                        waiting_visible = False
                        waiting_selectors = [
                            "text=Host has joined",
                            "text=We've let them know",
                            "text=Waiting for the host",
                            "text=Please wait"
                        ]
                        
                        for sel in waiting_selectors:
                            try:
                                elem = await self.page.locator(sel).first
                                if await elem.is_visible():
                                    waiting_visible = True
                                    break
                            except:
                                continue
                        
                        # Eğer waiting text GÖZÜKMÜYORSA, toolbar kontrol et
                        if not waiting_visible:
                            # 2. Meeting-only toolbar elements (bekleme odasında OLMAYAN)
                            meeting_only_selectors = [
                                "button[aria-label*='Mute']",
                                "button[aria-label*='Chat']",  # Chat sadece meeting'de
                                "button[aria-label*='Share']"  # Share sadece meeting'de
                            ]
                            
                            admitted = False
                            for selector in meeting_only_selectors:
                                try:
                                    elem = await self.page.query_selector(selector)
                                    if elem and await elem.is_visible():
                                        admitted = True
                                        logger.info(f"✅ Toplantı elementi tespit edildi: {selector}")
                                        break
                                except:
                                    continue
                            
                            if admitted:
                                logger.info("✅ Bekleme odasından içeri alındık!")
                                logger.info("✅ Katılım Başarılı!")
                                break
                    except Exception as e:
                        logger.debug(f"Admission check error: {e}")
                    
                    # STOP komutu kontrolü
                    if BOT_COMMAND_FILE.exists():
                        try:
                            import json
                            cmd = json.loads(BOT_COMMAND_FILE.read_text("utf-8"))
                            if cmd.get("command") == "stop":
                                logger.info("⛔ STOP komutu alındı (Waiting room)")
                                return False
                        except:
                            pass
                    
                    # Her 30 saniyede log
                    if int(elapsed) % 30 == 0 and int(elapsed) > 0:
                        logger.info(f"⏳ Bekleniyor... ({int(elapsed)}/{wait_timeout} sn)")
                    
                    # 1 SANİYE BEKLE (Meet/Teams gibi)
                    await asyncio.sleep(1)
                    
                    # Pencereyi öne getir (Her 10 saniyede bir)
                    if int(elapsed) % 10 == 0:
                        try:
                            await self.page.bring_to_front()
                        except:
                            pass
            else:
                # Bekleme odası YOK, direkt girebildik
                # Ama toolbar gerçekten var mı emin ol
                try:
                    toolbar = await self.page.wait_for_selector(
                        "div[class*='footer'], button[aria-label*='Audio']", 
                        timeout=10000  # 10 sn bekle
                    )
                    if toolbar:
                        logger.info("✅ Toplantı arayüzü yüklendi!")
                    else:
                        logger.error("Toplantıya girilemedi (Toolbar bulunamadı)")
                        await self.page.screenshot(path="debug_no_toolbar.png")
                        return False
                except Exception as e:
                    logger.error(f"Toplantıya girilemedi: {e}")
                    await self.page.screenshot(path="debug_join_failed.png")
                    return False

            # 4. Teams gibi - Post-join focus YAPMA
            await asyncio.sleep(0.5)
            
            # 5. Sesi Bağla (Computer Audio)
            logger.info("Ses bağlanıyor...")
            try:
                # Bazen otomatik popup çıkar
                await self.page.click(self.selectors["join_audio_btn"], timeout=5000)
                logger.info("Ses bağlandı.")
            except:
                logger.info("Ses butonu bulunamadı veya zaten bağlı.")
            
            # 5. Katılımcı Listesini Aç (Speaker tespiti için önemli olabilir)
            try:
                await self.page.click(self.selectors["participants_btn"], timeout=5000)
                logger.info("Katılımcı listesi açıldı.")
            except:
                pass
            
            # 6. ŞİMDİ PENCEREYI MAXİMİZE ET (Pencere kesin oluşmuş)
            try:
                logger.info("Pencere maximize ediliyor...")
                await asyncio.sleep(1)  # Pencere tamamen yüklensin
                self._bring_to_front_force()
                logger.info("✅ Pencere maximize edildi")
            except Exception as e:
                logger.warning(f"Pencere maximize hatası: {e}")
                
            return True

        except Exception as e:
            logger.error(f"Join hatası: {e}")
            traceback.print_exc()
            return False

    async def send_chat_message(self, message: str):
        """Send a message to meeting chat."""
        try:
            logger.info(f"Mesaj gönderiliyor: {message}")
            
            # Chat butonunu bul ve ZORLA tıkla
            logger.info("Chat butonu tıklanıyor...")
            
            # JS ile Tıkla (Daha güvenli)
            # Direkt JS içinde bulup tıklıyoruz, handle vs uğraşmıyoruz
            js_click_chat = """
            () => {
                const selectors = [
                    "button[aria-label='Chat']",
                    "button[aria-label*='Chat' i]",
                    "button:has-text('Chat')",
                    "div[role='button'][aria-label*='Chat']"
                ];
                
                for (let sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn) {
                        btn.click();
                        return true;
                    }
                }
                
                // XPath fallback
                const xpath = "//button[contains(translate(@aria-label, 'CHAT', 'chat'), 'chat')]";
                const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                if (result.singleNodeValue) {
                    result.singleNodeValue.click();
                    return true;
                }
                
                return false;
            }
            """
            
            clicked = await self.page.evaluate(js_click_chat)
            if clicked:
                logger.info("✓ Chat butonu JS ile tıklandı")
                await asyncio.sleep(2)
            else:
                logger.warning("⚠ Chat butonu JS ile bulunamadı, Playwright aranıyor...")
                try:
                    btn = await self.page.wait_for_selector("button[aria-label='Chat']", timeout=3000)
                    if btn:
                        await btn.click(force=True)
                        logger.info("✓ Playwright ile Chat tıklandı")
                        await asyncio.sleep(2)
                except:
                    logger.error("❌ Chat butonu HİÇBİR ŞEKİLDE tıklanamadı")
                    await self.page.screenshot(path="debug_chat_click_fail.png")
                    return False

            # Mesaj kutusunu bul ve YAZ (SADECE KLAVYE - EN GARANTİ)
            logger.info("Mesaj kutusu aranıyor...")
            try:
                # Önce JS ile focus yapalım
                js_focus_input = """
                () => {
                    const input = document.querySelector('textarea[placeholder*="message" i]') || 
                                  document.querySelector('textarea') ||
                                  document.querySelector('div[contenteditable="true"]');
                    if (input) {
                        input.focus();
                        input.click();
                        return true;
                    }
                    return false;
                }
                """
                await self.page.evaluate(js_focus_input)
                await asyncio.sleep(0.5)
                
                # Sadece klavye ile yaz (Yapıştırma/Value injection yok)
                logger.info("Klavye ile mesaj yazılıyor...")
                await self.page.keyboard.type(message, delay=50) # Her karakter arası 50ms - İnsan gibi
                await asyncio.sleep(0.5)
                await self.page.keyboard.press("Enter")
                logger.info("✓ Mesaj gönderildi (Klavye)")
                await asyncio.sleep(1)
                return True
                    
            except Exception as e:
                logger.error(f"❌ Mesaj yazma hatası: {e}")
                return False
            
        except Exception as e:
            logger.error(f"Chat genel hatası: {e}")
            return False

    async def open_participants_panel(self):
        """Open participants panel - FIXED JS VERSION."""
        try:
            logger.info("Katılımcı paneli açılıyor...")
            
            # JS ile direkt tıkla (Hata vermez)
            js_click_participants = """
            () => {
                const selectors = [
                    "button[aria-label='Participants']",
                    "button[aria-label*='Participants' i]",
                    "button[aria-label*='Katılımcılar' i]",
                    "button:has-text('Participants')",
                    "div[role='button'][aria-label*='Participants']"
                ];
                
                for (let sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
            """
            
            clicked = await self.page.evaluate(js_click_participants)
            
            if clicked:
                logger.info("✓ Katılımcı paneli açıldı (JS)")
                await asyncio.sleep(1)
                return True
            else:
                logger.warning("⚠ Katılımcı butonu JS ile bulunamadı")
                return False
                    
        except Exception as e:
            logger.error(f"Katılımcı paneli hatası: {e}")
            return False

    async def close_chat_panel(self):
        """Close chat panel - HEADER CLOSE BUTTON."""
        try:
            logger.info("Chat paneli kapatılıyor...")
            
            # 1. YÖNTEM: "Close" (X) butonunu arayalım - En temiz yöntem
            close_buttons = [
                "button[aria-label='Close']", 
                "button[aria-label='Close Chat']",
                "button[aria-label='Kapat']",
                "button[aria-label='Sohbeti Kapat']",
                "button.footer-button__chat-icon.is-active", # Bazen active class'ı vardır
                "div.chat-header__action button", # Header içindeki butonlar
                "button:has-text('Close')",
                "button:has-text('Kapat')"
            ]
            
            for selector in close_buttons:
                try:
                    # Sadece panel içindeyse veya active ise
                    btn = await self.page.wait_for_selector(selector, timeout=800, state="visible")
                    if btn:
                        # Butonun gerçekten bir kapatma butonu olduğundan emin olmak zor, ama aria-label güvenilirdir
                        await btn.click()
                        logger.info(f"✓ Chat paneli butonu ile kapatıldı: {selector}")
                        await asyncio.sleep(1)
                        return True
                except:
                    continue
            
            # 2. YÖNTEM: Toolbar butonuna tekrar bas (Toggle)
            logger.info("Close butonu bulunamadı, Toolbar butonu (Toggle) deneniyor...")
            
            js_toggle_chat = """
            () => {
                const btn = document.querySelector("button[aria-label='Chat']") || 
                            document.querySelector("button[aria-label*='Chat' i]");
                            
                // Eğer buton bulunduysa ve 'aria-expanded=true' ise tıkla
                if (btn) {
                    if (btn.getAttribute('aria-expanded') === 'true') {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
            """
            toggled = await self.page.evaluate(js_toggle_chat)
            if toggled:
                logger.info("✓ Chat paneli Toolbar butonu ile kapatıldı")
                await asyncio.sleep(1)
                return True
                
            # 3. YÖNTEM: Escape (Odaklanıp bas)
            logger.info("Escape ile kapatma deneniyor...")
            await self.page.keyboard.press("Escape")
            return True
            
        except Exception as e:
            logger.warning(f"Chat kapatma hatası: {e}")
            return False

    async def get_active_speakers(self):
        """
        Detect currently speaking participants using Zoom Web DOM.
        Uses exact selectors from Zoom's participant panel.
        """
        from pathlib import Path
        from datetime import datetime
        
        debug_log = Path("debug_speaker_detection.txt")
        
        def log_debug(msg):
            try:
                with open(debug_log, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            except: pass
        
        try:
            speakers = []
            all_participants = []
            log_debug("=" * 60)
            log_debug("SPEAKER DETECTION - ZOOM WEB EXACT SELECTORS")
            
            # ============================================
            # Zoom Web Katılımcı Paneli (Sağ Taraf)
            # ============================================
            
            # Tam panel selector
            panel = await self.page.query_selector("#participants-ul, .participants-list-container")
            
            # Panel kapanmış mı kontrol et ve gerekirse tekrar aç
            if not panel:
                log_debug("⚠ Katılımcı paneli kapalı, yeniden açılıyor...")
                import time
                current_time = time.time()
                # Son kontrolden 3 saniye geçmişse tekrar dene
                if current_time - self._last_panel_check > 3:
                    self._last_panel_check = current_time
                    await self.open_participants_panel()
                    await asyncio.sleep(0.5)
                    # Tekrar kontrol et
                    panel = await self.page.query_selector("#participants-ul, .participants-list-container")
                    if panel:
                        log_debug("✓ Panel yeniden açıldı")
                        logger.info("✓ Katılımcı paneli yeniden açıldı")
                    else:
                        log_debug("❌ Panel açılamadı")
                        return []
                else:
                    return []
            
            log_debug("✓ Katılımcı paneli bulundu")
            
            # Tüm katılımcıları bul
            items = await panel.query_selector_all(".participants-li")
            log_debug(f"✓ {len(items)} katılımcı bulundu")
            
            for idx, item in enumerate(items):
                try:
                    # aria-label: "Yusuf Batkitar (Host),computer audio unmuted,video off"
                    aria_label = await item.get_attribute("aria-label") or ""
                    
                    # İsmi çıkar
                    name_el = await item.query_selector(".participants-item__display-name")
                    name = ""
                    if name_el:
                        name = await name_el.text_content()
                        name = name.strip() if name else ""
                    
                    # Alternatif: aria-label'dan
                    if not name and aria_label:
                        name = aria_label.split(",")[0].replace("(Host)", "").replace("(Me)", "").replace("(Co-host)", "").strip()
                    
                    if name:
                        # Bot'un kendisini atla
                        if "sesly" in name.lower() or "(me)" in aria_label.lower():
                            log_debug(f"  [{idx}] {name} → (Bot - atlandı)")
                            continue
                        
                        # EXCLUDED İSİMLER: Gerçek katılımcı olmayan UI elementleri
                        excluded_names = [
                            "frame", "pen_spark", "pen_spark_io", "spark_io",
                            "sesly bot", "toplantı botu", "meeting bot",
                            "localhost", "panel", "bot panel", "sesly asistan",
                            "zoom", "katılım isteği", "join request"
                        ]
                        name_lower = name.lower()
                        if any(ex in name_lower for ex in excluded_names):
                            log_debug(f"  [{idx}] {name} → (Excluded - atlandı)")
                            continue
                        
                        all_participants.append(name)
                        log_debug(f"  [{idx}] {name}")
                        
                        # ============================================
                        # KONUŞMA TESPİTİ
                        # ============================================
                        is_speaking = False
                        method = ""
                        
                        # YÖNTEM 1: voip-speaking-icon (EN GÜVENİLİR!)
                        speaking_icon = await item.query_selector(".participants-icon__voip-speaking-icon")
                        if speaking_icon:
                            is_speaking = True
                            method = "voip-speaking-icon"
                        
                        # YÖNTEM 2: aria-label'da "talking" veya "speaking"
                        if not is_speaking:
                            aria_lower = aria_label.lower()
                            if "talking" in aria_lower or "speaking" in aria_lower:
                                is_speaking = True
                                method = "aria-label"
                        
                        # YÖNTEM 3: YEDEK - audio-unmuted SVG (ana yöntemler çalışmazsa)
                        # Mikrofonu açık olan kişi potansiyel konuşmacı
                        if not is_speaking:
                            unmuted_svg = await item.query_selector("svg[class*='audio-unmuted']")
                            if unmuted_svg:
                                is_speaking = True
                                method = "unmuted-mic (fallback)"
                        
                        if is_speaking:
                            speakers.append(name)
                            log_debug(f"      ★ KONUŞUYOR ({method})")
                        
                except Exception as e:
                    log_debug(f"  [{idx}] HATA: {e}")
            
            log_debug(f"\nSONUÇ: {len(speakers)} konuşmacı, {len(all_participants)} katılımcı")
            log_debug(f"  Konuşanlar: {speakers}")
            log_debug(f"  Tüm katılımcılar: {all_participants}")
            log_debug("=" * 60 + "\n")
            
            # Cache katılımcıları (transkript için)
            self._cached_participants = all_participants
            
            return speakers
            
        except Exception as e:
            logger.error(f"Speaker detection hatası: {e}")
            log_debug(f"FATAL HATA: {e}")
            return []
    
    async def _process_participant_item(self, item, idx, speakers, all_participants, log_debug):
        """Bir katılımcı item'ını işle ve speaking durumunu kontrol et"""
        try:
            # Tüm attributeleri topla
            aria_label = await item.get_attribute("aria-label") or ""
            class_attr = await item.get_attribute("class") or ""
            innerHTML = await item.inner_html()
            
            log_debug(f"  [{idx}] class: '{class_attr[:60]}...'")
            log_debug(f"  [{idx}] aria: '{aria_label[:80]}...'") if aria_label else None
            
            # İsim çıkar
            name = await self._extract_name_from_element(item, log_debug)
            
            if name:
                if name not in all_participants:
                    all_participants.append(name)
                    log_debug(f"      → Katılımcı eklendi: {name}")
            
            # Speaking kontrolü
            is_speaking = False
            method = ""
            
            check_text = f"{aria_label} {class_attr} {innerHTML}".lower()
            
            speaking_keywords = [
                "speaking", "talking", "is-speaking", "active-speaker",
                "audio-on", "unmuted", "voice-active", "audio-level"
            ]
            
            for kw in speaking_keywords:
                if kw in check_text:
                    is_speaking = True
                    method = kw
                    break
            
            # Mikrofon SVG kontrolü (yeşil = aktif)
            if not is_speaking:
                try:
                    mic_svg = await item.query_selector("svg[class*='mic'], svg[class*='audio'], i[class*='unmute']")
                    if mic_svg:
                        mic_class = await mic_svg.get_attribute("class") or ""
                        if "unmute" in mic_class.lower() or "on" in mic_class.lower():
                            is_speaking = True
                            method = "mic-unmuted"
                except: pass
            
            if is_speaking and name and name not in speakers:
                speakers.append(name)
                log_debug(f"      ★ KONUŞUYOR ({method}): {name}")
                
        except Exception as e:
            log_debug(f"  [{idx}] HATA: {e}")
    
    async def _extract_name_from_element(self, elem, log_debug):
        """Element'ten katılımcı ismini çıkar"""
        name = ""
        
        try:
            # 1. aria-label'dan
            aria = await elem.get_attribute("aria-label")
            if aria and "," in aria:
                name = aria.split(",")[0].strip()
                if name:
                    return self._clean_name(name)
            
            # 2. Spesifik name selector'ları
            name_selectors = [
                "span[class*='name']",
                "div[class*='name']", 
                "span[class*='display']",
                "[class*='user-name']",
                "[class*='participant-name']",
                "span:first-child",
            ]
            
            for sel in name_selectors:
                try:
                    name_el = await elem.query_selector(sel)
                    if name_el:
                        text = await name_el.text_content()
                        if text and len(text.strip()) >= 2:
                            return self._clean_name(text.strip())
                except: continue
            
            # 3. Text content
            full_text = await elem.text_content()
            if full_text:
                # İlk satırı veya ilk kelimeyi al
                first_line = full_text.strip().split("\n")[0]
                if len(first_line) >= 2 and len(first_line) <= 50:
                    return self._clean_name(first_line)
                    
        except: pass
        
        return name
    
    def _clean_name(self, name):
        """İsmi temizle"""
        if not name:
            return ""
        return name.replace("(Me)", "").replace("(Host)", "").replace("(Co-host)", "").strip()

    async def get_all_participants(self):
        """
        Katılımcı panelinden TÜM katılımcı isimlerini çeker.
        Zoom Web'in gerçek selector'larını kullanır.
        """
        try:
            # Cache varsa kullan
            if hasattr(self, '_cached_participants') and self._cached_participants:
                return self._cached_participants
            
            participants = []
            
            # Zoom Web panel
            panel = await self.page.query_selector("#participants-ul, .participants-list-container")
            if not panel:
                return []
            
            # Tüm katılımcıları bul
            items = await panel.query_selector_all(".participants-li")
            
            for item in items:
                try:
                    # İsmi çıkar
                    name_el = await item.query_selector(".participants-item__display-name")
                    name = ""
                    if name_el:
                        name = await name_el.text_content()
                        name = name.strip() if name else ""
                    
                    # Alternatif: aria-label
                    if not name:
                        aria = await item.get_attribute("aria-label") or ""
                        if aria:
                            name = aria.split(",")[0].replace("(Host)", "").replace("(Me)", "").replace("(Co-host)", "").strip()
                    
                    # Bot'u ve excluded isimleri atla
                    if name:
                        excluded_names = [
                            "frame", "pen_spark", "pen_spark_io", "spark_io",
                            "sesly bot", "sesly", "toplantı botu", "meeting bot",
                            "localhost", "panel", "bot panel", "sesly asistan"
                        ]
                        name_lower = name.lower()
                        if not any(ex in name_lower for ex in excluded_names):
                            if name not in participants:
                                participants.append(name)
                except: continue
            
            return participants
            
        except Exception as e:
            logger.error(f"Participant list hatası: {e}")
            return []

    async def check_meeting_ended(self):
        """Toplantının bitip bitmediğini veya geçersiz olduğunu kontrol et."""
        if not self.page:
            return True
            
        try:
            # 0. Sayfa Kapandı mı?
            try:
                if self.page.is_closed():
                    logger.info("Sayfa kapandı tespit edildi.")
                    return True
            except:
                pass
            
            # 1. URL Kontrolü
            # Toplantı bitince Zoom genelde '/postattendee' veya '/j/...' yerine ana sayfaya yönlendirir
            url = self.page.url
            if "postattendee" in url or "ended" in url:
                logger.info("URL değişikliği tespit edildi (Meeting Ended).")
                return True
                
            # 2. Modal/Metin Kontrolü
            try:
                content = (await self.page.content()).lower()
                
                # TOPLANTI BİTTİ MESAJLARI
                end_phrases = [
                    "the meeting has ended",
                    "this meeting has been ended by host",
                    "meeting has been ended by host",
                    "toplantı sahibi tarafından sonlandırıldı",
                    "you have been removed",
                    "leave meeting",
                ]
                
                # GEÇERSİZ/ESKİ LİNK MESAJLARI (YENİ!)
                invalid_phrases = [
                    "this meeting id is not valid",
                    "invalid meeting id",
                    "meeting does not exist",
                    "meeting not found",
                    "this meeting link is not valid",
                    "the meeting has expired",
                    "meeting has already ended",
                    "this meeting has not started",
                    "please wait for the host to start this meeting",
                    "waiting for host to start",
                    "this link has expired",
                    "geçersiz toplantı",
                    "toplantı bulunamadı",
                    "toplantı mevcut değil",
                    "bu toplantı linki geçersiz",
                ]
                
                for phrase in end_phrases:
                    if phrase in content:
                        logger.info(f"Toplantı bitiş metni tespit edildi: {phrase}")
                        self.end_reason = "normal"  # Normal bitiş
                        return True

                for phrase in invalid_phrases:
                    if phrase in content:
                        logger.warning(f"⚠️ GEÇERSİZ TOPLANTI TESPİT EDİLDİ: {phrase}")
                        self.end_reason = f"Geçersiz toplantı linki: {phrase}"  # Hata sebebi
                        return True
                    
            except:
                pass
            
            # NOT: Tek katılımcı timeout özelliği kaldırıldı
            # Toplantı sadece host bitirdiğinde veya herkes ayrıldığında sona erer

            return False
            
        except Exception as e:
            logger.error(f"Meeting end check error: {e}")
            return False


    async def close(self):
        """Tarayıcıyı kapat"""
        if self.browser:
            await self.browser.close()
        self.is_running = False

    def _bring_to_front_force(self):
        """Windows API kullanarak Chromium penceresini zorla öne getirir."""
        try:
            import win32gui
            import win32process
            import psutil

            def callback(hwnd, windows):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    
                    title = win32gui.GetWindowText(hwnd)
                    _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                    
                    try:
                        proc = psutil.Process(window_pid)
                        proc_name = proc.name().lower()
                    except:
                        proc_name = "unknown"
                    
                    # Sadece tarayıcıları hedefle
                    BROWSER_PROCESSES = ["chrome.exe", "msedge.exe", "chromium.exe"]
                    if proc_name not in BROWSER_PROCESSES:
                        return

                    # Başlık kontrolü (Zoom toplantısı veya genel Zoom başlığı)
                    # Zoom Web bazen "Zoom - ..." bazen "Launch Meeting - Zoom" vs başlıklar atar.
                    # Basitçe "Zoom" geçen tarayıcı pencerelerini alalım.
                    if "zoom" in title.lower():
                        logger.info(f"[FOCUS MATCH] Window found: '{title}' (PID: {window_pid})")
                        windows.append(hwnd)
                except:
                    pass

            hwnds = []
            win32gui.EnumWindows(callback, hwnds)
            
            if hwnds:
                # En son aktif olanı veya ilk bulduğunu öne al
                target_hwnd = hwnds[0]
                
                # Minimize ise aç
                win32gui.ShowWindow(target_hwnd, 9) # SW_RESTORE
                
                # Öne getir
                try:
                    win32gui.SetForegroundWindow(target_hwnd)
                except Exception as e:
                    # Bazen permission denied verir ama yine de deneriz
                    logger.warning(f"SetForegroundWindow warning: {e}")
                    pass
        except Exception as e:
            logger.warning(f"Pencere öne getirme hatası: {e}")

