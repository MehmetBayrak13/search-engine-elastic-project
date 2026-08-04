import sys
from pathlib import Path

# tests/ dizini bir paket olmadığı için proje kökünü import path'ine ekle,
# böylece `import app` ve `import config` çalışır.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
