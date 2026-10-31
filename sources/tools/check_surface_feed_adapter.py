"""Exercise native Path feed units, geometry preservation and NC clearance guards."""
import argparse
import copy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.surface_feed_adapter import adapt
from genlaw_pipeline.round_surface_nc import check_nc
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker


def check(run,out):
    require_bounded_worker();plan=json.loads((run/'preparation-result.json').read_text())
    original=json.loads((run/'native-path.json').read_text())['commands']
    frozen=copy.deepcopy(original);prepared,report=adapt(original,[0,0,plan['parameters']['clearance_height_mm']],120,60)
    assert original==frozen and len(prepared)==len(original) and len(report['changes'])>0
    assert all(r['from_mm_min']==120 and r['to_mm_min']==60 for r in report['changes'])
    assert all({k:v for k,v in a['parameters'].items() if k!='F'}=={k:v for k,v in b['parameters'].items() if k!='F'}
               and a['name']==b['name'] for a,b in zip(original,prepared))
    rejected=[]
    for name,commands in [('unsupported_arc',[{'name':'G2','parameters':{'X':1.,'F':2.}}]),
        ('comment_injection',[{'name':'(safe) G1 X100','parameters':{}}]),
        ('foreign_feed',[{'name':'G1','parameters':{'Z':-1.,'F':10.}}]),
        ('nonfinite_coordinate',[{'name':'G1','parameters':{'Z':float('nan'),'F':2.}}]),
        ('unexpected_axis',[{'name':'G1','parameters':{'A':1.,'F':2.}}])]:
        try:adapt(commands,[0,0,3],120,60)
        except ValueError:rejected.append(name)
        else:raise ValueError('Adapter accepted '+name)
    nc=(run/'program.nc').read_text();check_nc(nc,plan)
    # These are in-memory guard inputs, never sent to a machine or simulator.
    changes=[('excess_plunge_feed',nc.replace('F60.00000','F120.00000',1)),
             ('rapid_plunge_into_stock',nc.replace('G1 X3.04900 Y0.00000 Z-0.25000 F60.00000','G0 X3.04900 Y0.00000 Z-0.25000',1)),
             ('foreign_axis',nc.replace('F60.00000','F60.00000 A1',1))]
    for name,text in changes:
        if text==nc:raise ValueError('Control fixture did not mutate expected statement')
        try:check_nc(text,plan)
        except ValueError:rejected.append(name)
        else:raise ValueError('NC guard accepted '+name)
    write(out,{'status':'passed','recorded_native_path':file_binding(run/'native-path.json'),
        'original_nc':file_binding(run/'program.nc'),'native_commands_checked':len(original),
        'feed_changes':len(report['changes']),'geometric_parameters_and_order_equal':True,
        'rejected_controls':rejected,'source_code':[file_binding(ROOT/'genlaw_pipeline/surface_feed_adapter.py'),
            file_binding(ROOT/'genlaw_pipeline/round_surface_nc.py'),file_binding(Path(__file__))],
        'geometry_or_manufacturing_qualification':False})
    print(json.dumps({'passed':True,'rejections':len(rejected)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();check(a.run.resolve(),a.output.resolve())
