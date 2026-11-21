import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("SUPABASE_URL", None)            # tests always run on the in-memory database
os.environ.pop("SUPABASE_SERVICE_KEY", None)
os.environ.pop("AIVANA_API_KEY", None)
