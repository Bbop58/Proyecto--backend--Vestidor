import multiprocessing as mp
import os
import time
import pytest
from fastapi import HTTPException
from app.ml.training_runner import run_training
from tests.test_forecast_business import dataset


def delayed(sender,kind,data):time.sleep(10)


def crash(sender,kind,data):os._exit(3)


def error(sender,kind,data):sender.send(('error',None));sender.close()


def test_real_spawn_trains_company_data_and_returns_artifact():
    result=run_training('FORECAST',dataset([100]*30))
    assert result.result['estado']=='LISTO' and result.artifact


@pytest.mark.parametrize('worker',[delayed,crash,error])
def test_timeout_crash_and_failure_are_bounded_and_child_is_reaped(worker):
    before={p.pid for p in mp.active_children()};start=time.monotonic()
    with pytest.raises(HTTPException) as exc:
        run_training('FORECAST',dataset([100]*30),timeout=0.4,worker=worker)
    assert exc.value.status_code==503
    assert time.monotonic()-start<6
    assert {p.pid for p in mp.active_children()}==before


def test_worker_error_is_visible_after_import_not_just_timeout():
    with pytest.raises(HTTPException) as exc:
        run_training('FORECAST',dataset([100]*30),timeout=15,worker=error)
    assert exc.value.status_code==503 and 'No se pudo' in exc.value.detail
