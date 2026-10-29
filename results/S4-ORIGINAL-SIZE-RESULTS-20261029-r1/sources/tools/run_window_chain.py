"""Original-size explicit card/CAD -> native FreeCAD NC -> bounded global CAMotics cells."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import prepare,digest
from genlaw_pipeline.programming import checked_plan,check_nc,simulation_project,verify_binding
from genlaw_pipeline.run import bounded,write,FREECAD,workspace_environment,camotics_budget
from genlaw_pipeline.cell_windows import (candidate_engine,expected_grid,partition,window_budget,
                                         inspect_and_plan,run_window,file_binding)
from genlaw_pipeline.sharded_measurement import measure_shards


def run(input_path,out,engine,regression_path):
    input_path,out,engine,regression_path=[p.resolve() for p in (input_path,out,engine,regression_path)]
    for p in (input_path,out,engine,regression_path):p.relative_to(ROOT)
    out.mkdir(parents=True,exist_ok=False)
    regression=json.loads(regression_path.read_text())
    if (regression.get('schema')!='genlaw.cell-window-regression.v1' or regression.get('status')!='passed'
            or regression.get('engine_manifest')!=file_binding(engine)
            or len(regression.get('normal_cases',[]))!=4 or len(regression.get('controls',[]))!=2
            or not all(x['regression_passed'] for x in regression['normal_cases']+regression['controls']+regression['default_cases'])):
        raise ValueError('Original-size experiment requires the frozen default and partition regressions')
    candidate_engine(engine)
    record={'schema':'genlaw.window-chain-run.v1','status':'started','input':file_binding(input_path),
            'engine_manifest':file_binding(engine),'regression':file_binding(regression_path),
            'source_code':file_binding(Path(__file__)),'production_approved':False,'whole_process_card_complete':False}
    try:
        preparation=prepare(input_path);write(out/'preparation-result.json',preparation)
        if preparation['status']!='ready_for_declared_scope':raise ValueError('Original-size input is not ready: '+str(preparation['issues']))
        plan=checked_plan(input_path,out/'preparation-result.json');write(out/'execution-plan.json',plan)
        config=simulation_project(plan);grid=expected_grid(config);windows=partition(grid['steps'])
        write(out/'simulation-budget.json',{'legacy_full_stock':camotics_budget(config),'global_grid':grid,
            'window_count':len(windows),'windows':[{'bounds':w,**window_budget(w,grid['steps'])} for w in windows],
            'original_stock_dimensions_preserved':True,'native_limits_unchanged':True,
            'execution_backend':'explicit_global_cell_windows','experimental':True})
        write(out/'request.json',{'input':str(input_path),'preparation':str(out/'preparation-result.json'),'output':str(out)})
        env=workspace_environment();env.update(QT_QPA_PLATFORM='offscreen',GENLAW_CAM_REQUEST=str(out/'request.json'))
        bounded(out,'freecad',[FREECAD,'freecadcmd',ROOT/'genlaw_pipeline/freecad_worker.py'],env)
        cam=json.loads((out/'cam-result.json').read_text())
        if cam['status']!='generated_and_checked':raise ValueError('Native CAM failed: '+str(cam.get('error')))
        verify_binding(out/'program.nc',cam['nc_sha256']);check_nc((out/'program.nc').read_text(),plan)
        write(out/'simulation.camotics',config)
        cell_plan=inspect_and_plan(out/'simulation.camotics',engine,out/'windows')
        native_plan=json.loads(cell_plan.read_text())
        if native_plan['windows']!=windows or native_plan['grid']!=grid:
            raise ValueError('Native window plan differs from pre-CAM resource preflight')
        for i in range(len(windows)):
            directory=cell_plan.parent/f'window-{i:03d}';run_window(cell_plan,i,directory)
            bounded(directory,'postprocess',[sys.executable,ROOT/'tools/postprocess_window.py',
                '--plan',cell_plan,'--directory',directory])
            print(json.dumps({'original_size_window_completed':i+1,'windows':len(windows)}),flush=True)
        surface=out/'surface.json'
        bounded(out,'surface-audit',[sys.executable,ROOT/'genlaw_pipeline/sharded_surface.py','aggregate',
            '--plan',cell_plan,'--directory',cell_plan.parent,'--output',surface])
        measure_shards(out,surface,out/'native-geometry.json',cell_plan.parent,out/'metrics.json')
        verify_binding(out/'program.nc',cam['nc_sha256']);checked_plan(input_path,out/'preparation-result.json')
        metrics=json.loads((out/'metrics.json').read_text())
        record.update(status='original_size_global_geometry_measured',surface=file_binding(surface),
            metrics=file_binding(out/'metrics.json'),iou_percent=metrics['iou_percent'],
            nc=file_binding(out/'program.nc'),project=file_binding(out/'simulation.camotics'),
            native_cad_binding=file_binding(out/'native-geometry.json'),window_count=len(windows),
            process_checkpoint_verification='pending_actual_prefix_windows',feature_acceptance='pending_large_mesh_observations',
            display_and_replay='pending_actual_mesh_display_export')
    except Exception as error:
        record.update(status='failed',error=str(error))
        raise
    finally:
        write(out/'run-result.json',record)
        print(json.dumps({'status':record['status'],'error':record.get('error'),'iou_percent':record.get('iou_percent')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('input','output','engine','regression'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();run(a.input,a.output,a.engine,a.regression)
