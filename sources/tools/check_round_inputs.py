"""Bind all four archived inputs and reject resource/CAD/plan inconsistencies."""
import argparse
import copy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.round_archive import checked
from genlaw_pipeline.round_surface_preparation import prepare
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker


def run(inputs,runs,out):
    require_bounded_worker();out.mkdir(parents=True,exist_ok=False);archive=file_binding(inputs/'manifest.json')
    normals=[]
    for key in ['baseline','larger-radius','longer','shifted-datum']:
        plan,receipt=checked(inputs/key/'input.json',archive);saved=json.loads((runs/key/'preparation-result.json').read_text())
        if plan!=saved:raise ValueError('Archive admission changed the executed plan')
        write(out/(key+'-admission.json'),receipt);normals.append({'id':key,'receipt':file_binding(out/(key+'-admission.json')),
            'executed_preparation':file_binding(runs/key/'preparation-result.json'),'plans_identical':True})
    original=json.loads((inputs/'baseline/input.json').read_text());rejections=[]
    tests=[('wrong_stock',lambda s:s['stock']['min'].__setitem__(0,.1)),
        ('wrong_radius',lambda s:s.__setitem__('radius_mm',2.2)),
        ('missing_round',lambda s:s.__setitem__('round_edges',['top_x_parallel_ymin'])),
        ('flat_tool',lambda s:s['tool'].__setitem__('kind','cylindrical')),
        ('oversize_ball',lambda s:s['tool'].__setitem__('diameter_mm',4.)),
        ('short_cutting_length',lambda s:s['tool'].__setitem__('cutting_length_mm',.4)),
        ('foreign_tool_number',lambda s:s['tool'].__setitem__('id','T2')),
        ('not_center_cutting',lambda s:s['tool'].__setitem__('center_cutting',False)),
        ('rotary_scan',lambda s:s['cam'].__setitem__('scan_type','Rotational')),
        ('single_pass',lambda s:s['cam'].__setitem__('layer_mode','Single-pass')),
        ('wrong_start',lambda s:s['cam'].__setitem__('start_depth_mm',-.1)),
        ('wrong_end',lambda s:s['cam'].__setitem__('final_depth_mm',-2.)),
        ('insufficient_boundary_parameter',lambda s:s['cam'].__setitem__('boundary_adjustment_mm',.4)),
        ('unsafe_clearance',lambda s:s['cam'].__setitem__('clearance_height_mm',1.)),
        ('zero_stepdown',lambda s:s['cam'].__setitem__('step_down_mm',0.)),
        ('excess_stepover',lambda s:s['cam'].__setitem__('stepover_percent',101.)),
        ('zero_sampling',lambda s:s['cam'].__setitem__('sample_interval_mm',0.)),
        ('coarser_simulation',lambda s:s.__setitem__('resolution_mm',.1)),
        ('zero_spindle',lambda s:s['cam'].__setitem__('spindle_rpm',0.))]
    for key,mutation in tests:
        value=copy.deepcopy(original);mutation(value['specification']);path=out/(key+'.json');write(path,value)
        try:prepare(path)
        except (ValueError,KeyError,TypeError) as error:
            rejections.append({'id':key,'level':'resource_and_geometry_preparation','reason':str(error),'input':file_binding(path)})
        else:raise ValueError('Preparation accepted '+key)
    for key in ['unlisted_input','foreign_paper','altered_feed']:
        value=copy.deepcopy(original)
        if key=='foreign_paper':value['paper_card']=file_binding(inputs/'larger-radius/process-card.pdf')
        if key=='altered_feed':value['specification']['cam']['feed_mm_min']=200.
        path=out/(key+'.json');write(path,value)
        try:checked(path,archive)
        except ValueError as error:rejections.append({'id':key,'level':'reviewed_archive_admission','reason':str(error),'input':file_binding(path)})
        else:raise ValueError('Archive admission accepted '+key)
    bad=copy.deepcopy(archive);bad['sha256']='0'*64
    try:checked(inputs/'baseline/input.json',bad)
    except ValueError as error:rejections.append({'id':'changed_archive','level':'reviewed_archive_admission','reason':str(error)})
    else:raise ValueError('Changed archive was accepted')
    write(out/'result.json',{'status':'passed','normal_inputs':normals,'rejections':rejections,
        'new_FreeCAD_or_material_executions':0,'source_code':[file_binding(Path(__file__)),
            file_binding(ROOT/'genlaw_pipeline/round_archive.py'),file_binding(ROOT/'genlaw_pipeline/round_surface_preparation.py')],
        'arbitrary_production_PDF_parsing_qualified':False,'manufacturing_approval':False})
    print(json.dumps({'normal_inputs':len(normals),'input_rejections':len(rejections)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['inputs','runs','output']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();run(a.inputs.resolve(),a.runs.resolve(),a.output.resolve())
