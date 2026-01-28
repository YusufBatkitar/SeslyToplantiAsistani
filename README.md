# Sesly - AI Destekli Akıllı Toplantı Asistanı

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green.svg)
![Gemini AI](https://img.shields.io/badge/Gemini-AI-orange.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)

**Zoom, Microsoft Teams ve Google Meet toplantılarını otomatik olarak kaydeden,  
yapay zeka ile yazıya döken ve akıllı raporlar oluşturan kapsamlı bir toplantı asistanı.**

[Kurulum](#kurulum) · [Kullanim](#kullanim) · [API](#api-referansi) · [SSS](#sik-sorulan-sorular)

</div>

---

## Proje Hakkinda

### Bu Proje Nedir?

**Sesly**, online toplantılarınıza katılan, toplantıyı baştan sona kaydeden, toplantı sonunda transkript oluşturan ve size detaylı bir rapor sunan **yapay zeka destekli bir toplantı asistanıdır**.

Sistem şu işlemleri otomatik olarak gerçekleştirir:
- Toplantıya "Sesly Bot" İsmiyle giriş yapar
- Tüm konuşmaları kaydeder
- Konuşmaları yazıya döker (kimin ne söylediğini ayırt ederek)
- Toplantı bitince özet, kararlar,aksiyon maddelerini ve hangi katılımcının topltnıya ne kadar katkı sağladığını içeren bir rapor oluşturur

---

### Neden Bu Proje Gelistirildi?

Modern iş hayatında karşılaşılan gerçek problemlere çözüm sunmak için geliştirilmiştir:

| Problem | Sesly Çözümü |
|---------|--------------|
| Toplantı sırasında not almaktan konuşmaları kaçırma | Otomatik kayıt ve transkript - sadece toplantıya odaklanın |
| Toplantıya katılamama durumunda bilgi eksikliği | Detaylı rapor ve tam transkript ile hiçbir şey kaçmaz |
| "Kim ne dedi?" tartışmaları | Konuşmacı ayrımlı kayıt ile net dokümantasyon |
| Toplantı sonrası aksiyon maddelerinin unutulması | AI destekli rapor ile tüm kararlar ve görevler listelenir |
| Uzun toplantıları tekrar dinlemenin zaman kaybı | Toplantı özeti ile 1 saatlik toplantı 2 dakikada öğrenilir |

---

### Temel Ozellikler

#### 1. Otomatik Toplantiya Katilim
Sadece toplantı linkini yapıştırın, bot sizin yerinize toplantıya katılır. Desteklenen platformlar:
- **Zoom** (Web üzerinden)
- **Microsoft Teams** (Web üzerinden)  
- **Google Meet** (Web üzerinden)

#### 2. Gercek Zamanli Ses Kaydi
- Toplantı sesini yüksek kalitede kaydeder
- VB-Cable sanal ses kartı ile net kayıt
- FFmpeg ile profesyonel ses işleme
- Ses kayıtları tutulmaz. Rapor oluşturulduktan sonra silinir.

#### 3. Yapay Zeka ile Transkripsiyon
- **Google Gemini AI** ile Türkçe konuşma tanıma
- **Konuşmacı Ayrımı (Diarization)**: Kim ne zaman konuştu?
- Gerçek zamanlı yazıya dökme

#### 4. Akilli Rapor Olusturma
Toplantı bitince otomatik olarak oluşturulan raporlar:
- **Toplantı Özeti**: Ana konuların kısa özeti
- **Kararlar**: Toplantıda alınan kararlar
- **Aksiyon Maddeleri**: Kimin ne yapması gerektiği
- **Katılım Analizi**: Kim ne kadar konuştu

#### 5. Modern Web Arayuzu
- Mobil uyumlu responsive tasarım
- Takvim görünümü ile toplantı geçmişi
- Toplantı arama ve filtreleme
- Kullanıcı profil yönetimi

---

## Nasil Calisir?

```
+-------------------------------------------------------------+
|  1. KULLANICI                                               |
|     Toplantı linkini web arayüzüne yapıştırır               |
|     -> "Bot Gönder" butonuna tıklar                         |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|  2. BOT                                                     |
|     Otomatik olarak toplantıya katılır                      |
|     -> Zoom / Teams / Meet fark etmez                       |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|  3. KAYIT                                                   |
|     Toplantı boyunca ses kaydı yapılır                      |
|     -> VB-Cable + FFmpeg ile yüksek kalite                  |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|  4. TRANSKRIPSIYON                                          |
|     Konuşmalar yazıya dökülür                               |
|     -> Google Gemini AI ile Türkçe tanıma                   |
|     -> Kimin konuştuğu ayrıştırılır                         |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|  5. RAPOR                                                   |
|     Otomatik rapor oluşturulur                              |
|     -> Özet, kararlar, aksiyon maddeleri                    |
|     -> Supabase'e kaydedilir                                |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|  6. SONUC                                                   |
|     Kullanıcı web arayüzünden görüntüler                    |
|     -> Transkript, rapor, özet hepsi hazır                  |
+-------------------------------------------------------------+
```

---

## Kullanilan Teknolojiler

| Kategori | Teknoloji | Kullanim Amaci |
|----------|-----------|----------------|
| **Backend** | Python 3.10+, FastAPI | Ana sunucu ve API |
| **AI/ML** | Google Gemini AI | Konuşma tanıma ve özet oluşturma |
| **Veritabani** | Supabase (PostgreSQL) | Veri depolama ve kullanıcı yönetimi |
| **Web Otomasyon** | Selenium, Playwright | Toplantılara otomatik katılım |
| **Ses Isleme** | FFmpeg, VB-Cable | Ses kaydı ve dönüştürme |
| **Frontend** | HTML, CSS, JavaScript | Modern web arayüzü |

---

## Sistem Mimarisi

```
+---------------------------------------------------------------------+
|                        WEB ARAYUZU                                  |
|  (index.html, meetings.html, calendar.html, login.html)             |
+---------------------------------------------------------------------+
                              |
                              v
+---------------------------------------------------------------------+
|                     FASTAPI SERVER (server.py)                      |
|  +--------------+  +---------------+  +----------------------+      |
|  | /start-bot   |  | /transcribe   |  | /api/meetings        |      |
|  | /stop-bot    |  | /summarize    |  | /view-report         |      |
|  | /bot-status  |  | /get-summary  |  | /view-transcript     |      |
|  +--------------+  +---------------+  +----------------------+      |
+---------------------------------------------------------------------+
                              |
          +-------------------+-------------------+
          v                   v                   v
+-----------------+  +-----------------+  +-----------------+
|   ZOOM WORKER   |  |  TEAMS WORKER   |  |   MEET WORKER   |
| zoom_web_worker |  |teams_web_worker |  |   meet_worker   |
| zoom_web_client |  |teams_web_client |  | meet_web_client |
+-----------------+  +-----------------+  +-----------------+
          |                   |                   |
          +-------------------+-------------------+
                              v
+---------------------------------------------------------------------+
|                    ORTAK KATMANLAR                                  |
|  +----------------------+  +--------------+  +--------------+       |
|  | zoom_bot_recorder.py |  | rapor.py     |  | db_utils.py  |       |
|  | (Ses Kaydi)          |  | (AI Rapor)   |  | (Supabase)   |       |
|  +----------------------+  +--------------+  +--------------+       |
+---------------------------------------------------------------------+
                              |
                              v
+---------------------------------------------------------------------+
|                    DIS SERVISLER                                    |
|  +--------------+  +--------------+  +----------------------+       |
|  | Supabase     |  | Gemini AI    |  | FFmpeg               |       |
|  | (Veritabani) |  | (Transkript) |  | (Ses Isleme)         |       |
|  +--------------+  +--------------+  +----------------------+       |
+---------------------------------------------------------------------+
```

---

## Gereksinimler


### Yazilim Gereksinimleri

| Yazilim | Versiyon | Aciklama |
|---------|----------|----------|
| Python | 3.10+ | Ana programlama dili |
| FFmpeg | Latest | Ses/video işleme |
| VB-Cable | Latest | Sanal ses kartı |
| Chrome/Chromium | Latest | Web otomasyon |

---

## Kurulum

### 1. Depoyu Klonlayin
```bash
git clone https://github.com/kullaniciadi/SeslyToplantiAsistani.git
cd SeslyToplantiAsistani
```

### 2. Sanal Ortam Olusturun
```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Bagimliliklari Yukleyin
```bash
pip install -r requirements.txt
```

### 4. Playwright Tarayicilarini Kurun
```bash
playwright install chromium
```

### 5. FFmpeg Kurulumu
1. [FFmpeg](https://ffmpeg.org/download.html) indirin
2. `bin` klasörünü PATH'e ekleyin

### 6. VB-Cable Kurulumu
1. [VB-Cable](https://vb-audio.com/Cable/) indirin
2. Sürücüyü yönetici olarak kurun

### 7. Yapilandirma
Proje kök dizininde `.env` dosyası oluşturun:

```env
# SUPABASE AYARLARI
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# GEMINI AI AYARLARI
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-2.5-flash

# SUNUCU AYARLARI
PORT=8000
HOST=127.0.0.1
```

---

## Kullanim

### Sunucuyu Baslatma
```bash
python server.py
python sistem.py
```

Sunucu `http://127.0.0.1:8000` adresinde başlayacaktır.

### Adim Adim Kullanim

1. **Giris Yapin**: Tarayıcıda `http://127.0.0.1:8000` adresine gidin
2. **Toplanti Linki Girin**: Ana sayfada toplantı bağlantısını yapıştırın
3. **Bot Gonderin**: "Bot Gönder" butonuna tıklayın
4. **Bekleyin**: Bot toplantıya katılır ve kayıt yapar
5. **Rapor Alin**: Toplantı bitince otomatik rapor oluşturulur

### Desteklenen Link Formatlari

**Zoom:**
```
https://zoom.us/j/1234567890?pwd=abc123
```

**Microsoft Teams:**
```
https://teams.microsoft.com/l/meetup-join/...
```

**Google Meet:**
```
https://meet.google.com/abc-defg-hij
```

---

## API Referansi

### Bot Kontrolu

#### POST `/start-bot`
Toplantıya bot gönderir.

```json
{
    "meeting_link": "https://zoom.us/j/123456789",
    "platform": "zoom",
    "user_id": "uuid-string",
    "title": "Proje Toplantisi"
}
```

#### POST `/stop-bot`
Çalışan botu durdurur.

#### GET `/bot-status`
Bot durumunu döndürür.

### Veri Yonetimi

#### GET `/api/meetings`
Kullanıcının tüm toplantılarını listeler.

#### GET `/view-report?url=...`
Toplantı raporunu görüntüler.

#### GET `/view-transcript?url=...`
Toplantı transkriptini görüntüler.

---

## Proje Yapisi

```
SeslyToplantiAsistani/
|
+-- server.py              # Ana FastAPI sunucusu
+-- rapor.py               # AI rapor olusturma
+-- db_utils.py            # Supabase yardımcı fonksiyonlari
+-- sistem.py              # Sistem yardımcı fonksiyonlari
|
+-- zoom_web_worker.py     # Zoom bot yoneticisi
+-- zoom_web_client.py     # Zoom tarayici otomasyonu
+-- zoom_bot_recorder.py   # Ses kaydi modulu
+-- teams_web_worker.py    # Teams bot yoneticisi
+-- teams_web_client.py    # Teams tarayici otomasyonu
+-- meet_worker.py         # Meet bot yoneticisi
+-- meet_web_client.py     # Meet tarayici otomasyonu
|
+-- web_arayuz/            # Frontend dosyalari
|   +-- index.html         # Ana sayfa
|   +-- meetings.html      # Toplanti listesi
|   +-- calendar.html      # Takvim gorunumu
|   +-- login.html         # Giris sayfasi
|
+-- data/                  # Calisma zamani verileri
+-- logs/                  # Log dosyalari
+-- temp_reports/          # Gecici rapor dosyalari
|
+-- requirements.txt       # Python bagimliliklari
+-- .env                   # Ortam degiskenleri (olusturmaniz gerekir)
+-- README.md              # Bu dosya
```

---

## Sik Sorulan Sorular

### Hangi platformlari destekliyor?
Zoom, Microsoft Teams ve Google Meet desteklenmektedir.

### Toplanti ne kadar uzun olabilir?
Segment bazlı işleme sayesinde süre sınırı yoktur.

### Verilerim guvende mi?
- Tüm veriler Supabase'de şifreli olarak saklanır
- API anahtarları `.env` dosyasında tutulur ve GitHub'a yüklenmez
- Row Level Security (RLS) ile veri güvenliği sağlanır

---

## Sorun Giderme

| Sorun | Cozum |
|-------|-------|
| Bot toplantiya katilamiyor | İnternet bağlantısını ve toplantı linkini kontrol edin |
| Ses kaydedilmiyor | VB-Cable kurulumunu ve ses ayarlarını kontrol edin |
| Transkript olusmuyor | Gemini API anahtarının geçerli olduğundan emin olun |
| Rapor kaydedilmiyor | Supabase bağlantı bilgilerini kontrol edin |

---

## Performans

- **Ortalama Transkripsiyon Suresi**: ~20 saniye / 1 saat ses
- **Ortalama Rapor Olusturma**: ~15 saniye

---

<div align="center">

</div>
