import sys
print("DEBUG: rapor.py dosyasi calismaya basladi...", flush=True)

import json
import os
import datetime
import re
import uuid
from pathlib import Path
from collections import Counter
import google.generativeai as genai
from db_utils import upload_file, save_meeting_record  # Supabase fonksiyonları

# API Key'i environment variable'dan al (güvenlik için)
# ✅ .env dosyasından yükle
from dotenv import load_dotenv
load_dotenv(override=True)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("[WARN] GEMINI_API_KEY bulunamadı! Rapor oluşturma devre dışı.")
else:
    genai.configure(api_key=API_KEY)

def raporu_html_olarak_kaydet(rapor_metni, dosya_adi, meeting_title=None):
    """
    Rapor metnini HTML formatında kaydederek Türkçe karakter sorununu çözer.
    Gemini'dan gelen HTML formatını düzenli bir HTML dosyası olarak kaydeder.
    """
    if not rapor_metni:
        print("[WARN] Rapor metni yok")
        return None
    
    # Rapor tarihi
    rapor_tarihi = datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    
    # Toplantı başlığı (varsa kullan, yoksa varsayılan)
    header_title = meeting_title if meeting_title else "PROJE TOPLANTI ANALİZ RAPORU"
    
    # Tam HTML belgesi - KURUMSAL BEYAZ TEMA
    html_content = f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Toplantı Raporu - {rapor_tarihi}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            line-height: 1.7;
            background: #f8fafc;
            min-height: 100vh;
            color: #1e293b;
            padding: 40px 20px;
        }}
        
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.06);
            overflow: hidden;
        }}
        
        .header {{
            text-align: center;
            padding: 40px 30px 35px;
            background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
            color: white;
        }}
        
        .header-date {{
            font-size: 12px;
            color: rgba(255, 255, 255, 0.8);
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 2px;
            font-weight: 500;
        }}
        
        .header h1 {{
            font-size: 26px;
            font-weight: 700;
            color: white;
            margin: 0;
            letter-spacing: -0.5px;
        }}
        
        .content {{
            padding: 40px;
        }}
        
        h1 {{
            font-size: 24px;
            font-weight: 700;
            color: #1e293b;
            margin: 0 0 25px 0;
        }}
        
        h2 {{
            font-size: 16px;
            font-weight: 600;
            color: #1e293b;
            padding: 12px 16px;
            margin: 32px 0 18px 0;
            background: linear-gradient(90deg, #f1f5f9, #fff);
            border-left: 4px solid #4f46e5;
            border-radius: 0 8px 8px 0;
        }}
        
        h2:first-child {{ margin-top: 0; }}
        
        h3 {{
            font-size: 14px;
            font-weight: 600;
            color: #475569;
            margin: 20px 0 10px 0;
            padding-bottom: 6px;
            border-bottom: 1px solid #e2e8f0;
        }}
        
        p {{
            font-size: 14px;
            color: #475569;
            margin-bottom: 16px;
            line-height: 1.8;
        }}
        
        ul, ol {{
            margin: 12px 0 16px 24px;
            color: #475569;
        }}
        
        li {{
            margin-bottom: 8px;
            font-size: 14px;
            line-height: 1.7;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            font-size: 14px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            overflow: hidden;
        }}
        
        th {{
            background: #1e3a5f !important;
            color: #ffffff !important;
            padding: 14px 16px;
            text-align: left;
            font-weight: 600;
            font-size: 13px;
            border: none !important;
        }}
        
        td {{
            padding: 12px 16px;
            border-bottom: 1px solid #e2e8f0;
            color: #334155 !important;
            background: white !important;
        }}
        
        tr:last-child td {{ border-bottom: none; }}
        
        tr:nth-child(even) td {{
            background: #f8fafc !important;
        }}
        
        tr:hover td {{
            background: #e2e8f0 !important;
        }}
        
        strong {{
            color: #1e293b;
            font-weight: 600;
        }}
        
        .footer {{
            text-align: center;
            padding: 24px 40px;
            background: #f8fafc;
            border-top: 1px solid #e2e8f0;
        }}
        
        .footer p {{
            font-size: 12px;
            color: #64748b;
            margin: 0;
        }}
        
        .footer strong {{
            color: #4f46e5;
        }}
        
        @media print {{
            body {{ background: white; padding: 0; }}
            .container {{ box-shadow: none; border-radius: 0; }}
            .header {{ padding: 30px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-date">Oluşturulma Tarihi: {rapor_tarihi}</div>
            <h1>{header_title}</h1>
        </div>
        
        <div class="content">
            {rapor_metni}
        </div>
        
        <div class="footer">
            <p>Bu rapor <strong>Sesly Bot</strong> tarafından otomatik olarak oluşturulmuştur.</p>
        </div>
    </div>
</body>
</html>
"""
    
    # Dosyaya yaz
    try:
        with open(dosya_adi, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"✓ HTML raporu kaydedildi: {dosya_adi}")
        return dosya_adi
        
    except Exception as e:
        print(f"[ERROR] HTML kaydetme hatası: {e}")
        return None

def analyze_speaker_statistics(transcript_text, participant_names):
    """Transkriptten konuşmacı istatistiklerini çıkar"""
    print("[STATS] Konuşmacı istatistikleri hesaplanıyor...")
    
    stats = {
        "total_speakers": 0,
        "speaker_turns": {},
        "speaker_word_counts": {},
        "identified_speakers": [],
        "unknown_speakers": []
    }
    
    if not transcript_text or len(transcript_text) < 10:
        print("[WARN] Transkript boş veya çok kısa!")
        return stats
    
    lines = transcript_text.split('\n')
    processed_lines = 0
    
    for line in lines:
        line = line.strip()
        if not line or len(line) < 5:
            continue
        
        # "İsim: Konuşma" formatını ara
        match = re.match(r'^([^:]+):\s*(.+)$', line)
        
        if match:
            speaker = match.group(1).strip()
            speech = match.group(2).strip()
            word_count = len(speech.split())
            
            if speaker not in stats["speaker_turns"]:
                stats["speaker_turns"][speaker] = 0
                stats["speaker_word_counts"][speaker] = 0
            
            stats["speaker_turns"][speaker] += 1
            stats["speaker_word_counts"][speaker] += word_count
            processed_lines += 1
            
            # Katılımcı listesiyle eşleştir
            if participant_names:
                if speaker in participant_names:
                    if speaker not in stats["identified_speakers"]:
                        stats["identified_speakers"].append(speaker)
                elif "Konuşmacı" not in speaker and "Speaker" not in speaker:
                    if speaker not in stats["unknown_speakers"]:
                        stats["unknown_speakers"].append(speaker)
    
    stats["total_speakers"] = len(stats["speaker_turns"])
    
    print(f"[OK] {processed_lines} satır işlendi, {stats['total_speakers']} konuşmacı bulundu")
    return stats

def load_participant_data():
    """Katılımcı bilgilerini güvenle yükle"""
    print("[LOAD] Katılımcı bilgisi yükleniyor...")
    
    participants_file = "current_meeting_participants.json"
    
    if not os.path.exists(participants_file):
        print(f"[WARN] {participants_file} bulunamadı")
        return [], 0, "file_not_found"
    
    try:
        with open(participants_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            # Yeni format: Direkt liste
            if isinstance(data, list):
                names = data
            # Eski format: Dict içinde 'participants' key
            elif isinstance(data, dict):
                names = data.get("participants", [])
            else:
                names = []
            
            # EXCLUDED İSİMLER: Gerçek katılımcı olmayan UI elementleri ve bot isimleri
            excluded_names = [
                "frame", "pen_spark", "pen_spark_io", "spark_io",
                "sesly bot", "sesly", "toplantı botu", "meeting bot",
                "localhost", "panel", "bot panel", "sesly asistan",
                "google meet", "zoom", "meet", "katılım isteği", "join request"
            ]
            
            # Filtrelenmiş liste
            filtered_names = []
            for name in names:
                if name:
                    name_lower = name.lower().strip()
                    if not any(ex in name_lower for ex in excluded_names):
                        filtered_names.append(name)
                    else:
                        print(f"[FILTER] '{name}' katılımcı listesinden çıkarıldı (excluded)")
            
            names = filtered_names
            count = len(names)
            
            print(f"[SUCCESS] {count} katılımcı yüklendi")
            if names:
                print(f"[INFO] İlk 5: {', '.join(names[:5])}")
            
            return names, count, "json_file"
            
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse hatası: {e}")
        return [], 0, "json_error"
    except Exception as e:
        print(f"[ERROR] Dosya okuma hatası: {e}")
        return [], 0, "read_error"

def load_speaker_stats_json():
    """Vision monitor veya Worker'dan gelen konuşmacı istatistiklerini yükle"""
    print("[LOAD] Konuşmacı logları yükleniyor...")
    stats_file = "speaker_activity_log.json"
    
    if not os.path.exists(stats_file):
        print(f"[WARN] {stats_file} bulunamadı")
        return None
        
    try:
        with open(stats_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            # DURUM 1: Beklenen 'istatistik' formatı (Dict)
            if isinstance(data, dict) and 'statistics' in data:
                print(f"[SUCCESS] Hazır istatistikler yüklendi")
                return data
                
            # DURUM 2: Raw Log Listesi (Worker'dan gelen)
            elif isinstance(data, list):
                print(f"[INFO] Ham log listesi bulundu ({len(data)} kayıt), işleniyor...")
                
                # İstatistikleri hesapla
                stats = {}
                processed_data = {
                    "statistics": {},
                    "total_speakers": 0,
                    "meeting_duration": "0m 0s"
                }
                
                if not data:
                    return processed_data
                    
                # Zamana göre sırala
                sorted_logs = sorted(data, key=lambda x: x.get('timestamp', 0))
                start_time = sorted_logs[0].get('timestamp')
                end_time = sorted_logs[-1].get('timestamp')
                total_duration_sec = end_time - start_time
                
                if total_duration_sec > 0:
                    minutes = int(total_duration_sec // 60)
                    seconds = int(total_duration_sec % 60)
                    processed_data["meeting_duration"] = f"{minutes}m {seconds}s"
                
                # Süre hesaplama mantığı
                for i in range(len(sorted_logs) - 1):
                    current_log = sorted_logs[i]
                    next_log = sorted_logs[i+1]
                    
                    # Bu logun geçerlilik süresi (sonraki log gelene kadar)
                    duration = next_log.get('timestamp') - current_log.get('timestamp')
                    if duration > 10: duration = 10 # 10 saniyeden uzun boşlukları kes (timeout)
                    if duration < 0: duration = 0
                    
                    # DÜZELTME: Hem 'speakers' hem 'current_speakers' anahtarlarını destekle
                    # Zoom/Meet 'speakers' kullanabilir, Teams 'current_speakers' kullanıyor
                    speakers = current_log.get('speakers') or current_log.get('current_speakers', [])
                    
                    for speaker in speakers:
                        if speaker not in stats:
                            stats[speaker] = {"total_seconds": 0, "turn_count": 0}
                        
                        stats[speaker]["total_seconds"] += duration
                        # Turn count (basitçe her log girişi bir turn sayılmaz ama yaklaşık değer)
                        # Daha iyisi: Önceki logda bu speaker yoksa turn artır
                        prev_speakers = sorted_logs[i-1].get('speakers') or sorted_logs[i-1].get('current_speakers', []) if i > 0 else []
                        if speaker not in prev_speakers:
                            stats[speaker]["turn_count"] += 1
                
                # Formatla
                for speaker, val in stats.items():
                    total_sec = val["total_seconds"]
                    m = int(total_sec // 60)
                    s = int(total_sec % 60)
                    formatted = f"{m}m {s}s"
                    
                    percentage = 0
                    if total_duration_sec > 0:
                        percentage = int((total_sec / total_duration_sec) * 100)
                    
                    processed_data["statistics"][speaker] = {
                        "total_seconds": total_sec,
                        "duration": formatted, # rapor.py formatı
                        "duration_formatted": formatted,
                        "turn_count": val["turn_count"],
                        "percentage": percentage
                    }
                
                processed_data["total_speakers"] = len(stats)
                print(f"[SUCCESS] İstatistikler hesaplandı: {len(stats)} konuşmacı")
                return processed_data
            
            else:
                print("[WARN] Bilinmeyen JSON formatı")
                return None
                
    except Exception as e:
        print(f"[ERROR] İstatistik okuma/hesaplama hatası: {e}")
        return None

def extract_names_from_transcript(transcript_text):
    """Fallback: Transkriptten isim çıkar"""
    print("[FALLBACK] Transkriptten isim çıkarılıyor...")
    
    # Türkçe karakterli isim formatı
    pattern = r'^([A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)*?):'
    
    found_names = set()
    for line in transcript_text.split('\n'):
        match = re.match(pattern, line.strip())
        if match:
            name = match.group(1).strip()
            # Geçerli isim kontrolü
            if len(name) >= 3 and name not in ['Konuşmacı', 'Speaker']:
                found_names.add(name)
    
    result = sorted(list(found_names))
    print(f"[EXTRACT] {len(result)} isim bulundu")
    return result

def get_meeting_title():
    """bot_task.json'dan toplantı başlığını al"""
    try:
        task_file = Path("data/bot_task.json")
        if task_file.exists():
            task_data = json.loads(task_file.read_text(encoding="utf-8"))
            title = task_data.get("title", "")
            if title and title.strip():
                return title.strip()
    except Exception as e:
        print(f"[WARN] Toplantı başlığı okunamadı: {e}")
    return None

def generate_meeting_report(transcript_text):
    """Toplantı raporu oluştur - İYİLEŞTİRİLMİŞ"""
    print("\n" + "="*60)
    print("[RAPOR] Rapor oluşturma başladı")
    print("="*60)
    
    # 0. TOPLANTI BAŞLIĞINI AL
    meeting_title = get_meeting_title()
    if meeting_title:
        print(f"[INFO] Toplantı başlığı: {meeting_title}")
    
    # 1. KATILIMCI BİLGİSİNİ YÜKLE
    participant_names, participant_count, data_source = load_participant_data()
    
    # 2. FALLBACK: Transkriptten isim çıkar
    if participant_count == 0:
        participant_names = extract_names_from_transcript(transcript_text)
        participant_count = len(participant_names)
        data_source = "extracted_from_transcript" if participant_names else "none"
    
    # 3. KONUŞMACI İSTATİSTİKLERİ
    speaker_stats = analyze_speaker_statistics(transcript_text, participant_names)
    
    print(f"\n[STATS] Konuşmacı: {speaker_stats['total_speakers']}")
    print(f"[STATS] Tanımlanan: {len(speaker_stats['identified_speakers'])}")
    print(f"[STATS] Veri kaynağı: {data_source}")
    
    # 4. VERİ KAYNAĞI NOTU
    source_notes = {
        "json_file": "Katılımcı bilgileri Zoom panelinden alındı",
        "extracted_from_transcript": "⚠ Katılımcı bilgileri transkriptten çıkarıldı",
        "file_not_found": "⚠ Katılımcı dosyası bulunamadı",
        "json_error": "⚠ Katılımcı dosyası okunamadı",
        "read_error": "⚠ Katılımcı bilgisi yüklenemedi",
        "none": "Katılımcı bilgisi mevcut değil"
    }
    data_source_note = source_notes.get(data_source, "Bilinmeyen veri kaynağı")
    
    # 4.1 VISION MONITOR VERİSİNİ YÜKLE (YENİ)
    vision_stats = load_speaker_stats_json()
    vision_context = ""
    
    if vision_stats and vision_stats.get('statistics'):
        vision_context = "\n**GÖRSEL TESPİT EDİLEN KONUŞMACI SÜRELERİ (KESİN VERİ):**\n"
        vision_context += f"- Toplam Toplantı Süresi: {vision_stats.get('meeting_duration', 'Bilinmiyor')}\n"
        
        # Süreye göre sırala
        sorted_vision = sorted(
            vision_stats['statistics'].items(),
            key=lambda x: x[1]['total_seconds'],
            reverse=True
        )
        
        for speaker, data in sorted_vision:
            # FIX: KeyError 'duration_formatted' -> 'duration'
            duration_str = data.get('duration_formatted', data.get('duration', '0m 0s'))
            # FIX: KeyError 'turn_count' -> safely get
            turn_count = data.get('turn_count', 0)
            vision_context += f"- {speaker}: {duration_str} (%{data.get('percentage', 0)}), {turn_count} kez konuştu\n"
            
        print("[INFO] Vision monitor verisi rapora eklendi")
    else:
        vision_context = ""  # Vision Monitor kullanılmıyorsa boş bırak
    
    # 5. TOPLANTI BAŞLIĞI CONTEXT
    meeting_title_context = ""
    if meeting_title:
        meeting_title_context = f"\n**TOPLANTI ADI:** {meeting_title}\n"
    
    # 6. GEMINI PROMPT - HTML FORMAT
    FINAL_PROMPT = f"""
SEN: Sen yüksek düzeyde profesyonel bir toplantı analisti ve formatlama uzmanısın. Görevin, aşağıdaki transkriptten detaylı bir rapor hazırlamak ve çıktıyı A4 basımına uygun, profesyonel bir HTML formatında, kalın ve vurgulu başlıklar kullanarak vermektir. Raporu sadece HTML olarak döndür. Asla düz metin veya Markdown kullanma.

{meeting_title_context}

<h1 style='font-size: 24px; color: #1e88e5; border-bottom: 2px solid #1e88e5; padding-bottom: 5px;'>{meeting_title if meeting_title else 'PROJE TOPLANTI ANALİZ RAPORU'}</h1>

<h2 style='font-size: 18px; color: #333;'>1. TOPLANTI ÖZETİ (ANA FİKİR)</h2>
<p style='font-size: 14px;'>Toplantının ana konusunu, tartışılan en önemli 3 noktayı ve nihai sonuçlarını özetle.</p>

<h2 style='font-size: 18px; color: #333;'>2. SUNULAN FİKİRLER, KARARLAR VE DURUM ANALİZİ</h2>
<p style='font-size: 14px;'>Transkriptten tespit edilen her fikri aşağıdaki tabloya ekle. Her satır bir fikir olmalı:</p>
<table border='1' cellpadding='8' cellspacing='0' width='100%' style='border-collapse: collapse; font-size: 14px;'>
    <tr style='background-color: #f0f0f0;'>
        <th width='20%'>Fikri Sunan</th>
        <th width='50%'>Fikir Detayı</th>
        <th width='30%'>Durum (Kabul/Red/Tartışıldı)</th>
    </tr>
    <!-- Transkriptten fikir satırları ekle -->
</table>

<h3 style='font-size: 16px; color: #555; margin-top: 15px;'>Nihai Kararlar</h3>
<ul style='list-style-type: disc; font-size: 14px; margin-left: 20px;'>
    <!-- Kesinleşen kararları madde madde listele -->
</ul>

<h2 style='font-size: 18px; color: #333;'>3. AKSİYON MADDELERİ (YAPILACAKLAR)</h2>
<p style='font-size: 14px;'>Transkriptten tespit edilen tüm aksiyonları tabloya ekle:</p>
<table border='1' cellpadding='8' cellspacing='0' width='100%' style='border-collapse: collapse; font-size: 14px;'>
    <tr style='background-color: #f0f0f0;'>
        <th width='20%'>Sorumlu Kişi</th>
        <th width='50%'>Görev Tanımı</th>
        <th width='30%'>Son Tarih/Durum</th>
    </tr>
    <!-- Aksiyon satırları ekle -->
</table>

<h2 style='font-size: 18px; color: #333;'>4. KATILIM KALİTESİ ANALİZİ</h2>
<p style='font-size: 14px;'>Transkriptten her katılımcının katkısını değerlendir. <strong>Katkı Notu</strong> şu kriterlere göre belirlenir:</p>
<ul style='font-size: 12px; color: #666; margin-bottom: 15px;'>
    <li><strong>Yüksek:</strong> Birden fazla fikir sunmuş, karar almış veya aksiyon üstlenmiş</li>
    <li><strong>Orta:</strong> En az bir fikir/soru sormuş veya tartışmaya katılmış</li>
    <li><strong>Düşük:</strong> Sadece dinleyici konumunda kalmış veya çok az katkı sağlamış</li>
</ul>
<table border='1' cellpadding='8' cellspacing='0' width='100%' style='border-collapse: collapse; font-size: 14px;'>
    <tr style='background-color: #f0f0f0;'>
        <th width='25%'>Katılımcı</th>
        <th width='20%'>Sunduğu Fikir Sayısı</th>
        <th width='20%'>Aldığı Karar/Görev</th>
        <th width='20%'>Sorduğu Soru</th>
        <th width='15%'>Katkı Notu</th>
    </tr>
    <!-- Her katılımcı için satır ekle. Katkı Notu: Düşük/Orta/Yüksek -->
</table>

{vision_context}

**TRANSKRİPT:**
{transcript_text[:20000]}

**ÖNEMLİ TALİMATLAR:** 
- Çıktıyı sadece HTML olarak ver, markdown kullanma
- Tüm tabloları doldur, boş bırakma
- Eğer bir bölüm için bilgi yoksa "Transkriptte bu konuda bilgi bulunamadı" yaz
- Türkçe karakter kullan
- HTML yorumlarını (<!-- -->) kaldır ve gerçek içerikle değiştir
- Transkript 20.000 karakterden uzunsa, özet bilgilerle devam et
- **TOPLANTI ADI:** Eğer yukarıda toplantı adı belirtildiyse, rapor başlığında bu adı kullan.
- **KRİTİK:** 'Görsel Tespit Edilen Konuşmacı Süreleri' ve 'Katılımcı Bilgileri' bölümlerindeki verileri kullanarak, transkriptteki aksiyonları ve fikirleri mümkün olduğunca doğru kişilere atfet.
- **KATKI NOTU AÇIKLAMASI:** Her katılımcının 'Katkı Notu' değerini yukarıdaki kriterlere göre belirle ve tabloda göster.
"""
    
    # 8. GEMİNİ API ÇAĞRISI
    try:
        print("[GEMINI] API çağrısı gönderiliyor...")
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
        
        # Güvenlik ayarlarını gevşet (Hata almamak için)
        safety_settings = [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE"
            }
        ]
        
        response = model.generate_content(FINAL_PROMPT, safety_settings=safety_settings)
        rapor_metni = response.text or "Rapor oluşturulamadı."
        
        # Markdown clean up (```html ... ``` temizle)
        rapor_metni = re.sub(r'^```html\s*', '', rapor_metni, flags=re.MULTILINE)
        rapor_metni = re.sub(r'^```\s*', '', rapor_metni, flags=re.MULTILINE)
        rapor_metni = re.sub(r'\s*```$', '', rapor_metni, flags=re.MULTILINE)
        
        print(f"[SUCCESS] Gemini rapor oluşturdu: {len(rapor_metni)} karakter")
        
    except Exception as e:
        print(f"[ERROR] Gemini hatası: {e}")
        # Basit fallback rapor (HTML formatında)
        participants_str = ', '.join(participant_names) if participant_names else 'Bilinmiyor'
        rapor_metni = f"""
<h1 style='font-size: 24px; color: #1e88e5; border-bottom: 2px solid #1e88e5; padding-bottom: 5px;'>TOPLANTI RAPORU</h1>

<h2 style='font-size: 18px; color: #333;'>1. Özet</h2>
<p style='font-size: 14px;'>Toplantı kaydı alındı. {participant_count} katılımcı tespit edildi.</p>

<h2 style='font-size: 18px; color: #333;'>2. Katılımcılar</h2>
<p style='font-size: 14px;'>{participants_str}</p>

<h2 style='font-size: 18px; color: #333;'>3. Konuşmacı İstatistikleri</h2>
<p style='font-size: 14px;'>Toplam konuşmacı: {speaker_stats['total_speakers']}</p>

<h2 style='font-size: 18px; color: #333;'>4. Not</h2>
<p style='font-size: 14px;'>Detaylı analiz için Gemini API'sine erişim gerekli.<br>Hata: {str(e)}</p>
"""
    
    # 9. HTML OLUŞTUR
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]  # 8 karakter kısa UUID
    
    # Geçici dizine kaydet
    temp_dir = Path("temp_reports")
    temp_dir.mkdir(exist_ok=True)
    
    html_path = str(temp_dir / f"Toplanti_Raporu_{timestamp}_{unique_id}.html")
    
    result_path = raporu_html_olarak_kaydet(rapor_metni, html_path, meeting_title)
    
    if result_path and Path(result_path).exists():
        file_size = os.path.getsize(result_path) / 1024
        print(f"[SUCCESS] HTML raporu oluşturuldu: {result_path} ({file_size:.1f} KB)")
        
        # --- SUPABASE UPLOAD ---
        try:
            print("[UPLOAD] Rapor Supabase'e yükleniyor...")
            public_url = upload_file("reports", result_path)
            if public_url:
                print(f"[Cloud] Rapor URL: {public_url}")
                return result_path, public_url  # URL'i de döndür
        except Exception as e:
            print(f"[WARN] Upload hatası: {e}")
            return result_path, None
            
        return result_path, None
    else:
        print("[ERROR] HTML dosyası oluşturulamadı!")
        return None, None

def save_to_supabase(html_report_path, html_report_url, transcript_text):
    """
    Rapor ve transkripti Supabase'e kaydeder.
    bot_task.json'dan toplantı bilgilerini okur.
    """
    try:
        task_file = Path("data/bot_task.json")
        if not task_file.exists():
            print("[WARN] bot_task.json bulunamadı, DB kaydı yapılamıyor.")
            return

        task_data = json.loads(task_file.read_text(encoding="utf-8"))
        user_id = task_data.get("user_id")
        
        if not user_id:
            print("[WARN] user_id bulunamadı (misafir mod?), DB kaydı atlanıyor.")
            return

        print(f"[DB] Kayıt başlıyor... User: {user_id}")

        # 1. Transkripti yükle
        transcript_url = None
        try:
            # Benzersiz dosya adı oluştur (timestamp + UUID) - Her toplantı için farklı dosya
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = uuid.uuid4().hex[:8]  # 8 karakter kısa UUID
            t_path = Path(f"temp_reports/transcript_{timestamp}_{unique_id}.txt")
            t_path.parent.mkdir(exist_ok=True)
            t_path.write_text(transcript_text, encoding="utf-8")
            
            print("[UPLOAD] Transkript yükleniyor...")
            transcript_url = upload_file("transcripts", str(t_path))
            if transcript_url:
                print(f"[Cloud] Transkript URL: {transcript_url}")
        except Exception as e:
            print(f"[WARN] Transkript upload hatası: {e}")

        # 2. DB Kaydı oluştur
        # Süre hesapla (basitçe karakter sayısından tahmin veya task timestamps)
        # Şimdilik transkript uzunluğundan tahmini bir süre yazalım
        duration_min = len(transcript_text) // 1000  # Çok kaba taslak
        duration_str = f"{duration_min} dk" if duration_min > 0 else "1 dk"

        success = save_meeting_record(
            user_id=user_id,
            title=task_data.get("title", "İsimsiz Toplantı"),
            platform=task_data.get("platform", "Zoom"),
            start_time=datetime.datetime.utcnow().isoformat(), # UTC olarak kaydet
            duration=duration_str,
            transcript_url=transcript_url,
            report_url=html_report_url,
            summary_text="Otomatik oluşturulan toplantı raporu." # İleride AI özeti buraya gelebilir
        )
        
        if success:
            print("[SUCCESS] Toplantı veritabanına başarıyla kaydedildi!")
        else:
            print("[ERROR] Veritabanı kaydı başarısız oldu.")

    except Exception as e:
        print(f"[ERROR] save_to_supabase genel hatası: {e}")




# def create_pdf_report(rapor_metni, participant_count, speaker_stats, data_source_note):
#     """PDF raporu oluştur - İYİLEŞTİRİLMİŞ"""
#     # Bu fonksiyon artık kullanılmıyor. HTML rapor kullanılıyor.
#     # PDF gerekirse, tarayıcıdan "Print to PDF" kullanılabilir.
#     pass

if __name__ == "__main__":
    print("[MAIN] Rapor oluşturma başlatılıyor...", flush=True)
    
    transcript_file = Path("latest_transcript.txt")
    if not transcript_file.exists():
        print("[ERROR] latest_transcript.txt bulunamadı!", flush=True)
    else:
        text = transcript_file.read_text(encoding="utf-8")
        if not text.strip():
            print("[ERROR] Transkript dosyası boş!", flush=True)
        else:
            print(f"[INFO] Transkript yüklendi ({len(text)} karakter). Rapor üretiliyor...", flush=True)
            
            # 1. Raporu oluştur
            report_path, report_url = generate_meeting_report(text)
            
            # 2. Veritabanına kaydet (Eğer rapor başarılıysa)
            if report_path and report_url:
                save_to_supabase(report_path, report_url, text)