import os
import mimetypes
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

# .env yükle
load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
# Backend işlemleri için Service Role Key tercih edilir (RLS bypass)
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

def init_supabase() -> Client:
    """Supabase istemcisini başlatır"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[ERROR] SUPABASE_URL veya SUPABASE_KEY eksik! .env dosyasını kontrol edin.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"[ERROR] Supabase bağlantı hatası: {e}")
        return None

def upload_file(bucket_name: str, file_path: str, destination_path: str = None) -> str:
    """
    Dosyayı belirtilen bucket'a yükler ve public URL döndürür.
    
    Args:
        bucket_name (str): 'transcripts' veya 'reports'
        file_path (str): Yüklenecek dosyanın yerel yolu
        destination_path (str): Storage içindeki hedef yol (opsiyonel, boşsa dosya adı kullanılır)
        
    Returns:
        str: Public URL veya None
    """
    client = init_supabase()
    if not client:
        return None

    path_obj = Path(file_path)
    if not path_obj.exists():
        print(f"[ERROR] Dosya bulunamadı: {file_path}")
        return None

    if not destination_path:
        destination_path = path_obj.name

    # Content Type belirle
    ext = path_obj.suffix.lower()
    if ext == ".html":
        content_type = "text/html; charset=utf-8"
    elif ext == ".txt":
        content_type = "text/plain; charset=utf-8"
    elif ext == ".json":
        content_type = "application/json; charset=utf-8"
    elif ext == ".pdf":
        content_type = "application/pdf"
    else:
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            content_type = "application/octet-stream"

    try:
        with open(file_path, 'rb') as f:
            print(f"[UPLOAD] {path_obj.name} -> {bucket_name}/{destination_path} yükleniyor...")
            response = client.storage.from_(bucket_name).upload(
                file=f,
                path=destination_path,
                file_options={"content-type": content_type, "upsert": "true"}
            )
        
        # Public URL al
        public_url = client.storage.from_(bucket_name).get_public_url(destination_path)
        print(f"[SUCCESS] Yüklendi: {public_url}")
        return public_url

    except Exception as e:
        print(f"[ERROR] Dosya yükleme hatası: {e}")
        return None

def save_meeting_record(user_id: str, title: str, platform: str, start_time: str, duration: str, 
                        transcript_url: str = None, report_url: str = None, summary_text: str = None) -> bool:
    """
    Toplantı kaydını 'meetings' tablosuna ekler.
    
    Args:
        user_id (str): Kullanıcının UUID'si (Auth'dan gelmeli)
        title (str): Toplantı başlığı
        ...
    """
    client = init_supabase()
    if not client:
        return False

    data = {
        "user_id": user_id,
        "title": title,
        "platform": platform,
        "start_time": start_time, # ISO format string
        "duration": duration,
        "status": "completed",
        "summary_text": summary_text,
        "transcript_path": transcript_url,
        "report_path": report_url
    }

    try:
        response = client.table("meetings").insert(data).execute()
        print(f"[DB] Toplantı kaydı eklendi: {title}")
        return True
    except Exception as e:
        print(f"[ERROR] DB kayıt hatası: {e}")
        return False

def delete_user_account(user_id: str) -> bool:
    """
    Kullanıcıyı sistemden (Auth ve DB) tamamen siler.
    Dikkat: Bu işlem için SERVICE_ROLE anahtarı gerekebilir.
    """
    client = init_supabase()
    if not client:
        return False

    try:
        # 1. Auth'dan sil (Cascade ile profiles ve meetings de silinmeli)
        print(f"[ADMIN] Kullanıcı siliniyor: {user_id}")
        response = client.auth.admin.delete_user(user_id)
        print("[SUCCESS] Kullanıcı silindi.")
        return True
    except Exception as e:
        print(f"[ERROR] Hesap silme hatası: {e}")
        return False
