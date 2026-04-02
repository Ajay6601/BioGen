import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
CONSTRAINTS_DIR = Path(__file__).resolve().parent / "constraints"
DATA_DIR = ROOT_DIR / "data"
OUTPUT_DIR = ROOT_DIR / "outputs"
SANDBOX_DIR = ROOT_DIR / "sandbox_runs"

MODEL = os.getenv("BIOGEN_MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.getenv("BIOGEN_MAX_TOKENS", "4096"))
SANDBOX_TIMEOUT = int(os.getenv("BIOGEN_SANDBOX_TIMEOUT", "120"))
LOG_LEVEL = os.getenv("BIOGEN_LOG_LEVEL", "INFO")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Supported analysis types
ANALYSIS_TYPES = ["bulk_rnaseq_de", "scrna_clustering", "visualization"]

# Ensure dirs exist
for d in [OUTPUT_DIR, SANDBOX_DIR]:
    d.mkdir(parents=True, exist_ok=True)
