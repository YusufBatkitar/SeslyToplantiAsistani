# 🚀 Sesly Bot - Linux Docker Deployment Rehberi

## 📊 Kaynak Gereksinimleri

| Worker Sayısı | RAM | CPU | Önerilen VPS | Fiyat |
|---------------|-----|-----|--------------|-------|
| 3 worker | 4 GB | 2 vCPU | Hetzner CX21 | ~€4/ay |
| **5 worker** | **8 GB** | **4 vCPU** | **Hetzner CX31** | **~€8/ay** |
| 10 worker | 16 GB | 6 vCPU | Hetzner CX41 | ~€15/ay |

> Her worker ~1-1.5 GB RAM kullanır (Chromium + FFmpeg + PulseAudio)

---

## 🛠️ VPS Kurulum Adımları

### 1. VPS Satın Al
- **Hetzner**: https://www.hetzner.com/cloud
- **Contabo**: https://contabo.com
- **DigitalOcean**: https://www.digitalocean.com

Ubuntu 22.04 LTS seçin.

### 2. Docker Kurulumu (SSH ile bağlandıktan sonra)

```bash
# Sistem güncelle
sudo apt update && sudo apt upgrade -y

# Docker kur
curl -fsSL https://get.docker.com | sh

# Docker Compose kur
sudo apt install docker-compose-plugin -y

# Kullanıcıyı docker grubuna ekle
sudo usermod -aG docker $USER
newgrp docker
```

### 3. Proje Dosyalarını Yükle

```bash
# Proje klasörü oluştur
mkdir -p ~/sesly-bot
cd ~/sesly-bot

# Dosyaları SCP ile yükle (Windows'tan)
# PowerShell'de:
# scp -r C:\Users\user\Desktop\SeslyToplantiAsistani\* root@VPS_IP:~/sesly-bot/
```

### 4. Environment Değişkenleri

```bash
# .env dosyası oluştur
cp .env.example .env
nano .env
```

`.env` içeriği:
```env
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJhbGci...
GEMINI_API_KEY=AIza...
REDIS_URL=redis://redis:6379/0
```

### 5. Docker Build & Run

```bash
# İmajları build et
docker compose build

# Servisleri başlat
docker compose up -d

# Logları izle
docker compose logs -f worker
```

---

## 📈 Ölçeklendirme

### Worker Sayısını Artırma

```bash
# 10 worker'a çıkar
docker compose up -d --scale worker=10
```

Veya `docker-compose.yml`'de:
```yaml
worker:
  deploy:
    replicas: 10
```

### Mevcut Durumu Kontrol

```bash
# Çalışan container'lar
docker compose ps

# Kaynak kullanımı
docker stats
```

---

## 🔧 Sorun Giderme

### Loglar
```bash
# Tüm loglar
docker compose logs

# Sadece worker logları
docker compose logs -f worker

# Sadece son 100 satır
docker compose logs --tail=100 worker
```

### Yeniden Başlatma
```bash
docker compose restart worker
```

### Tamamen Sıfırlama
```bash
docker compose down
docker compose up -d --build
```

---

## 📁 Dosya Yapısı

```
sesly-bot/
├── .env                    # Gizli anahtarlar
├── .env.example            # Örnek env dosyası
├── Dockerfile              # Container image
├── docker-compose.yml      # Multi-container config
├── docker-entrypoint.sh    # Xvfb + PulseAudio init
├── requirements-linux.txt  # Python dependencies
├── tasks.py                # Celery tasks
├── platform_utils.py       # Cross-platform helper
├── zoom_web_client.py      # Zoom bot
├── meet_web_client.py      # Meet bot
├── teams_web_client.py     # Teams bot
└── ...
```

---

## 🔄 Kuyruk Sistemi Akışı

```
[Web Arayüzü] → [Supabase task_queue] → [Redis] → [Celery Workers]
                                                      ↓
                                    ┌─────┬─────┬─────┬─────┬─────┐
                                    │ W1  │ W2  │ W3  │ W4  │ W5  │
                                    └─────┴─────┴─────┴─────┴─────┘
                                    Her worker bir toplantıya katılır
```

---

## 💡 İpuçları

1. **VPS Seçimi**: Avrupa'ya yakın VPS seçin (gecikme düşük olur)
2. **Yedekleme**: `.env` dosyasını güvenli yerde saklayın
3. **Monitoring**: `docker stats` ile kaynak kullanımını izleyin
4. **Güncelleme**: Kod güncellemesi için `docker compose up -d --build`
