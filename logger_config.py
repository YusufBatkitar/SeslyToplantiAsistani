import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
import os

def setup_logger(name: str, log_file: str = "sesly_bot.log") -> logging.Logger:
    """
    Merkezi logger yapılandırması
    
    Features:
    - Console + dosyaya yazma
    - Log rotation (10 MB, 5 backup)
    - Timestamp otomatik
    - Satır numarası
    
    Args:
        name: Logger ismi (genellikle __name__)
        log_file: Log dosya ismi (varsayılan: sesly_bot.log)
    
    Returns:
        logging.Logger: Yapılandırılmış logger instance
    """
    logger = logging.getLogger(name)
    
    # Zaten yapılandırılmışsa tekrar ekleme
    if logger.handlers:
        return logger
    
    # Log seviyesi (.env'den okunabilir, varsayılan DEBUG vision için)
    default_level = "DEBUG" if "vision" in log_file.lower() else "INFO"
    log_level = os.getenv("LOG_LEVEL", default_level).upper()
    logger.setLevel(log_level)
    
    # Formatlar
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Log klasörü
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # File handler (rotating) - UNBUFFERED için
    file_handler = RotatingFileHandler(
        logs_dir / log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8',
        delay=False  # Hemen dosya aç
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    
    # CRITICAL: Buffer'ı kapat - gerçek zamanlı log yazmak için
    file_handler.stream.reconfigure(line_buffering=True)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    # Vision log için DEBUG, diğerleri INFO
    console_level = logging.DEBUG if "vision" in log_file.lower() else logging.INFO
    console_handler.setLevel(console_level)
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Propagation'ı kapat - duplicate log önlemek için
    logger.propagate = False
    
    return logger