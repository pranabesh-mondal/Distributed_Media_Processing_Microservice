import os
import sys
from pathlib import Path

# Hermetic default test environment (no real services required).
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("S3_ENDPOINT_URL", "")

# Ensure the project root is importable when tests run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
