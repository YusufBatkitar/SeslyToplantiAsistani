"""
Celery Task Definitions
========================
Redis-backed task queue for parallel meeting bot execution.
"""

import os
import sys
import asyncio
import shutil
import json
from pathlib import Path
from datetime import datetime

# Celery fork işlemi için Python path'e /app ekle
if '/app' not in sys.path:
    sys.path.insert(0, '/app')

from celery import Celery
from celery.exceptions import MaxRetriesExceededError

# Redis connection
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Celery app
app = Celery('sesly_tasks', broker=REDIS_URL, backend=REDIS_URL)

# Celery configuration
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Istanbul',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=7200,  # 2 saat max (uzun toplantılar için)
    task_soft_time_limit=6600,  # 1 saat 50 dk soft limit
    worker_prefetch_multiplier=1,  # Her worker tek task alsın
    task_acks_late=True,  # Task bitince ACK
)

# ============================================================
# SUPABASE HELPERS
# ============================================================

def get_supabase_client():
    """Supabase client oluştur"""
    from supabase import create_client
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')
    if not url or not key:
        raise ValueError("Supabase credentials missing!")
    return create_client(url, key)

def update_task_status(task_id: str, status: str, error: str = None):
    """Task status güncelle"""
    try:
        client = get_supabase_client()
        update_data = {"status": status}
        
        if status == "processing":
            update_data["started_at"] = datetime.utcnow().isoformat()
        elif status in ("completed", "failed"):
            update_data["completed_at"] = datetime.utcnow().isoformat()
        
        if error:
            update_data["error_message"] = error[:500]  # Max 500 char
        
        client.table("task_queue").update(update_data).eq("id", task_id).execute()
        print(f"[TASK] {task_id} -> {status}")
    except Exception as e:
        print(f"[ERROR] Status update failed: {e}")

# ============================================================
# CELERY TASKS
# ============================================================

@app.task(bind=True, max_retries=2, default_retry_delay=60)
def process_meeting(self, task_id: str, meeting_url: str, platform: str, user_id: str):
    """
    Toplantıya katıl, kaydet, transkripsiyon yap.
    Her worker bağımsız olarak bu task'ı çalıştırır.
    
    Args:
        task_id: Supabase task_queue ID
        meeting_url: Toplantı linki
        platform: 'zoom', 'meet', 'teams'
        user_id: Kullanıcı UUID
    """
    print(f"\n{'='*60}")
    print(f"[WORKER] Task başladı: {task_id}")
    print(f"[WORKER] Platform: {platform}")
    print(f"[WORKER] URL: {meeting_url}")
    print(f"{'='*60}\n")
    
    try:
        # 1. Status güncelle
        update_task_status(task_id, 'processing')
        
        # 2. Platform'a göre bot çalıştır
        if platform == 'zoom':
            result = run_zoom_task(meeting_url, task_id)
        elif platform == 'meet':
            result = run_meet_task(meeting_url, task_id)
        elif platform == 'teams':
            result = run_teams_task(meeting_url, task_id)
        else:
            raise ValueError(f"Bilinmeyen platform: {platform}")
        
        # 3. Tamamlandı
        update_task_status(task_id, 'completed')
        
        # 4. Dashboard'u sıfırla
        _reset_bot_task()
        
        print(f"\n[SUCCESS] Task tamamlandı: {task_id}\n")
        return {"success": True, "task_id": task_id}
        
    except Exception as e:
        error_msg = str(e)
        print(f"\n[ERROR] Task hatası: {error_msg}\n")
        
        # Retry mantığı
        try:
            raise self.retry(exc=e, countdown=60)
        except MaxRetriesExceededError:
            update_task_status(task_id, 'failed', error_msg)
            _reset_bot_task()
            return {"success": False, "task_id": task_id, "error": error_msg}

def _reset_bot_task():
    """Task bittikten sonra bot_task.json'ı sıfırla"""
    try:
        task_file = Path("data/bot_task.json")
        task_file.write_text('{"active": false}', encoding="utf-8")
        print("[CLEANUP] bot_task.json sıfırlandı")
    except Exception as e:
        print(f"[WARN] bot_task.json sıfırlanamadı: {e}")

# ============================================================
# PLATFORM-SPECIFIC RUNNERS
# ============================================================

def run_zoom_task(meeting_url: str, task_id: str):
    """Zoom toplantısını işle"""
    try:
        from zoom_web_worker import run_zoom_web_task
        result = asyncio.run(run_zoom_web_task(meeting_url))
        return result
    finally:
        # Geçici dosyaları temizle
        work_dir = Path(f"/tmp/workers/{task_id}")
        cleanup_work_dir(work_dir, task_id)

def run_meet_task(meeting_url: str, task_id: str):
    """Meet toplantısını işle"""
    try:
        from meet_worker import run_meet_task as meet_runner
        result = asyncio.run(meet_runner(meeting_url))
        return result
    finally:
        work_dir = Path(f"/tmp/workers/{task_id}")
        cleanup_work_dir(work_dir, task_id)

def run_teams_task(meeting_url: str, task_id: str):
    """Teams toplantısını işle"""
    try:
        from teams_web_worker import run_teams_task as teams_runner
        result = asyncio.run(teams_runner(meeting_url))
        return result
    finally:
        work_dir = Path(f"/tmp/workers/{task_id}")
        cleanup_work_dir(work_dir, task_id)

def cleanup_work_dir(work_dir: Path, task_id: str):
    """
    Rapor tamamlandıktan sonra geçici dosyaları temizle.
    Ses kayıtları, segment'ler ve diğer geçici dosyalar silinir.
    """
    try:
        if work_dir.exists():
            # Dosya listesini al (log için)
            files = list(work_dir.glob("*"))
            total_size = sum(f.stat().st_size for f in files if f.is_file())
            
            # Tüm klasörü sil
            shutil.rmtree(work_dir)
            
            print(f"[CLEANUP] Task {task_id}: {len(files)} dosya silindi ({total_size / 1024 / 1024:.2f} MB)")
    except Exception as e:
        print(f"[CLEANUP ERROR] Task {task_id}: {e}")

# ============================================================
# UTILITY TASKS
# ============================================================

@app.task
def cleanup_old_tasks():
    """Eski tamamlanmış task'ları temizle (cron job)"""
    try:
        client = get_supabase_client()
        # 7 günden eski completed/failed task'ları sil
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        
        client.table("task_queue")\
            .delete()\
            .in_("status", ["completed", "failed"])\
            .lt("completed_at", cutoff)\
            .execute()
        
        print("[CLEANUP] Old tasks removed")
    except Exception as e:
        print(f"[ERROR] Cleanup failed: {e}")

@app.task
def health_check():
    """Worker sağlık kontrolü"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "redis": "connected"
    }
