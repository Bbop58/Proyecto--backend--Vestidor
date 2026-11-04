"""Phase 7 QA only: a fresh dedicated DB, temporary keys, no external providers."""
import os
import secrets
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(
    DATABASE_URL='postgresql://audit@127.0.0.1:55499/fashionstore_test_phase7_browser',
    SECRET_KEY=secrets.token_urlsafe(48), WALLET_SIGNING_KEY=secrets.token_urlsafe(48),
    RUN_STARTUP_MIGRATIONS='false', RUN_DEMO_SEED='false', GEMINI_API_KEY='',
    VOICE_GEMINI_API_KEY='', CHATBOT_GEMINI_API_KEY='', HF_TOKEN='',
)

if __name__ == '__main__':
    import uvicorn
    from sqlalchemy import inspect
    from app.database import engine
    existing = bool(inspect(engine).get_table_names())
    if existing and sys.argv[1:] != ['--reuse']:
        raise RuntimeError('Phase 7 browser DB must be empty, or explicitly --reuse without seeding')
    # Reuse the small synthetic browser fixture in a validated local target.
    import runpy
    if not existing:
        runpy.run_path(str(Path(__file__).with_name('phase5_browser_fixture.py')),run_name='__fixture__')
    uvicorn.run('app.main:app', host='127.0.0.1', port=58001)
