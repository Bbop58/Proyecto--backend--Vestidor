"""Local QA API: reused synthetic fixture, no provider keys or model artifacts."""
import os,secrets,tempfile
from pathlib import Path

os.environ.update(DATABASE_URL='postgresql://audit@127.0.0.1:55439/fashionstore_test_phase5_browser',
    SECRET_KEY=secrets.token_urlsafe(48),WALLET_SIGNING_KEY=secrets.token_urlsafe(48),
    RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false',
    GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',CHATBOT_GEMINI_API_KEY='')

if __name__=='__main__':
    import uvicorn
    from app.ml.ml_service import MLAnalyticsService
    from app.ml.supervised_model import DemandForecaster
    from app.ml.unsupervised_model import CustomerSegmenter
    with tempfile.TemporaryDirectory(prefix='fashionstore_phase6_') as directory:
        MLAnalyticsService._forecaster=DemandForecaster(model_path=str(Path(directory)/'demand.joblib'))
        MLAnalyticsService._segmenter=CustomerSegmenter(model_path=str(Path(directory)/'segments.joblib'))
        uvicorn.run('app.main:app',host='127.0.0.1',port=58001)
