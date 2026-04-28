from pathlib import Path

# Base project directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Subdirectories
CORE_DIR = BASE_DIR / "core"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
RESEARCH_DIR = BASE_DIR / "research"
UTILS_DIR = BASE_DIR / "utils"

# File paths
DB_PATH = RESEARCH_DIR / "elequant.db"
OPERATORS_JSON = DATA_DIR / "operators.json"
DATAFIELDS_JSON = DATA_DIR / "datafields.json"
ENV_FILE = BASE_DIR / ".env"

# Ensure directories exist
for folder in [CORE_DIR, DATA_DIR, LOGS_DIR, RESEARCH_DIR, UTILS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)
