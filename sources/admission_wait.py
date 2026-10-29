"""Wait only after an unstarted capacity rejection; retain the original limits."""
import json
from pathlib import Path
import time
from .cell_windows import file_binding
from .run import bounded,write

ROOT=Path(__file__).resolve().parents[1]


def preserve_capacity_rejection(out,label):
    cap=out/(label+'-capacity.json')
    if not cap.exists() or (out/(label+'.log')).exists() or (out/(label+'-resources.json')).exists():
        raise ValueError('Only a worker that never started may wait for admission')
    data=json.loads(cap.read_text())
    if data['eligible_cpus'] and data['mem_available_bytes']>3*1024**3 and data['disk_free_bytes']>4.5*1024**3:
        raise ValueError('Capacity record does not explain admission rejection')
    root=out/'admission-pauses'/label;root.mkdir(parents=True,exist_ok=True)
    archive=root/f'attempt-{len(list(root.glob("attempt-*")))+1:02d}';archive.mkdir()
    original=file_binding(cap);cap.rename(archive/cap.name)
    write(archive/'record.json',{'reason':'capacity_not_admitted','native_execution_started':False,
        'original_capacity':original,'preserved_capacity':file_binding(archive/cap.name),
        'source_code':file_binding(Path(__file__)),'limits_increased':False})


def admitted_bounded(out,label,command,env=None,project=None):
    for attempt in range(20):
        try:return bounded(out,label,command,env,project)
        except RuntimeError as error:
            if str(error)!='Capacity unavailable; '+label+' not started':raise
            preserve_capacity_rejection(out,label)
            print(json.dumps({'waiting_for_capacity':label,'attempt':attempt+1,'native_started':False}),flush=True)
            if attempt==19:raise RuntimeError('Capacity remained unavailable after 20 recorded admissions') from error
            time.sleep(15)
