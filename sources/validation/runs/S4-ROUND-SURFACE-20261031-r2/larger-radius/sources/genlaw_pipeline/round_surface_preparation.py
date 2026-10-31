"""Explicit experimental card/CAD/resource contract for two external rounds."""
import json
import math
from pathlib import Path
from .cell_windows import checked_file,file_binding,expected_grid,window_budget
from .round_surface_geometry import RoundReference


def prepare(input_path):
    entry=json.loads(Path(input_path).read_text())
    if entry.get('schema')!='genlaw.round-surface-input.v1':raise ValueError('Unsupported round input')
    s=entry['specification'];ref=json.loads(checked_file(entry['CAD_reference']).read_text())
    checked_file(entry['CAD']);checked_file(entry['paper_card']);card=json.loads(checked_file(entry['process_plan']).read_text())
    if (ref['cad']!=entry['CAD'] or card['cad']!=entry['CAD']
            or any(abs(ref['bounds'][k][i]-s['stock'][k][i])>1e-8 for k in ['min','max'] for i in range(3))
            or ref['radius_mm']!=s['radius_mm'] or not ref['target_created_before_NC']
            or card['operations'][2]['radius_mm']!=s['radius_mm']
            or card['operations'][2]['edges']!=s['round_edges']
            or s['round_edges']!=['top_x_parallel_ymin','top_x_parallel_ymax']):
        raise ValueError('Paper plan, original CAD and declared rounds disagree')
    RoundReference(ref);t=s['tool'];p=s['cam']
    if (t['kind']!='ballnose' or t['id']!='T1' or t['center_cutting'] is not True
            or not 0<t['diameter_mm']<2*s['radius_mm']
            or not t['diameter_mm']/2<t['cutting_length_mm']<=t['length_mm']
            or p['native_operation']!='Surface' or p['scan_type']!='Planar' or p['pattern']!='Line'
            or p['layer_mode']!='Multi-pass' or p['cut_mode']!='Climb'):
        raise ValueError('Require the declared fixed-axis ball-end Surface operation')
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in [t['diameter_mm'],t['cutting_length_mm'],t['length_mm'],s['radius_mm'],s['resolution_mm']]):
        raise ValueError('Finite resource dimensions required')
    for key in ['step_down_mm','sample_interval_mm','linear_deflection_mm','geometry_tolerance_mm','spindle_rpm','feed_mm_min','plunge_mm_min']:
        if type(p[key]) not in (int,float) or not math.isfinite(p[key]) or p[key]<=0:raise ValueError('Positive finite CAM parameter required: '+key)
    lo,hi=[s['stock'][k] for k in ['min','max']]
    if (p['start_depth_mm']!=hi[2] or p['final_depth_mm']!=lo[2]
            or not hi[2]<p['safe_height_mm']<p['clearance_height_mm']
            or not 1<=p['stepover_percent']<=100 or p['boundary_adjustment_mm']<=t['diameter_mm']/2
            or not 0<s['resolution_mm']<=.025):raise ValueError('Unsupported explicit cutting envelope')
    # Native CAMotics ballnose = full sphere plus a cylinder starting one radius
    # above the tip. Declare the cylindrical portion so total cutting reach is 5 mm.
    project={'units':'metric','resolution-mode':'manual','resolution':s['resolution_mm'],
        'tools':{'1':{'units':'metric','shape':'ballnose','diameter':t['diameter_mm'],
                      'length':t['cutting_length_mm']-t['diameter_mm']/2}},
        'workpiece':{'automatic':False,'margin':0,'bounds':s['stock']},'files':['program.nc']}
    grid=expected_grid(project);window=[0,0,0,*grid['steps']];budget=window_budget(window,grid['steps'])
    if not budget['allowed']:raise ValueError('This probe requires one complete bounded material window')
    return {'schema':'genlaw.round-surface-execution-plan.v1','status':'prepared_for_native_probe',
        'input':file_binding(input_path),'input_bindings':{k:entry[k] for k in ['CAD','CAD_reference','process_plan','paper_card']},
        'specification':s,'stock':{'bounds_mm':s['stock']},'tool':t,'parameters':p,
        'setup':{'startup':{'position_mm':[0,0,0],'tool':'unloaded','spindle':'stopped',
                            'retract_before_tool_selection_z_mm':p['clearance_height_mm']}},
        'simulation_project':project,'grid':grid,'window':window,'budget':budget,
        'qualified_for_production':False,'whole_real_card_complete':False}
