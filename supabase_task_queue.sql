-- ============================================================
-- SESLY BOT - TASK QUEUE TABLOSU
-- ============================================================
-- Bu SQL'i Supabase SQL Editor'da çalıştırın

-- Task Queue Tablosu
CREATE TABLE IF NOT EXISTS task_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    meeting_url TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('zoom', 'meet', 'teams')),
    title TEXT,
    bot_name TEXT DEFAULT 'Sesly Bot',
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')),
    error_message TEXT,
    priority INT DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    
    -- Results
    transcript_url TEXT,
    report_url TEXT,
    duration_seconds INT,
    
    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Index for queue processing (pending tasks first, by priority and creation time)
CREATE INDEX IF NOT EXISTS idx_task_queue_pending 
ON task_queue (status, priority DESC, created_at ASC) 
WHERE status = 'pending';

-- Index for user's tasks
CREATE INDEX IF NOT EXISTS idx_task_queue_user 
ON task_queue (user_id, created_at DESC);

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================

ALTER TABLE task_queue ENABLE ROW LEVEL SECURITY;

-- Kullanıcılar kendi task'larını görebilir
CREATE POLICY "Users can view own tasks" 
ON task_queue FOR SELECT 
USING (auth.uid() = user_id);

-- Kullanıcılar kendi task'larını ekleyebilir
CREATE POLICY "Users can insert own tasks" 
ON task_queue FOR INSERT 
WITH CHECK (auth.uid() = user_id);

-- Kullanıcılar pending durumundaki task'larını iptal edebilir
CREATE POLICY "Users can cancel pending tasks" 
ON task_queue FOR UPDATE 
USING (auth.uid() = user_id AND status = 'pending')
WITH CHECK (status = 'cancelled');

-- Service role tüm işlemleri yapabilir (backend için)
CREATE POLICY "Service role full access" 
ON task_queue FOR ALL 
USING (auth.jwt() ->> 'role' = 'service_role');

-- ============================================================
-- HELPER FUNCTION: Get Queue Position
-- ============================================================

CREATE OR REPLACE FUNCTION get_queue_position(task_id UUID)
RETURNS INT AS $$
DECLARE
    position INT;
BEGIN
    SELECT COUNT(*) + 1 INTO position
    FROM task_queue
    WHERE status = 'pending'
    AND created_at < (SELECT created_at FROM task_queue WHERE id = task_id);
    
    RETURN position;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
