"""Finite child processes; no database, writes or shared global models in workers."""
import multiprocessing as mp
import time
from fastapi import HTTPException


def _train_worker(sender,kind,dataset):
    try:
        from threadpoolctl import threadpool_limits
        from app.ml.supervised_model import train_forecast
        from app.ml.unsupervised_model import train_segments
        with threadpool_limits(limits=1):
            result={'FORECAST':train_forecast,'RFM':train_segments}[kind](dataset)
        sender.send(('ok',result))
    except Exception:
        # No dataset, buyer names, credential or provider exception in logs/responses.
        sender.send(('error',None))
    finally:sender.close()


def run_training(kind,dataset,*,timeout=60,worker=None):
    if kind not in ('FORECAST','RFM'):raise ValueError('Unknown analysis kind')
    if not 0<timeout<=60:raise ValueError('Training timeout must be at most60 seconds')
    context=mp.get_context('spawn')
    reader,writer=context.Pipe(duplex=False)
    # Spawn isolates the algorithms; threadpool_limits caps loaded BLAS runtimes.
    process=context.Process(target=worker or _train_worker,args=(writer,kind,dataset),daemon=True)
    start=time.monotonic()
    try:
        process.start();writer.close()
        if not reader.poll(max(0,timeout-(time.monotonic()-start))):
            raise HTTPException(503,'El análisis excedió60 segundos; se conserva el resultado anterior.')
        try:status,result=reader.recv()
        except (EOFError,OSError):raise HTTPException(503,'El proceso de análisis no pudo terminar; reintenta.')
        if status!='ok':raise HTTPException(503,'No se pudo actualizar el análisis; se conserva el anterior.')
        return result
    finally:
        reader.close();writer.close()
        if process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():process.terminate();process.join(timeout=2)
            if process.is_alive():process.kill();process.join(timeout=2)
            process.close()
