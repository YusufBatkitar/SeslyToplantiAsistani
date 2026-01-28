

-- Kodları çalıştırmak için aşağıdaki adımları izleyin:
--
-- SQL KODLARINI ÇALIŞTIRMA
--   Sol menüden "SQL Editor" seçin
--   Bu dosyadaki kodları SIRASIYLA kopyalayıp yapıştırın:
--      a) Önce "1. TABLOLAR" bölümünü çalıştırın
--      b) Sonra "2. STORAGE BUCKET'LAR" bölümünü çalıştırın
--      c) Sonra "3. RLS POLİCY'LERİ" bölümünü çalıştırın
--      d) Son olarak "4. STORAGE POLİCY'LERİ" bölümünü çalıştırın
--    Her bölümü ayrı ayrı "Run" butonuyla çalıştırın
--

--    - "Table Editor" bölümünden tablolarınızı görüntüleyebilirsiniz
--    - "Database" > "Policies" bölümünden policy'leri kontrol edebilirsiniz

-- UUID extension'ı etkinleştir (Supabase'de varsayılan olarak aktif)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 1. TABLOLAR
-- ============================================

-- Kullanıcı Profilleri Tablosu
CREATE TABLE public.profiles (
  id UUID NOT NULL PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT,
  full_name TEXT,
  role TEXT DEFAULT 'user',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Toplantılar Tablosu
CREATE TABLE public.meetings (
  id UUID NOT NULL DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  title TEXT,
  platform TEXT,
  meeting_url TEXT,
  status TEXT DEFAULT 'pending',
  start_time TIMESTAMPTZ,
  duration TEXT,
  transcript_path TEXT,
  report_path TEXT,
  summary_text TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================
-- 2. STORAGE BUCKET'LAR
-- ============================================

-- Raporlar için bucket
INSERT INTO storage.buckets (id, name, public) 
VALUES ('reports', 'reports', true);

-- Transkriptler için bucket
INSERT INTO storage.buckets (id, name, public) 
VALUES ('transcripts', 'transcripts', true);

-- ============================================
-- 3. ROW LEVEL SECURITY (RLS) POLİCY'LERİ
-- ============================================

-- Profiles tablosu için RLS
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Herkes profilleri görüntüleyebilir (public)
CREATE POLICY "Public profiles are viewable by everyone" 
ON public.profiles FOR SELECT 
USING (true);

-- Kullanıcılar kendi profillerini oluşturabilir
CREATE POLICY "Users can insert their own profile" 
ON public.profiles FOR INSERT 
WITH CHECK (auth.uid() = id);

-- Kullanıcılar kendi profillerini güncelleyebilir
CREATE POLICY "Users can update own profile" 
ON public.profiles FOR UPDATE 
USING (auth.uid() = id);

-- Meetings tablosu için RLS
ALTER TABLE public.meetings ENABLE ROW LEVEL SECURITY;

-- Kullanıcılar kendi toplantılarını görebilir
CREATE POLICY "Users can view own meetings" 
ON public.meetings FOR SELECT 
USING (auth.uid() = user_id);

-- Kullanıcılar kendi toplantılarını görebilir (alternatif)
CREATE POLICY "Users can view their own meetings" 
ON public.meetings FOR SELECT 
USING (auth.uid() = user_id);

-- Kullanıcılar toplantı oluşturabilir
CREATE POLICY "Users can insert own meetings" 
ON public.meetings FOR INSERT 
WITH CHECK (auth.uid() = user_id);

-- Kullanıcılar kendi toplantılarını güncelleyebilir
CREATE POLICY "Users can update own meetings" 
ON public.meetings FOR UPDATE 
USING (auth.uid() = user_id);

-- Kullanıcılar kendi toplantılarını silebilir
CREATE POLICY "Users can delete own meetings" 
ON public.meetings FOR DELETE 
USING (auth.uid() = user_id);

-- ============================================
-- 4. STORAGE POLİCY'LERİ
-- ============================================

-- Reports bucket - herkes okuyabilir (public)
CREATE POLICY "Public Access to Reports"
ON storage.objects FOR SELECT
USING (bucket_id = 'reports');

-- Reports bucket - giriş yapmış kullanıcılar yükleyebilir
CREATE POLICY "Authenticated users can upload reports"
ON storage.objects FOR INSERT
WITH CHECK (bucket_id = 'reports' AND auth.role() = 'authenticated');

-- Transcripts bucket - herkes okuyabilir (public)
CREATE POLICY "Public Access to Transcripts"
ON storage.objects FOR SELECT
USING (bucket_id = 'transcripts');

-- Transcripts bucket - giriş yapmış kullanıcılar yükleyebilir
CREATE POLICY "Authenticated users can upload transcripts"
ON storage.objects FOR INSERT
WITH CHECK (bucket_id = 'transcripts' AND auth.role() = 'authenticated');

-- ============================================
-- NOTLAR:
-- - Bu kodlar Supabase SQL Editor'da çalıştırılabilir
-- - auth.users tablosu Supabase tarafından otomatik oluşturulur
-- - Storage bucket'lar Supabase Dashboard'dan da oluşturulabilir
-- ============================================
