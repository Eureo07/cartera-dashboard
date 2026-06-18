# -*- coding: utf-8 -*-
"""
Configuracion unificada para todos los scripts de la cartera.
Importar con: from config_loader import CFG, logger, get_logger
"""
import json, os, sys, logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

_CONFIG_PATH = None
for p in [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
    "C:/Users/franl/OneDrive/Escritorio/Inversi\u00f3n/OpenCode/2026/config.json",
]:
    if os.path.exists(p):
        _CONFIG_PATH = p
        break

if not _CONFIG_PATH:
    print("ERROR: config.json no encontrado")
    sys.exit(1)

with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

BASE_DIR = CFG["base_dir"]
if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR, exist_ok=True)

# Build full paths
for key in CFG.get("paths", {}):
    rel = CFG["paths"][key]
    CFG["paths"][key] = rel if os.path.isabs(rel) else os.path.join(BASE_DIR, rel)

# Logging setup
_log_dir = CFG["paths"].get("logs", os.path.join(BASE_DIR, "logs"))
os.makedirs(_log_dir, exist_ok=True)

def get_logger(name, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.handlers:
        return logger
    fh = RotatingFileHandler(
        os.path.join(_log_dir, f"{name}.log"),
        maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)
    return logger

logger = get_logger("cartera")
