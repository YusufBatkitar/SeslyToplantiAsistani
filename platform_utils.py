"""
Platform Abstraction Layer
==========================
Windows ve Linux arasında platform-bağımsız çalışmayı sağlar.
"""

import platform
import os
import shutil

# Platform Detection
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"

# ============================================================
# AUDIO DEVICE ABSTRACTION
# ============================================================

def get_audio_device() -> str:
    """
    Platformdan bağımsız ses cihazı adı döndürür.
    Windows: VB-Cable
    Linux: PulseAudio virtual sink
    """
    if IS_WINDOWS:
        return "CABLE Output (VB-Audio Virtual Cable)"
    elif IS_LINUX:
        return "pulse"  # PulseAudio default
    else:
        return "default"

def get_audio_device_for_ffmpeg() -> list:
    """
    FFmpeg için platform-specific audio input argümanları döndürür.
    Returns: ["-f", "device_type", "-i", "device_name"]
    """
    if IS_WINDOWS:
        return ["-f", "dshow", "-i", "audio=CABLE Output (VB-Audio Virtual Cable)"]
    elif IS_LINUX:
        # PulseAudio loopback - docker-entrypoint.sh'da oluşturuluyor
        return ["-f", "pulse", "-i", "virtual_mic.monitor"]
    else:
        return ["-f", "avfoundation", "-i", ":0"]  # macOS

# ============================================================
# FFMPEG PATH
# ============================================================

def get_ffmpeg_path() -> str:
    """FFmpeg yolunu döndürür."""
    # Önce env'den bak
    env_path = os.getenv("FFMPEG_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    
    # PATH'te ara
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    
    # Windows hardcoded fallback
    if IS_WINDOWS:
        hardcoded = r"C:\Users\user\Desktop\ffmpeg-2025-10-19-git-dc39a576ad-full_build\bin\ffmpeg.exe"
        if os.path.exists(hardcoded):
            return hardcoded
    
    return "ffmpeg"  # Son çare

# ============================================================
# WINDOW MANAGEMENT (No-op on Linux)
# ============================================================

def bring_window_to_front(title_keywords: list = None):
    """
    Pencereyi öne getirir.
    Windows: win32gui API kullanır
    Linux: No-op (headless modda gerek yok)
    """
    if not IS_WINDOWS:
        return  # Linux'ta gerek yok
    
    try:
        import win32gui
        import win32con
        import win32process
        import psutil
        
        if title_keywords is None:
            title_keywords = ["Zoom", "Meet", "Teams"]
        
        def enum_callback(hwnd, results):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return True
            
            title_lower = title.lower()
            if any(kw.lower() in title_lower for kw in title_keywords):
                results.append(hwnd)
            return True
        
        hwnds = []
        win32gui.EnumWindows(enum_callback, hwnds)
        
        if hwnds:
            hwnd = hwnds[0]
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            win32gui.SetForegroundWindow(hwnd)
            
    except ImportError:
        pass  # pywin32 yüklü değil
    except Exception as e:
        print(f"[PLATFORM] Window focus error: {e}")

def minimize_window(title_keywords: list = None):
    """
    Belirli pencereyi minimize eder.
    Linux: No-op
    """
    if not IS_WINDOWS:
        return
    
    try:
        import win32gui
        import win32con
        
        if title_keywords is None:
            return
        
        def enum_callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            
            title = win32gui.GetWindowText(hwnd)
            if any(kw.lower() in title.lower() for kw in title_keywords):
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return True
        
        win32gui.EnumWindows(enum_callback, None)
        
    except ImportError:
        pass
    except Exception as e:
        print(f"[PLATFORM] Minimize error: {e}")

# ============================================================
# BROWSER OPTIONS
# ============================================================

def get_chrome_options_for_platform(options):
    """
    Platform'a göre Chrome options ekler.
    Linux'ta headless ve sandbox ayarları.
    """
    if IS_LINUX:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
    
    # Her platformda gerekli
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--disable-notifications")
    options.add_argument("--autoplay-policy=no-user-gesture-required")
    
    return options

def get_playwright_browser_args() -> list:
    """Playwright için browser argümanları."""
    args = [
        "--use-fake-ui-for-media-stream",
        "--disable-notifications",
        "--autoplay-policy=no-user-gesture-required",
    ]
    
    if IS_LINUX:
        args.extend([
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ])
    
    return args

# ============================================================
# DISPLAY ENVIRONMENT
# ============================================================

def setup_display():
    """
    Linux'ta DISPLAY environment variable'ı ayarlar.
    Xvfb kullanılıyorsa :99'a set eder.
    """
    if IS_LINUX and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"

# ============================================================
# UTILITY
# ============================================================

def log_platform_info():
    """Platform bilgisini loglar."""
    print(f"[PLATFORM] OS: {platform.system()} {platform.release()}")
    print(f"[PLATFORM] Python: {platform.python_version()}")
    print(f"[PLATFORM] FFmpeg: {get_ffmpeg_path()}")
    print(f"[PLATFORM] Audio Device: {get_audio_device()}")
    if IS_LINUX:
        print(f"[PLATFORM] DISPLAY: {os.environ.get('DISPLAY', 'NOT SET')}")
