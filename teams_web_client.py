
import asyncio
import traceback
import time
from playwright.async_api import async_playwright
import json
import base64
import gzip
import logging
from pathlib import Path

# Logger Setup
logger = logging.getLogger("TeamsWebClient")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[TEAMS-WEB] %(message)s'))
logger.addHandler(handler)

class TeamsWebBot:
    def __init__(self, meeting_url, bot_name="Sesly Bot"):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        
        # Timeout takibi için
        self.waiting_start_time = None
        self.is_running = False
        self.end_reason = None  # Toplantı sona erme sebebi (normal/invalid link)

    async def start(self):
        """Playwright ve tarayıcıyı başlatır."""
        logger.info("Playwright başlatılıyor...")
        self.playwright = await async_playwright().start()
        
        # VPS için headless=True çok daha performanslıdır.
        # Ancak ses yakalamak için bazen headful gerekebilir.
        # Şimdilik headless=False yapıyoruz ki ses/video izinleri easier olsun (veya debug için).
        # Sistem sesini yakalamak için ekranda bir şeylerin oynaması gerekebilir.
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=[
                "--use-fake-ui-for-media-stream",  # Kamera/Mikrofon izinlerini atla
                "--disable-notifications",
                "--window-size=1280,800" # Pencere boyutunu sabitle
            ]
        )
        
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800}, # İçerik boyutunu pencereyle eşle
            permissions=["microphone", "camera"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        self.page = await self.context.new_page()
        
        # WebSocket monitoring'i otomatik enjekte et
        await self.page.add_init_script("""
            window._wsMessages = [];
            window._wsSpeakerData = [];
            
            const OriginalWebSocket = WebSocket;
            window.WebSocket = function(...args) {
                const ws = new OriginalWebSocket(...args);
                
                ws.addEventListener('message', function(event) {
                    const data = event.data;
                    window._wsMessages.push({
                        time: Date.now(),
                        data: typeof data === 'string' ? data : '[Binary]'
                    });
                    
                    // Speaker-related mesajları özel array'e at
                    if (typeof data === 'string' && /speak|participant|roster/i.test(data)) {
                        window._wsSpeakerData.push({
                            time: Date.now(),
                            data: data
                        });
                        console.log('[WS-SPEAKER]', data.substring(0, 200));
                    }
                });
                
                return ws;
            };
            
            console.log('✅ WebSocket monitor active');
        """)
        
        self.is_running = True
        

        
        # Pencereyi ÖNE GETİR (Windows API ile Zorla)
        try:
            await self.page.bring_to_front()
            
            # Browser PID'sini al
            pid = self.browser_process_pid()
            if pid:
                self._bring_to_front_force(pid)
            else:
                # PID yoksa başlığa göre dene
                await asyncio.sleep(1) # Başlığın gelmesini bekle
                self._bring_to_front_force(target_title="Teams")
                
        except Exception as e:
            logger.warning(f"Pencere öne getirme hatası: {e}")
            pass
            
        logger.info("Tarayıcı hazır ve öne getirildi.")

    def browser_process_pid(self):
        """Playwright browser process ID'sini bulmaya çalışır."""
        try:
             # Bu özellik her zaman erişilebilir olmayabilir
             # Chromium launch return value internal process access
             # .process özelliği sync api'de var, async'de _process, _impl_obj vs karmaşık.
             # Basitçe: return None for now, use title based fallback usually safer here without deep hacks
             return None 
        except:
            return None

    def _bring_to_front_force(self, pid=None, target_title=None):
        """Windows API kullanarak pencereyi zorla öne getirir."""
        try:
            import win32gui
            import win32con
            import win32process

            def callback(hwnd, windows):
                try:
                    title = win32gui.GetWindowText(hwnd)
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    
                    # Process Name Kontrolü (Desktop App'i elemek için)
                    _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                    try:
                        import psutil
                        proc = psutil.Process(window_pid)
                        proc_name = proc.name().lower()
                    except:
                        proc_name = "unknown"
                    
                    # BROWSER VALIDATION (IDE veya diğer pencereleri elemek için)
                    BROWSER_PROCESSES = ["chrome.exe", "msedge.exe", "chromium.exe", "opera.exe", "brave.exe"]
                    
                    # Eğer process bir tarayıcı DEĞİLSE, kesinlikle atla!
                    # (Dosya adında 'Teams' geçen IDE pencerelerini önlemek için)
                    if proc_name not in BROWSER_PROCESSES:
                         return

                    match_title = False
                    if pid:
                         if window_pid == pid:
                            match_title = True
                    elif target_title and title:
                        if isinstance(target_title, (list, tuple)):
                             if any(t.lower() in title.lower() for t in target_title):
                                 match_title = True
                        elif target_title.lower() in title.lower():
                            match_title = True
                    
                    if not match_title and not pid and not target_title:
                         if ("teams" in title.lower() or "meet" in title.lower()):
                            match_title = True

                    if match_title:
                        # Log candidate to see what we are finding
                        # logger.info(f"[FOCUS DEBUG] Found candidate: '{title}' (PID: {window_pid}, Proc: '{proc_name}')")

                        # Eğer Teams masaüstü uygulamasıysa (Teams.exe) ATL
                        if "teams.exe" in proc_name or "ms-teams.exe" in proc_name:
                            logger.info(f"[FOCUS SKIP] Desktop App detected: {proc_name}")
                            return

                        # Eğer process bir tarayıcı DEĞİLSE, kesinlikle atla!
                        if proc_name not in BROWSER_PROCESSES:
                             logger.info(f"[FOCUS SKIP] Non-browser process: {proc_name} ('{title}')")
                             return

                        logger.info(f"[FOCUS MATCH] Window found: '{title}' (PID: {window_pid}, Proc: '{proc_name}')")
                        windows.append(hwnd)
                except:
                    pass

            hwnds = []
            win32gui.EnumWindows(callback, hwnds)
            
            if hwnds:
                target_hwnd = hwnds[0]
                
                # ShowWindow ile minimize ise aç (RESTORE)
                # SW_RESTORE = 9
                win32gui.ShowWindow(target_hwnd, 9)
                
                # SAFE FOCUS: Basit SetForegroundWindow
                try:
                    win32gui.SetForegroundWindow(target_hwnd)
                    # win32gui.ShowWindow(target_hwnd, win32con.SW_MAXIMIZE) # Opsiyonel
                except Exception as e:
                    logger.warning(f"Standart focus hatası (Kritik değil): {e}")
                    # Bazen sadece Alt tuşuna basmak işe yarar
                    try:
                         import ctypes
                         user32 = ctypes.windll.user32
                         user32.keybd_event(0x12, 0, 0, 0) # ALT down
                         user32.keybd_event(0x12, 0, 2, 0) # ALT up
                         win32gui.SetForegroundWindow(target_hwnd)
                    except:
                        pass
                        
                logger.info(f"Pencere Windows API ile (Safe) öne getirildi: {target_hwnd}")
        except Exception as e:
            logger.warning(f"Windows API focus hatası: {e}")

    async def join_meeting(self):
        """Toplantıya katılım akışı."""
        try:
            logger.info(f"Linke gidiliyor: {self.meeting_url}")
            await self.page.goto(self.meeting_url, wait_until="networkidle")
            
            # Sayfa yüklendikten sonra TEKRAR öne getirmeyi dene (Başlık değiştiği için)
            # "Görüşmeye katıl" veya "Join conversation" başlıklarını yakalar
            self._bring_to_front_force(target_title=("Teams", "Microsoft Teams", "Görüşmeye katıl", "Join"))

            # POPUP ENGELLEME: "Microsoft Teams açılsın mı?" penceresi için ESC bas
            # Bu native bir dialog olduğu için selector ile seçilemez.
            # Playwright keyboard.press yetmeyebilir, OS seviyesinde basacağız.
            try:
                logger.info("Olası popup için bekleniyor ve OS seviyesinde ESC basılacak...")
                await asyncio.sleep(2) # 3s -> 2s
                
                import ctypes
                user32 = ctypes.windll.user32
                VK_ESCAPE = 0x1B
                
                # Garanti olsun diye birkaç kez ESC bas (OS Level)
                for i in range(3):
                    # Key Down
                    user32.keybd_event(VK_ESCAPE, 0, 0, 0)
                    # Key Up
                    user32.keybd_event(VK_ESCAPE, 0, 2, 0)
                    
                    logger.info(f"ESC basıldı ({i+1}/3)")
                    await asyncio.sleep(0.5) # 1.5s -> 0.5s (Daha seri)
                    
                logger.info("Popup için ESC komutları gönderildi.")
            except Exception as e:
                logger.warning(f"ESC basma hatası: {e}")

            # 1. "Bu tarayıcıda devam et" / "Continue on this browser"
            logger.info("Web arayüzü seçeneği aranıyor...")
            await asyncio.sleep(1) # 2s -> 1s (Daha hızlı)
            
            try:
                # Buton: "Bu tarayıcıda devam et" veya "Continue on this browser"
                # data-tid="joinOnWeb" en güvenilir selector
                web_join_btn = self.page.locator("button[data-tid='joinOnWeb']")
                
                # Eğer data-tid ile bulamazsa metinle dene (TR/EN - CSS OR Selector)
                if await web_join_btn.count() == 0:
                     # CSS virgül (,) operatörü OR anlamına gelir.
                     web_join_btn = self.page.locator('button:has-text("Bu tarayıcıda"), button:has-text("Continue on this browser"), button:has-text("Use the web app")').first

                if await web_join_btn.is_visible(timeout=10000):
                    # Force click bazen overlay varsa işe yarar
                    await web_join_btn.click(force=True)
                    logger.info("Web ile katıl butonu tıklandı. Yönlendirme bekleniyor...")
                    await asyncio.sleep(5) # Sayfa yenilenmesi/yüklenmesi için bekle
                else:
                    logger.warning("Web join butonu bulunamadı (zaten geçilmiş olabilir).")

            except Exception as e:
                logger.warning(f"Web join butonu hatası: {e}")

            # 2. Pre-Join Ekranı (İsim Girme & AV Ayarları)
            logger.info("Pre-join ekranı bekleniyor...")
            
            # İsim input alanı bekleniyor (Robust Selector Strategy)
            name_input = None
            try:
                logger.info("İsim alanı aranıyor (Adınızı yazın)...")
                # 1. data-tid (Standart)
                # 2. Placeholder (TR/EN) - Case sensitive olabilir, o yüzden tam metin ekledim.
                # 3. Generic Text Input (Sayfada genelde tek input olur)
                name_input = self.page.locator(
                    "input[data-tid='prejoin-display-name-input'], "
                    "input[placeholder='Adınızı yazın'], "
                    "input[aria-label='Adınızı yazın'], "
                    "input[placeholder='Type your name'], "
                    "input[type='text']"
                ).first
                
                # Inputun görünmesini bekle
                await name_input.wait_for(state="visible", timeout=10000)
                
                # Temizle ve yaz
                await name_input.click() 
                await name_input.fill(self.bot_name)
                # Enter basarak onayla (Bazen butonu aktifleştirir)
                await name_input.press("Enter")
                logger.info(f"İsim girildi: {self.bot_name}")

            except Exception as e:
                logger.warning(f"İsim girme hatası (Kritik değil, devam ediliyor): {e}")
            
            # 3. Kamera & Mikrofonu Kapat & SES AYARLARI (CABLE Input)
            # BU ADIMLAR "JOIN" BUTONUNA BASMADAN ÖNCE YAPILMALI VE GARANTİ EDİLMELİ.
            
            logger.info("AV ayarları için güvenli döngü başlatılıyor...")

            # Adım 0: "Bilgisayar sesi" (Computer Audio) seçili mi emin ol.
            # Bazen "Ses kullanma" seçili gelir, o zaman menüler gözükmez.
            try:
                comp_audio = self.page.locator("text='Bilgisayar sesi', text='Computer audio'").first
                if await comp_audio.count() > 0:
                    await comp_audio.click(force=True)
                    await asyncio.sleep(1)
            except:
                pass
            
            # --- AUDIO SETUP RETRY LOOP ---
            audio_success = False
            for i in range(3):
                try:
                    logger.info(f"Ses ayarı denemesi {i+1}/3...")
                    
                    # Kullanıcı: "Hoparlör yazısının üstüne tıklaması lazım"
                    # TR: Hoparlör, EN: Speaker
                    speaker_text_el = self.page.locator("*:has-text('Hoparlör'), *:has-text('Speaker')").locator("visible=true").last
                    
                    if await speaker_text_el.count() > 0:
                        txt = await speaker_text_el.text_content()
                        if "CABLE Input" in (txt or ""):
                            logger.info("✅ Hoparlör zaten 'CABLE Input' (Tespit edildi).")
                            audio_success = True
                            break
                        
                        logger.info(f"Hoparlör yazısına tıklanıyor: {txt[:30]}...")
                        await speaker_text_el.click(force=True)
                        await asyncio.sleep(1)
                        
                        # Menüden CABLE Input seç
                        cable_opt = self.page.locator("li[role='option']:has-text('CABLE Input'), span:has-text('CABLE Input')").first
                        if await cable_opt.count() > 0:
                             await cable_opt.click(force=True)
                             logger.info("✅ 'CABLE Input' menüden seçildi.")
                             audio_success = True
                             await self.page.mouse.click(0, 0)
                             break
                    else:
                        logger.warning("'Hoparlör/Speaker' yazısı bulunamadı.")
                    
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.warning(f"Ses ayarı hatası: {e}")
                    await asyncio.sleep(2)

            if not audio_success:
                logger.error("❌ Ses cihazı ayarlanamadı.")

            # --- VIDEO TOGGLE RETRY LOOP ---
            # Kullanıcı: "Arka plan filtrelerinin solunda kamera switchi var (arada emoji var)"
            # Layout Snapshot: [Camera Icon] ... [Switch] ... [Filter Icon] [Text: Arka plan filtreleri]
            for i in range(3):
                try:
                    cam_toggle = None
                    
                    # 1. En Basit: Aria Label (Öncelikli)
                    potential_cams = self.page.locator(
                        "[aria-label='Kamera'], [aria-label='Camera'], "
                        "[aria-label='Görüntü'], [aria-label='Video'], "
                        "[title='Kamera'], [title='Camera']"
                    )
                    
                    if await potential_cams.count() > 0:
                        for idx in range(await potential_cams.count()):
                            el = potential_cams.nth(idx)
                            if await el.is_visible():
                                cam_toggle = el
                                logger.info(f"Kamera butonu aria-label ile bulundu: {await el.get_attribute('aria-label')}")
                                break

                    # 2. Layout Bazlı (Screenshot Referanslı)
                    # "Arka plan filtreleri" metnini çapa olarak kullanıyoruz.
                    if not cam_toggle:
                        xpath_text = "(//*[contains(text(), 'Arka plan filtreleri')] | //*[contains(text(), 'Background filters')])[1]"
                        
                        # 2a. Yazıdan geriye doğru giden İLK Switch (Aradaki icon/emoji ne olursa olsun atlar)
                        # *[@role='switch'] herhangi bir tag olabilir (div, button, span)
                        cam_toggle_switch = self.page.locator(f"{xpath_text}/preceding::*[@role='switch'][1]").first
                        if await cam_toggle_switch.count() > 0:
                            cam_toggle = cam_toggle_switch
                            logger.info("Layout: Metin -> Geriye doğru ilk Switch bulundu.")
                        
                        # 2b. Eğer switch yoksa, switch'in de solundaki "Kamera İkonu"na tıkla.
                        # Bu genellikle en soldaki ikondur.
                        if not cam_toggle:
                             # Yazıdan geriye doğru giden, switch olmayan ama 'Video' veya 'Camera' ikonunu andıran şey.
                             cam_toggle_icon = self.page.locator(f"{xpath_text}/preceding::*[contains(@data-icon, 'Video') or contains(@class, 'ui-icon')][last()]").first
                             if await cam_toggle_icon.count() > 0:
                                 cam_toggle = cam_toggle_icon
                                 logger.info("Layout: Metin -> Geriye doğru Kamera İkonu bulundu.")

                    # 3. Genel Yedek (.ui-icon__filled)
                    if not cam_toggle or await cam_toggle.count() == 0:
                         cam_toggle = self.page.locator(".ui-icon__filled[data-icon='Video']").first

                    # Aksiyon
                    if cam_toggle and await cam_toggle.count() > 0:
                         # Durum kontrolü
                         try:
                             val_pressed = await cam_toggle.get_attribute("aria-pressed")
                             val_checked = await cam_toggle.get_attribute("aria-checked")
                             title_val = await cam_toggle.get_attribute("title") or ""
                             
                             # Bazen title "Kamerayı kapat" derse açık demektir.
                             is_on = (val_pressed == "true") or (val_checked == "true") or ("kapat" in title_val.lower()) or ("turn off" in title_val.lower())
                         except: 
                             is_on = None # Durum okunamadı

                         # Eylem
                         if is_on:
                             logger.info("Kamera AÇIK tespit edildi. Kapatılıyor...")
                             await cam_toggle.click(force=True)
                             await asyncio.sleep(1)
                             # Check closure (Basit kontrol)
                             val_after = await cam_toggle.get_attribute("aria-pressed")
                             if val_after != "true":
                                 logger.info("✅ Kamera başarıyla kapatıldı.")
                                 break
                         elif is_on is None:
                             # Durum bilinmiyor. İlk turda bas, sonra bekle.
                             if i == 0:
                                 logger.info("Kamera durumu belirsiz, kapatmak için tıklanıyor.")
                                 await cam_toggle.click(force=True)
                                 await asyncio.sleep(1)
                             else:
                                 pass
                         else:
                             logger.info("✅ Kamera zaten kapalı.")
                             break
                    else:
                        logger.warning("Kamera butonu bu turda bulunamadı.")
                        
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"Kamera kapatma hatası: {e}")
                    await asyncio.sleep(1)
            
            # --- MICROPHONE TOGGLE (KEYBOARD ONLY) ---
            # Kullanıcı isteği: Selectorler sorunlu olduğu için sadece Ctrl+Shift+M kullanılacak.
            # Risk: Kör toggle. Eğer önceden kapalıysa açabilir. Ancak genelde açık gelir.
            # Önlem: Çakışmayı önlemek için diğer tüm algoritmalar kaldırıldı.
            logger.info("Mikrofon kontrolü: Klavye kısayolu (Ctrl+Shift+M) gönderiliyor...")
            try:
                # Sayfaya odaklan (Garantili Focus)
                await self.page.click("body", force=True)
                await asyncio.sleep(0.5)
                
                # Kısayol: Mute/Unmute
                await self.page.keyboard.press("Control+Shift+M")
                logger.info("✅ Ctrl+Shift+M komutu gönderildi.")
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.warning(f"Mikrofon kısayol hatası: {e}")
            
            # Scroll (Emin olmak için)
            try:
                await self.page.keyboard.press("PageDown")
                await asyncio.sleep(1)
            except: pass

            # Selectors:
            # 1. data-tid='prejoin-join-button' (Standart)
            # 2. Text: "Şimdi katıl", "Join now", "Katıl", "Join" (Genişletilmiş)
            # 3. Class içeren: 'join-btn' (Bazen classlarda olur)
            
            join_btn = self.page.locator(
                "button[data-tid='prejoin-join-button'], "
                "button:has-text('Şimdi katıl'), "
                "button:has-text('Join now'), "
                "button:has-text('Katıl'), "
                "button:has-text('Join')"
            ).first
            
            logger.info("Katıl butonu aranıyor (Genişletilmiş arama)...")
            
            # Debug screenshot kaldırıldı
            
            # CRITICAL FIX: is_visible beklemez, anlık kontrol eder. wait_for kullanmalıyız.
            # CRITICAL FIX: is_visible beklemez, anlık kontrol eder. wait_for kullanmalıyız.
            try:
                # Butonun görünür olmasını bekle
                await join_btn.wait_for(state="visible", timeout=30000)
                await join_btn.scroll_into_view_if_needed()
                
                # RETRY LOGIC (3 Kere Dene)
                clicked_successfully = False
                
                # Lobide olup olmadığımızı anlayacak metinler
                lobby_indicators = (
                     "button[data-tid='hangup-button'], " # Zaten girdiysek
                     "*:has-text('Someone in the meeting should let you in soon'), "
                     "*:has-text('Toplantıdaki birisi sizi'), "
                     "*:has-text('sizi içeri almalı'), "
                     "*:has-text('kabul edilmeyi'), "
                     "*:has-text('lobide'), "
                     "*:has-text('Lobi'), "
                     "*:has-text('yakında'), "
                     "*:has-text('bildireceğiz'), "
                     "*:has-text('almasını'), " # "Birisinin sizi almasını bekliyor"
                     "*:has-text('ev sahibi'), "
                     "*:has-text('içeri alacak'), " # New from screenshot
                     "*:has-text('Kısa sürede'), "  # New from screenshot
                     "*:has-text('Waiting for people'), "
                     "*:has-text('let you in')"
                )

                for attempt in range(3):
                    try:
                        logger.info(f"'Katıl' butonuna basılıyor (Deneme {attempt+1}/3)...")
                        await join_btn.click(force=True)
                        
                        # Tıkladıktan sonra butonun kaybolmasını veya URL'in değişmesini bekle
                        await asyncio.sleep(5)
                        
                        # KONTROL 1: Buton kayboldu mu?
                        if not await join_btn.is_visible():
                            logger.info("✅ 'Katıl' butonu kayboldu (Tıklama başarılı).")
                            clicked_successfully = True
                            break
                        
                        # KONTROL 2: Buton var ama Lobide miyiz? (User Report)
                        if await self.page.locator(lobby_indicators).first.is_visible():
                             logger.info("✅ Lobi/Bekleme yazıları tespit edildi (Tıklama başarılı).")
                             clicked_successfully = True
                             break
                        
                        logger.warning("⚠️ Buton hala görünür ve lobi yazısı yok, tekrar denenecek...")
                            
                    except Exception as e:
                        logger.warning(f"Tıklama hatası ({attempt+1}): {e}")
                        await asyncio.sleep(2)

                if not clicked_successfully:
                     logger.warning("3 denemeye rağmen buton kaybolmadı, devam ediliyor.")


            except Exception as e:
                logger.error(f"Katıl butonu bulunamadı (Timeout): {e}")
                raise Exception("Join button not found")

            # 5. Katılım Doğrulama (Post-Click Check)
            # VPS için NET Başarı/Başarısızlık Dönmeli.
            logger.info("Katılım durumu kontrol ediliyor (Toolbar veya Bekleme Odası)...")
            
            try:
                logger.info("Katılım isteği gönderildi, bağlantı bekleniyor...")
                # UI'ın tepki vermesi için kısa bir süre tanı
                # UI Uyandırma (Mouse Move) - Kontrollerin görünmesi için
                try:
                    await self.page.mouse.move(500, 500)
                    await asyncio.sleep(0.5)
                    await self.page.mouse.move(100, 100)
                except: pass

                # Mevcut URL'i logla
                logger.info(f"Verification Check URL: {self.page.url}")

                # Başarı veya Bekleme Odası Göstergeleri
                # 1. Toplantı İçi: Toolbar butonları (Hangup, Mic, Leave) veya Video/Canvas elemanları
                #    EK: Sohbet ve Kişiler butonları (Top bar'da sabit)
                # 2. Bekleme Odası: "Someone in the meeting...", "sizi içeri almalı"
                
                check_selector = (
                    "button[data-tid='hangup-button'], "
                    "button[data-tid='microphone-mute-button'], "
                    "div[data-tid='call-controls'], "
                    "button[aria-label='Leave'], "
                    "button[aria-label='Ayrıl'], "
                    "button[aria-label='Sohbet'], button[aria-label='Chat'], "  # Chat btn
                    "button[aria-label='Kişiler'], button[aria-label='People'], " # People btn
                    "video, canvas, "  # Generic video/display elements
                    "ul[role='list'], " # Participant list potential
                    "*:has-text('Someone in the meeting should let you in soon'), "
                    "*:has-text('Toplantıdaki birisi sizi'), "
                    "*:has-text('sizi içeri almalı'), "
                    "*:has-text('kabul edilmeyi'), "
                    "*:has-text('lobide'), "
                    "*:has-text('almasını'), "
                    "*:has-text('ev sahibi'), "
                    "*:has-text('yakında'), "
                    "*:has-text('içeri alacak'), "
                    "*:has-text('Kısa sürede'), "
                    "*:has-text('Waiting for people in the meeting to let you in')"
                )
                
                # İlk bağlantı için 60sn bekle (Ağ yavaşlığı vs.)
                first_indicator = self.page.locator(check_selector).first
                await first_indicator.wait_for(state="visible", timeout=60000)
                
                # Şimdi ne gördüğümüze bakalım
                content_text = await self.page.content()
                in_lobby = ("Someone in the meeting" in content_text) or \
                           ("sizi içeri almalı" in content_text) or \
                           ("Toplantıdaki birisi sizi" in content_text) or \
                           ("kabul edilmeyi" in content_text) or \
                           ("lobide" in content_text) or \
                           ("almasını" in content_text) or \
                           ("ev sahibi" in content_text) or \
                           ("yakında" in content_text) or \
                           ("içeri alacak" in content_text) or \
                           ("Kısa sürede" in content_text) or \
                           ("Waiting for people" in content_text) or \
                           ("let you in" in content_text)

                if in_lobby:
                    logger.info("⚠️ Durum: Bekleme Odası (Lobby) tespit edildi.")
                    logger.info("⏳ 10 Dakikalık bekleme süresi başlatılıyor...")
                    
                    # 10 dakika (600 saniye) boyunca içeri alınmayı bekle
                    wait_start = time.time()
                    admitted = False
                    
                    while (time.time() - wait_start) < 600:
                        # Toplantı içi butonları kontrol et
                        in_meeting_indicators = self.page.locator(
                            "button[data-tid='hangup-button'], "
                            "button[data-tid='microphone-mute-button'], "
                            "div[data-tid='call-controls'], "
                            "button[aria-label='Leave'], "
                            "button[aria-label='Ayrıl'], "
                            "button[aria-label='Sohbet'], "
                            "button[aria-label='Kişiler'], "
                            "div[data-tid='participant-avatar'], "  # Teams Light Indicator
                            "div[data-stream-type='Video'], "       # Video feed indicator
                            "button[id='hangup-button']"            # ID fallback
                        ).first
                        
                        if await in_meeting_indicators.count() > 0 and await in_meeting_indicators.is_visible():
                            logger.info("✅ Bekleme odasından içeri alındık!")
                            admitted = True
                            break
                            
                        # Belki atıldık veya hata oldu? (Opsiyonel kontrol)
                        
                        await asyncio.sleep(2) # 2 saniyede bir kontrol (Hızlandırıldı)
                        
                    if not admitted:
                        logger.error("❌ Bekleme odası zaman aşımı (10 dakika). Toplantıya alınmadı.")
                        return False
                    
                else:
                    logger.info("✅ Doğrudan toplantı arayüzü tespit edildi.")

                logger.info("✅ Katılım Başarılı!")
                return True
                
            except Exception as e:
                logger.error(f"Katılım doğrulama başarısız (Timeout/Error): {e}")
                return False

        except Exception as e:
            logger.error(f"Katılım hatası: {traceback.format_exc()}")
            return False

    async def open_chat(self):
        """Chat panelini açar (Robust with Wait)."""
        try:
            # UI Uyandırma (Toolbar gizli olabilir veya yükleniyor olabilir)
            try:
                await self.page.mouse.move(500, 500)
                await asyncio.sleep(0.5)
            except: pass

            logger.info("Chat butonu aranıyor...")
            # 1. Text/Label Bazlı (En Güvenilir)
            chat_selectors = [
                "button[aria-label='Sohbet']", 
                "button[aria-label='Chat']", 
                "button:has-text('Sohbet')", 
                "button:has-text('Chat')",
                "button[data-tid='chat-button']"
            ]
            
            chat_btn = self.page.locator(", ".join(chat_selectors)).first
            
            # Bekle (Yükleme gecikmeleri için)
            try:
                await chat_btn.wait_for(state="visible", timeout=15000)
            except:
                logger.warning("Chat butonu 15sn içinde görünmedi.")
                return

            if await chat_btn.is_visible():
                # Zaten açık mı?
                is_pressed = await chat_btn.get_attribute("aria-pressed")
                if is_pressed == "true":
                    logger.info("Chat paneli zaten açık.")
                else:
                    await chat_btn.click()
                    logger.info("Chat butonuna tıklandı.")
                    await asyncio.sleep(2) # Panelin açılmasını bekle
            else:
                logger.warning("Chat butonu bulunamadı (Visible değil).")
                
        except Exception as e:
            logger.error(f"Chat açma hatası: {e}")

    async def send_message(self, message):
        """Chat mesajı gönderir."""
        try:
            await self.open_chat()
            
            logger.info(f"Mesaj yazılıyor: {message}")
            
            # Mesaj kutusu
            editor = self.page.locator("div[role='textbox'], div[contenteditable='true'], textarea[data-tid='ckeditor-new-message']").last
            
            if await editor.count() > 0:
                await editor.wait_for(state="visible", timeout=10000)
                await editor.click()
                await editor.fill(message) 
                await asyncio.sleep(0.5)
                await editor.press("Enter")
                
                send_btn = self.page.locator("button[aria-label='Gönder'], button[aria-label='Send'], button[data-tid='newMessage-send-button']").last
                if await send_btn.is_visible():
                    await send_btn.click()
                    logger.info("Gönder butonuna basıldı.")
                
                logger.info("Mesaj gönderim işlemi tamamlandı.")
            else:
                logger.error("Mesaj kutusu (Editor) bulunamadı.")

        except Exception as e:
            logger.error(f"Mesaj gönderme hatası: {e}")

    async def open_participants_list(self):
        """Katılımcı listesini açar (Robust with Wait)."""
        try:
             # UI Uyandırma
            try:
                await self.page.mouse.move(500, 500)
                await asyncio.sleep(0.5)
            except: pass

            logger.info("Katılımcı listesi butonu aranıyor...")
            
            # Selector listesi
            people_selectors = [
                "button[aria-label='Kişiler']",
                "button[aria-label='People']", 
                "button[aria-label='Katılımcılar']",
                "button:has-text('Kişiler')", 
                "button:has-text('People')",
                "button:has-text('Katılımcılar')",
                "button[data-tid='participants-button']"
            ]
            
            # Tüm adayları bul
            candidates = self.page.locator(", ".join(people_selectors))
            count = await candidates.count()
            logger.info(f"Kişiler butonu için {count} aday bulundu.")
            
            clicked = False
            for i in range(count):
                btn = candidates.nth(i)
                if await btn.is_visible():
                    # Zaten basılı mı?
                    is_pressed = await btn.get_attribute("aria-pressed")
                    if is_pressed == "true":
                        logger.info(f"Aday {i}: Katılımcı listesi zaten açık.")
                        clicked = True
                        break
                    
                    try:
                        logger.info(f"Aday {i}: Görünür buton bulundu, tıklanıyor...")
                        await btn.click()
                        await asyncio.sleep(2)
                        clicked = True
                        break
                    except Exception as e:
                        logger.warning(f"Aday {i} tıklama hatası: {e}")
            
            if not clicked:
                logger.warning("Hiçbir 'Kişiler' butonu tıklanamadı veya bulunamadı.")

        except Exception as e:
            logger.error(f"Katılımcı listesi açma hatası: {e}")

    async def _extract_ws_speaker_data(self):
        """
        WebSocket'ten yakalanan rosterUpdate mesajlarını decode eder.
        serverMuted: false olanları aktif konuşmacı olarak döndürür.
        """
        try:
            import base64
            import gzip
            
            result = await self.page.evaluate("""
                () => {
                    if (!window._wsSpeakerData) return null;
                    
                    // Son 50 speaker mesajını al (yeterince geniş)
                    const recent = window._wsSpeakerData.slice(-50);
                    
                    return {
                        totalMessages: window._wsMessages.length,
                        speakerMessages: window._wsSpeakerData.length,
                        recentSpeaker: recent
                    };
                }
            """)
            
            if not result or not result.get('recentSpeaker'):
                return []
            
            active_speakers = []
            
            # Her mesajı decode et
            for msg in result['recentSpeaker']:
                try:
                    # WebSocket mesajı format: "3:::{json_data}"
                    data_str = msg['data']
                    if ':::' not in data_str:
                        continue
                    
                    # JSON kısmını al
                    json_part = data_str.split(':::')[1]
                    msg_data = json.loads(json_part)
                    
                    # rosterUpdate mesajlarını kontrol et
                    if '/rosterUpdate/' not in msg_data.get('url', ''):
                        continue
                    
                    # Body'i decode et (gzip + base64)
                    body_b64 = msg_data.get('body', '')
                    if not body_b64:
                        continue
                    
                    try:
                        # Base64 decode
                        decoded = base64.b64decode(body_b64)
                        # Gzip decompress
                        decompressed = gzip.decompress(decoded)
                        # UTF-8 decode
                        text = decompressed.decode('utf-8')
                        # JSON parse
                        roster_data = json.loads(text)
                        
                        # Participants'ları tara
                        participants = roster_data.get('participants', {})
                        
                        for participant_id, participant_info in participants.items():
                            display_name = participant_info.get('details', {}).get('displayName')
                            
                            if not display_name:
                                continue
                            
                            # Endpoints'leri kontrol et
                            endpoints = participant_info.get('endpoints', {})
                            
                            for endpoint_id, endpoint_info in endpoints.items():
                                # Call veya Lobby içindeki mediaStreams'i kontrol et
                                call_data = endpoint_info.get('call', {})
                                lobby_data = endpoint_info.get('lobby', {})
                                
                                for location_data in [call_data, lobby_data]:
                                    if not location_data:
                                        continue
                                    
                                    media_streams = location_data.get('mediaStreams', [])
                                    
                                    for stream in media_streams:
                                        if stream.get('type') == 'audio':
                                            # ÖNCE: Gerçek speaking göstergelerini kontrol et
                                            is_speaking = stream.get('isActiveSpeaker', False) or \
                                                         stream.get('isSpeaking', False) or \
                                                         stream.get('speaking', False)
                                            
                                            # YEDEK: Eğer speaking field yoksa, unmuted kontrolü
                                            # (ama sadece tek kişi unmuted ise güvenilir)
                                            if not is_speaking and not stream.get('serverMuted', True):
                                                # Mikrofon açık - potansiyel konuşmacı
                                                is_speaking = True
                                            
                                            if is_speaking:
                                                if display_name not in active_speakers:
                                                    active_speakers.append(display_name)
                                                    logger.info(f"🎤 [WS-ROSTER] Active speaker: {display_name}")
                                                break
                        
                    except Exception as e:
                        logger.debug(f"Roster decode error: {e}")
                        continue
                        
                except Exception as e:
                    logger.debug(f"Message parse error: {e}")
                    continue
            
            return active_speakers
            
        except Exception as e:
            logger.error(f"WebSocket extraction error: {e}")
            return []

    async def get_participants(self):
        """Katılımcı listesini tarar ve konuşanları tespit eder (Debug Modlu)."""
        active_speakers = []
        # DEBUG LOG HEADER
        debug_log = [f"--- SCAN: {time.strftime('%X')} ---"]

        # =========================================================================
        # PRIORITY 1: WebSocket rosterUpdate (Teams Internal API) - EN DOĞRU!
        # =========================================================================
        ws_speakers = await self._extract_ws_speaker_data()
        
        # İlk denemede boşsa, 2 saniye bekle ve tekrar dene (WebSocket mesajları için)
        if not ws_speakers:
            debug_log.append("[WS-ROSTER] İlk denemede mesaj yok, 2s bekleyip tekrar deneniyor...")
            await asyncio.sleep(2)
            ws_speakers = await self._extract_ws_speaker_data()
        
        if ws_speakers:
            debug_log.append(f"[WS-ROSTER] {len(ws_speakers)} active speakers detected via WebSocket")
            for speaker in ws_speakers:
                debug_log.append(f"   -> WebSocket Speaker: {speaker}")
            
            # WebSocket'ten veri geldiyse, bunu kullan ve döndür
            active_speakers = ws_speakers
            
            # Debug log yaz
            try:
                Path("debug_speaker_detection.txt").write_text("\n".join(debug_log), encoding="utf-8")
            except: pass
            
            logger.info(f"🗣️ Konuşanlar (WebSocket): {', '.join(active_speakers)}")
            return active_speakers
        else:
            debug_log.append("[WS-ROSTER] No WebSocket data after retry, falling back to Grid/List scan")
        
        # =========================================================================
        # FALLBACK: Grid & List Scan (Eski yöntem)
        # =========================================================================
        tid_elements = []
        try:
             # data-stream-type='Video' veya data-tid
             tid_elements = await self.page.locator("div[data-tid][data-stream-type]").all()
        except: pass

        has_grid_elements = len(tid_elements) > 0
        
        if has_grid_elements:
             debug_log.append(f"Grid modu aktif ({len(tid_elements)} element). Liste açılmayacak.")
        else:
             # Liste açık mı? Değilse açmayı dene (Legacy Mode / Full Teams)
             if not await self._is_participants_list_open():
                 try:
                     # logger.info("Grid boş, liste açılıyor...") # Spam olmasın
                     await self.open_participants_list()
                     await asyncio.sleep(0.5)
                 except: pass

        try:
             # GRID ELEMENTLERİNİ İŞLE (STRATEGY 0 & Unmuted)
             all_participants = []  # İlk önce TÜM katılımcıları topla
             
             for el in tid_elements:
                    try:
                        # İsim doğrulama
                        tid_name = await el.get_attribute("data-tid")
                        name = self._clean_name(tid_name, "")
                        
                        if name and name not in all_participants:
                            all_participants.append(name)
                            debug_log.append(f"   [GRID] Participant found: {name}")
                        
                        # ŞİMDİ konuşma kontrolü yap
                        if name:
                            # 1. Style (Glow) Kontrolü
                            style_attr = await el.get_attribute("style") or ""
                            # Teams'de konuşan kişinin çerçevesi
                            if "outline" in style_attr or "box-shadow" in style_attr or "border" in style_attr:
                                if name not in active_speakers:
                                    active_speakers.append(name)
                                    debug_log.append(f"   -> MATCH STRATEGY 0 (TID+Style): {name}")
                            
                            # 2. YEDEK: Unmuted Icon Kontrolü (ana yöntem çalışmazsa)
                            # Mikrofonu açık olan kişi potansiyel konuşmacı
                            if name not in active_speakers:
                                try:
                                    icon_paths = await el.locator(".ui-icon svg path").all()
                                    for path in icon_paths:
                                        d_attr = await path.get_attribute("d") or ""
                                        # Muted Icon'da slash var
                                        if "15 15" in d_attr or "16 16" in d_attr or "l15 15" in d_attr: 
                                            continue 
                                        # Filled icon = unmuted
                                        path_class = await path.get_attribute("class") or ""
                                        if "ui-icon__filled" in path_class: 
                                            active_speakers.append(name)
                                            debug_log.append(f"   -> MATCH STRATEGY 0.5 (Unmuted Icon): {name}")
                                            break
                                except: pass
                    except: pass
             
             # FALLBACK - KALDIRILDI!
             # Eski kod: Hiç konuşan bulunamazsa TÜM katılımcıları döndürüyordu.
             # Bu YANLIŞ çünkü herkes konuşuyor gibi görünüyordu.
             # Artık: Konuşan yoksa boş liste döndürülüyor - bu doğru davranış.
             if not active_speakers and all_participants:
                 debug_log.append(f"   [INFO] No active speakers detected. {len(all_participants)} participant(s) in meeting.")

             
        except Exception as e:
            debug_log.append(f"Grid Scan Error: {e}")
        
        # Eğer yukarıda bulduysak, listeye bakmaya devam et (Yedek olarak)
        # Ama aktif konuşmacı bulduysak listeyi çok zorlamaya gerek yok
        
        # -------------------------------------------------------------------------
        # STRATEGY 1: LIST SCAN (Legacy/Side Panel)
        # -------------------------------------------------------------------------
        try:
            # GLOBAL STRATEGY (Grid & List): Sayfadaki tüm 'Speaking' işaretlerini tara
            # Bu, liste kapalı olsa bile ana ekrandaki (Grid) konuşmacıları yakalar.
            try:
                global_speakers = self.page.locator("[data-is-speaking='true'], [data-active-speaker-id]")
                g_count = await global_speakers.count()
                for i in range(g_count):
                    try:
                        el = global_speakers.nth(i)
                        
                        # İsim bulmaya çalış
                        # Genelde bu elementin içinde veya aria-label'ında yazar.
                        text_val = await el.inner_text()
                        aria_val = await el.get_attribute("aria-label") or ""
                        
                        name = self._clean_name(text_val, aria_val)
                        if name and name not in active_speakers:
                            active_speakers.append(name)
                            # Debug log'a ekleyelim (List döngüsü dışında)
                            # logger.info(f"[GLOBAL MATCH] Found speaker via global scan: {name}") 
                    except: pass
            except: pass
            
            # Legacy Code continues...
            # GLOBAL STRATEGY 0: data-tid Name Extraction (Most Reliable)


            # GLOBAL STRATEGY 2: CSS Style (Outline/Box-Shadow) - Teams Light
            # "outline" veya "box-shadow" style'ı olan div'leri bul (Mavi/Mor renkli)
            try:
                # Teams Blue: rgb(0, 120, 212) | Teams Purple: rgb(98, 100, 167)
                style_speakers = await self.page.locator("div[style*='outline'], div[style*='box-shadow']").all()
                for el in style_speakers:
                    try:
                        style_attr = await el.get_attribute("style")
                        # Renk kontrolü (Basitçe 'rgb' var mı diye bakalım, her renk kabulümüz şimdilik)
                        if "rgb" in style_attr:
                            text_val = await el.inner_text()
                            aria_val = await el.get_attribute("aria-label") or ""
                            
                            # İsim text'te yoksa, içindeki IMG alt tagine bak (Avatar)
                            if not text_val:
                                img = el.locator("img").first
                                if await img.count() > 0:
                                    text_val = await img.get_attribute("alt") or await img.get_attribute("title") or ""

                            name = self._clean_name(text_val, aria_val)
                            
                            if name and name not in active_speakers:
                                active_speakers.append(name)
                                # debug_log.append(f"   -> MATCH STRATEGY 4 (CSS): {name}")
                    except: pass
            except: pass

            # GLOBAL STRATEGY 3: React Fiber (Internal State) - "Nuclear Option"
            # DOM üzerinde activeSpeaker bilgisini React içinden çeker.
            try:
                fiber_speakers = await self.page.evaluate("""
                    () => {
                        const speakers = [];
                        // Tüm adayları bul
                        const roots = document.querySelectorAll("div.video-container, div[data-tid='video-tile'], div");
                        
                        roots.forEach(root => {
                            const key = Object.keys(root).find(k => k.startsWith("__reactFiber"));
                            if (key) {
                                const fiber = root[key];
                                const props = fiber.memoizedProps || fiber.pendingProps;
                                
                                // Olası prop isimleri
                                if (props?.activeSpeaker || props?.isSpeaking || props?.speaking) {
                                    // İsim bul (displayName, name, veya child text)
                                    let name = props.displayName || props.name;
                                    
                                    // Eğer props içinde isim yoksa, DOM'dan al
                                    if (!name && root.innerText) name = root.innerText;
                                    
                                    if (name) speakers.push(name);
                                }
                            }
                        });
                        return speakers;
                    }
                """)
                
                if fiber_speakers:
                    for s in fiber_speakers:
                        name = self._clean_name(str(s), "")
                        if name and name not in active_speakers:
                             active_speakers.append(name)
                             # debug_log.append(f"   -> MATCH STRATEGY 5 (FIBER): {name}")
            except: pass

            # Liste konteynerini bul
            participant_list = self.page.locator("ul[role='list'], div[role='list']").last
            
            # SELF-HEALING: Listeyi bulamazsa AÇMAYI DENE (SADECE Legacy Mode'daysa!)
            if not has_grid_elements and await participant_list.count() == 0:
                logger.warning("Katılımcı listesi kapalı görünüyor. Tekrar açılmaya çalışılıyor...")
                await self.open_participants_list() # Retry opening
                await asyncio.sleep(2)
            # SELF-HEALING: Listeyi bulamazsa AÇMAYI DENE (Bu kısım artık yukarıdaki Grid-First mantığına taşındı)
            # if await participant_list.count() == 0:
            #     logger.warning("Katılımcı listesi kapalı görünüyor. Tekrar açılmaya çalışılıyor...")
            #     await self.open_participants_list() # Retry opening
            #     await asyncio.sleep(2)
                
            #     # Tekrar kontrol et
            #     participant_list = self.page.locator("ul[role='list'], div[role='list']").last
            
            # Hala yoksa pes et (Ama Global Strategy çalıştıysa active_speakers dolu olabilir!)
            if await participant_list.count() == 0:
                if active_speakers:
                     # Liste yok ama Global Scan çalıştı -> Başarılı!
                     return active_speakers
                
                # Debug kaldırıldı - sadece uyarı logla
                if not Path("_debug_logged").exists():
                    logger.warning("Katılımcı listesi bulunamadı.")
                return []

            # Tüm elemanları al
            li_items = await participant_list.locator("li[role='listitem'], div[role='listitem']").all()
            
            # DEBUG LOG HEADER
            debug_log.append(f"--- SCAN: {time.strftime('%X')} ({len(li_items)} items) ---")
            
            for i, li in enumerate(li_items):
                try:
                    text_content = await li.inner_text()
                    aria_label = await li.get_attribute("aria-label") or ""
                    data_tid = await li.get_attribute("data-tid") or ""
                    is_speaking_attr = await li.get_attribute("data-is-speaking")
                    
                    # Log entry
                    log_line = f"[{i}] Text: {text_content.splitlines()[0][:20]}... | Label: {aria_label} | SpeakingAttr: {is_speaking_attr}"
                    debug_log.append(log_line)

                    # --- DETECT SPEAKERS ---
                    
                    # Strateji 1: data-is-speaking="true"
                    if is_speaking_attr == "true":
                        name = self._clean_name(text_content, aria_label)
                        if name and name not in active_speakers:
                            active_speakers.append(name)
                            debug_log.append(f"   -> MATCH STRATEGY 1: {name}")
                        continue

                    # Strateji 2: Aria-label analizi
                    keywords = ["konuşuyor", "speaking", "unmuted", "mikrofon açık"]
                    lower_label = aria_label.lower()
                    
                    # "Muted" kelimesi geçiyorsa konuşmuyordur (Teams bazen "Muted" der)
                    if "muted" in lower_label and "unmuted" not in lower_label:
                        continue

                    if any(k in lower_label for k in ["konuşuyor", "speaking"]):
                        name = self._clean_name(text_content, aria_label)
                        if name and name not in active_speakers:
                            active_speakers.append(name)
                            debug_log.append(f"   -> MATCH STRATEGY 2 (Speaking): {name}")
                        continue

                    # Strateji 2.5: YEDEK - Unmuted kontrolü (ana yöntemler çalışmazsa)
                    # Mikrofonu açık olan kişi potansiyel konuşmacı
                    if name not in active_speakers:
                        icon_paths = await li.locator("svg path").all()
                        is_muted_icon = False
                        has_mic_icon = len(icon_paths) > 0
                        
                        for path in icon_paths:
                            d_attr = await path.get_attribute("d") or ""
                            if "15 15" in d_attr or "15-15" in d_attr:
                                is_muted_icon = True
                                break
                        
                        # Mikrofon ikonu var ama slash yok = unmuted
                        if has_mic_icon and not is_muted_icon:
                            name = self._clean_name(text_content, aria_label)
                            if name and name not in active_speakers:
                                active_speakers.append(name)
                                debug_log.append(f"   -> MATCH STRATEGY 2.5 (Unmuted): {name}")
                    # Strateji 3: data-active-speaker-id (User Suggestion)
                    # Hidden div veya parent olabilir. Li üzerinde arıyoruz.
                    active_id = await li.get_attribute("data-active-speaker-id")
                    if active_id:
                         # ID varsa konuşuyordur ama ismi ID değil text'ten alalım.
                         name = self._clean_name(text_content, aria_label)
                         if name and name not in active_speakers:
                            active_speakers.append(name)
                            debug_log.append(f"   -> MATCH STRATEGY 3 (ID): {name}")
                except Exception as e:
                    logger.error(f"Katılımcı listesi elemanı işleme hatası: {e}")
                    debug_log.append(f"CRITICAL ERROR processing list item: {str(e)}")
                    # Continue to next item, don't return here
            
            # DEBUG DOSYASINA YAZ (HER ZAMAN)
            try:
                with open("debug_speaker_detection.txt", "w", encoding="utf-8") as f:
                    f.write("\n".join(debug_log))
            except Exception as e:
                logger.error(f"Debug log yazma hatası: {e}")

            if active_speakers:
                logger.info(f"🗣️ Konuşanlar: {', '.join(active_speakers)}")
                
            return active_speakers

        except Exception as e:
            if "closed" in str(e).lower():
                return []
            logger.error(f"Katılımcı listesi okuma hatası: {e}")
            return []

    def _clean_name(self, text, label):
        """Helper to extract clean name from text or aria-label"""
        # Öncelik aria-label
        if label:
            # "Ahmet Yılmaz, Konuşuyor" -> "Ahmet Yılmaz"
            clean = label.replace("Konuşuyor", "").replace("Speaking", "").replace(",", "").strip()
            # Bazen "Katılımcı: Ahmet Yılmaz" olabilir
            clean = clean.split(":")[-1].strip()
            name = clean
        elif text:
            # Text çok satırlı olabilir: "Ahmet\nOrganizatör"
            name = text.split('\n')[0].strip()
        else:
            return ""
        
        # EXCLUDED İSİMLER: Gerçek katılımcı olmayan UI elementleri ve bot isimleri
        excluded_names = [
            "frame", "pen_spark", "pen_spark_io", "spark_io",
            "sesly bot", "sesly", "toplantı botu", "meeting bot",
            "localhost", "panel", "bot panel", "sesly asistan",
            "microsoft teams", "teams", "katılım isteği", "join request"
        ]
        
        if name:
            name_lower = name.lower().strip()
            if any(ex in name_lower for ex in excluded_names):
                logger.debug(f"[FILTER] '{name}' excluded - ger    çek katılımcı değil")
                return ""
        
        return name

    async def check_meeting_ended(self):
        """Toplantı bitti mi veya geçersiz mi kontrol eder."""
        try:
            if self.page.is_closed():
                return True

            # 1. Metin Kontrolü (Kesin Bitiş)
            end_factors = [
                "text=Meeting ended",
                "text=Toplantı bitti",
                "text=You have been removed",
                "text=Toplantıdan kaldırıldınız",
                "text=Çağrınızdan memnun musunuz?",
                "text=Teams'e bugün ücretsiz katılın",
                "text=Daha fazla bilgi edinin"
            ]
            for selector in end_factors:
                 if await self.page.locator(selector).is_visible():
                     logger.info(f"Toplantı bitiş mesajı tespit edildi: {selector}")
                     self.end_reason = "normal"
                     return True

            # 2. GEÇERSİZ/ESKİ LİNK TESPİTİ (YENİ!)
            try:
                content = (await self.page.content()).lower()
                invalid_phrases = [
                    "this meeting doesn't exist",
                    "meeting doesn't exist",
                    "this meeting has expired",
                    "meeting has expired",
                    "invalid meeting link",
                    "meeting link is no longer valid",
                    "meeting not found",
                    "unable to join this meeting",
                    "bu toplantı mevcut değil",
                    "toplantı bulunamadı",
                    "geçersiz toplantı linki",
                    "bu toplantı süresi dolmuş",
                    "toplantı bağlantısı geçersiz",
                    "this meeting id is invalid",
                    "couldn't find the meeting",
                ]
                
                for phrase in invalid_phrases:
                    if phrase in content:
                        logger.warning(f"⚠️ GEÇERSİZ TEAMS TOPLANTISI TESPİT EDİLDİ: {phrase}")
                        self.end_reason = f"Geçersiz Teams toplantısı: {phrase}"
                        return True
            except:
                pass
            

            
            # 2. "Başkalarının katılması bekleniyor" ve "Tek Kişi" Timeout
            # Bu durumlar hemen çıkış sebebi değil, 60 saniye sürerse çıkış sebebidir.
            try:
                waiting_texts = [
                    "text=Başkalarının katılması bekleniyor",
                    "text=Waiting for others to join",
                    "text=When the meeting starts, we'll let people know",
                    "text=Bu toplantıda (1)"  # Safety Net'i buraya taşıdık (Timeout'a tabi olsun)
                ]
                
                is_waiting = False
                for txt in waiting_texts:
                    if await self.page.locator(txt).is_visible():
                        is_waiting = True
                        break
                
                # Ayrıca katılımcı listesinden de kontrol (Yazı değişebilir ama sayı 1 ise)
                if not is_waiting:
                    try:
                        participant_list = self.page.locator("ul[role='list']").last
                        if await participant_list.is_visible():
                            count = await participant_list.locator("li[role='listitem']").count()
                            if count == 1:
                                is_waiting = True
                    except: pass
                
                if is_waiting:
                    if self.waiting_start_time is None:
                        self.waiting_start_time = time.time()
                        logger.info("⏳ Tek kişi/Bekleme modu tespit edildi. Sayaç başladı.")
                    else:
                        elapsed = time.time() - self.waiting_start_time
                        if elapsed > 120: # 2 Dakika kuralı (User request)
                            logger.info(f"⌛ Bekleme/Yalnızlık süresi ({elapsed:.1f}s) doldu. Toplantı bitmiş sayılıyor.")
                            return True
                else:
                    # Durum düzeldi (biri geldi), sayacı sıfırla
                    if self.waiting_start_time is not None:
                         logger.info("✅ Katılımcı geldi, bekleme sayacı sıfırlandı.")
                         self.waiting_start_time = None

            except Exception as e:
                pass
            
            # ( Eski Safety Net Bloğu kaldırıldı çünkü yukarıya entegre edildi )
                 
        except Exception as e:
            if "closed" in str(e).lower():
                return True
        return False

    async def close(self):
        """Tarayıcıyı kapatır."""
        if self.browser:
            await self.browser.close()
            logger.info("Tarayıcı kapatıldı.")
        if self.playwright:
            await self.playwright.stop()
            
