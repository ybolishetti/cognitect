"""
Ensures the test suite never depends on real Supabase credentials.

api/auth.py and api/storage/plan_store.py read SUPABASE_URL,
SUPABASE_SERVICE_KEY, and SUPABASE_JWT_SECRET from the environment at *import
time* (fail-fast, matching how engine/intent_parser/parser.py already treats
COGNITECT_CLAUDE_API_KEY). Any test file that imports api.main
(test_load.py, test_plans_v2.py) transitively imports those modules.

This module-level code (not a fixture — fixtures run too late, after test
modules are already imported) runs before pytest imports any test module in
this directory, so dummy values are in place before those imports fire.
os.environ.setdefault() means a real .env (loaded later, inside api/main.py's
load_dotenv() call) never overrides these — dotenv's default is
override=False — so tests are hermetic and never touch real Supabase
credentials, even if a real .env happens to be present locally.
"""
import os

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-not-for-production")
