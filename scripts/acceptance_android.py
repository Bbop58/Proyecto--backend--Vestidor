"""UI-only helpers for the owned QA emulator, never physical devices."""
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ADB=Path(os.environ['LOCALAPPDATA'])/'Android/Sdk/platform-tools/adb.exe'
DEVICE='emulator-5554'
ROOT=Path(__file__).resolve().parents[2]
CAPTURES=ROOT/'docs/auditoria_software/RONDA_ACEPTACION_1_CAPTURAS'


def shell(*args):
    return subprocess.check_output([str(ADB),'-s',DEVICE,'shell',*args],stderr=subprocess.STDOUT,timeout=35)


def dump():
    for attempt in range(3):
        file='/sdcard/qa-round1-current.xml'
        shell('rm','-f',file)
        output=shell('uiautomator','dump',file)
        if b'dumped to:' in output:
            return ET.fromstring(shell('cat',file))
        time.sleep(1)
    raise RuntimeError('No fresh Android UI dump; do not use stale XML')


def tap(node):
    x,y,x2,y2=map(int,re.findall(r'\d+',node.get('bounds')))
    shell('input','tap',str((x+x2)//2),str((y+y2)//2))


def click(text,exact=True):
    nodes=[n for n in dump().iter('node') if (n.get('content-desc') or n.get('text') or '')==text] if exact else [n for n in dump().iter('node') if text in (n.get('content-desc') or n.get('text') or '')]
    if len(nodes)!=1:raise RuntimeError('Expected one visible target: '+text+'; count='+str(len(nodes)))
    tap(nodes[0])


def fill(password,value):
    tap(next(n for n in dump().iter('node') if n.get('class')=='android.widget.EditText' and n.get('password')==str(password).lower()))
    focused=next(n for n in dump().iter('node') if n.get('class')=='android.widget.EditText' and n.get('focused')=='true')
    if focused.get('password')!=str(password).lower():raise RuntimeError('Unexpected focused field')
    shell('input','keyevent','123');shell('input','keyevent',*(['67']*85));shell('input','text',value);shell('input','keyevent','4')


def inspect():
    root=dump()
    for n in root.iter('node'):
        text=n.get('content-desc') or (n.get('text') if n.get('password')!='true' else '')
        if text:print(text[:240],n.get('bounds'))
        if n.get('class')=='android.widget.EditText':print('field password',n.get('password'),n.get('bounds'))


def screenshot(name):
    CAPTURES.mkdir(parents=True,exist_ok=True)
    (CAPTURES/(name+'.png')).write_bytes(subprocess.check_output([str(ADB),'-s',DEVICE,'exec-out','screencap','-p'],timeout=25))


if __name__=='__main__':inspect()
