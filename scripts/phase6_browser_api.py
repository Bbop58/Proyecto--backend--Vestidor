"""Local QA API: reused synthetic fixture, no provider keys or model artifacts."""
import os,secrets,tempfile
from pathlib import Path

os.environ.update(DATABASE_URL='postgresql://audit@127.0.0.1:55439/fashionstore_test_phase5_browser',
    SECRET_KEY=secrets.token_urlsafe(48),WALLET_SIGNING_KEY=secrets.token_urlsafe(48),
    RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false',
    GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',CHATBOT_GEMINI_API_KEY='')

if __name__=='__main__':
    import uvicorn
    # Historical QA launcher only. ML now reads company data from its QA DB;
    # no global synthetic estimators or temporary joblib artifacts are installed.
    uvicorn.run('app.main:app',host='127.0.0.1',port=58001)
