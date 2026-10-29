"""Finish display export from verified complete parts after an inspected interrupt."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded as bounded
from genlaw_pipeline.cell_windows import file_binding,checked_file
from genlaw_pipeline.run import write
RUN=ROOT/'validation/runs/S4-ORIGINAL-SIZE-CHECKPOINTS-20261029-r1'
OUT=ROOT/'validation/runs/S4-ORIGINAL-SIZE-CHECKPOINT-DISPLAY-20261029-r1'
LOAD=lambda p:json.loads(p.read_text())


def resume():
    intent=LOAD(OUT/'intent.json');checked_file(intent['source_code']);checked_file(intent['admission_wait_source'])
    summary=LOAD(checked_file(intent['checkpoints']))
    attempts=OUT/'resumes';attempts.mkdir(exist_ok=True);number=len(list(attempts.glob('attempt-*')))+1
    attempt=attempts/f'attempt-{number:02d}';attempt.mkdir()
    write(attempt/'intent.json',{'original_intent':file_binding(OUT/'intent.json'),'source_code':file_binding(Path(__file__))})
    records=[]
    for item in summary['checkpoints']:
        result=LOAD(checked_file(item['result']));surface=checked_file(result['surface']);data=LOAD(surface)
        case=OUT/item['id'];case.mkdir(exist_ok=True);parts=case/'parts';parts.mkdir(exist_ok=True)
        saved=sorted(parts.glob('window-*.json'))
        if [p.stem for p in saved]!=[f'window-{i:03d}' for i in range(len(saved))]:raise ValueError('Noncontiguous display parts')
        for i,path in enumerate(saved):
            row=LOAD(path)
            for key in ('source_result','source_audit','display_surface','source_code'):checked_file(row[key])
            if row['source_result']!=data['windows'][i]['result'] or row['source_audit']!=data['windows'][i]['audit']:
                raise ValueError('Foreign display part')
            if row['quantitative_mesh_modified']:raise ValueError('Quantitative mesh was changed')
        if len(list(parts.glob('window-*.stl')))!=len(saved):raise ValueError('Inspect incomplete STL before resuming')
        print(json.dumps({'display_checkpoint':item['id'],'verified_reused_windows':len(saved)}),flush=True)
        if not (case/'display.json').exists():
            for start in range(len(saved),len(data['windows']),8):
                end=min(start+8,len(data['windows']))
                bounded(case,f'resume-{number:02d}-compact-{start:03d}',[sys.executable,ROOT/'tools/build_window_display.py',
                    '--surface',surface,'--output',parts,'--batch-start',str(start),'--batch-end',str(end)])
                print(json.dumps({'display_checkpoint':item['id'],'windows_completed':end,'total':len(data['windows'])}),flush=True)
            bounded(case,f'resume-{number:02d}-combine',[sys.executable,ROOT/'genlaw_pipeline/planar_display.py','combine',
                '--surface',surface,'--directory',parts,'--output',case/'actual.mesh','--report',case/'display.json'])
        display=LOAD(case/'display.json');checked_file(display['display'])
        assert display['actual_surface']==result['surface'] and display['triangles']<=800000
        records.append({'id':item['id'],'checkpoint':item['result'],'display':file_binding(case/'display.json'),'triangles':display['triangles']})
        print(json.dumps(records[-1]),flush=True)
    failure=OUT/'after-02/compact-024-resources.json';failed=LOAD(failure)
    assert failed['status']=='interrupted' and not failed['success']
    write(OUT/'recovered-interruptions.json',{'status':'recovered_after_inspection','failures':[{
        'failed':file_binding(failure),'accepted_display':file_binding(OUT/'after-02/display.json'),
        'completed_parts_reused':[file_binding(OUT/f'after-02/parts/window-{i:03d}.json') for i in (24,25,26)],
        'remaining_original_batch_parts':[file_binding(OUT/f'after-02/parts/window-{i:03d}.json') for i in range(27,32)],
        'all_parts_bound_to_same_actual_surface':True}],
        'termination_cause':'unknown','quantitative_mesh_modified':False,'limits_increased':False})
    write(OUT/'summary.json',{'status':'actual_checkpoint_displays_exported','checkpoints':records,
        'source_code':file_binding(Path(__file__)),'recovery':file_binding(OUT/'recovered-interruptions.json'),
        'quantitative_mesh_modified':False})


if __name__=='__main__':resume()
