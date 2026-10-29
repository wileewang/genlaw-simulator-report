"""Export completed quantitative checkpoints with the existing planar display code."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded as bounded
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.run import write
LOAD=lambda p:json.loads(p.read_text())


def build(run,out):
    run=run.resolve();out=out.resolve();run.relative_to(ROOT);out.relative_to(ROOT)
    summary=LOAD(run/'summary.json')
    if summary['status']!='fine_checkpoints_passed':raise ValueError('Complete quantitative checks first')
    out.mkdir(parents=True,exist_ok=False)
    write(out/'intent.json',{'checkpoints':file_binding(run/'summary.json'),'source_code':file_binding(Path(__file__)),
        'admission_wait_source':file_binding(ROOT/'genlaw_pipeline/admission_wait.py')})
    records=[]
    for row in summary['checkpoints']:
        result=LOAD(checked_file(row['result']));surface=checked_file(result['surface'])
        if not result['fine_checkpoint_acceptance']:raise ValueError('Checkpoint is not accepted')
        case=out/row['id'];case.mkdir();parts=case/'parts';parts.mkdir()
        count=len(LOAD(surface)['windows'])
        for start in range(0,count,8):
            bounded(case,f'compact-{start:03d}',[sys.executable,ROOT/'tools/build_window_display.py',
                '--surface',surface,'--output',parts,'--batch-start',str(start),'--batch-end',str(min(start+8,count))])
            print(json.dumps({'display_checkpoint':row['id'],'windows_completed':min(start+8,count),'total':count}),flush=True)
        bounded(case,'combine',[sys.executable,ROOT/'genlaw_pipeline/planar_display.py','combine',
            '--surface',surface,'--directory',parts,'--output',case/'actual.mesh','--report',case/'display.json'])
        display=LOAD(case/'display.json')
        if display['triangles']>800000:raise ValueError('Browser per-mesh budget exceeded')
        records.append({'id':row['id'],'checkpoint':row['result'],'display':file_binding(case/'display.json'),
            'triangles':display['triangles']})
        print(json.dumps(records[-1]),flush=True)
    write(out/'summary.json',{'status':'actual_checkpoint_displays_exported','checkpoints':records,
        'source_code':file_binding(Path(__file__)),'quantitative_mesh_modified':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.run,a.output)
