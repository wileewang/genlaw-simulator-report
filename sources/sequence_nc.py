"""Check every word of the native ordered post, including tool transitions.

Native comments delimit segments, but never establish geometric correctness:
every segment is independently checked against its feature, cutter and material
precondition. Only in-memory guard projections use a single-tool wrapper; the
native NC file itself is never rewritten.
"""
import copy
import re
from .sequence_geometry import CAPABILITY, child_plan, label
from .profile_sequence_geometry import CAPABILITY as PROFILED
from .pocket_sequence_geometry import CAPABILITY as POCKETED
from .coordinate_setup import enabled, validate_setup, program_view, trace_to_cad

HEADER='G17 G54 G40 G49 G80 G90\nG21\n'
SHUTDOWN='M5\nG17 G54 G90 G80 G40\nM2\n'


def native_header(plan):
    if enabled(plan):
        setup=plan['setup'];validate_setup(setup,plan['stock']['bounds_mm'])
        z=setup['startup']['retract_before_tool_selection_z_mm']
        return f'G17 {setup["work_offset"]} G40 G49 G80 G90\nG21\nM5\nG53 G0 Z{z:.5f}\nG21\n'
    startup=plan['setup']['startup']
    if startup['position_mm']!=[0,0,0] or startup['tool']!='unloaded' or startup['spindle']!='stopped':raise ValueError('Unsupported initial state')
    z=startup['retract_before_tool_selection_z_mm']
    if z<=plan['stock']['bounds_mm']['max'][2]:raise ValueError('Initial clearance intersects stock')
    # linuxcnc_post appends its units block after the user-supplied preamble.
    return HEADER+f'M5\nG0 Z{z:g}\nG21\n'


def native_shutdown(plan):
    return f'M5\nG17 {plan["setup"]["work_offset"]} G90 G80 G40\nM2\n' if enabled(plan) else SHUTDOWN


def segments(text,header,shutdown=SHUTDOWN):
    from .programming import nc_words
    if len(text)>2*1024**2:raise ValueError('Ordered NC text budget exceeded')
    total=nc_words(text)
    if len(total)>40000:raise ValueError('Ordered NC block budget exceeded')
    prefix=[];tail=[];items=[];active=None;index=0
    for raw in text.splitlines():
        line=raw.strip();start=re.fullmatch(r'\(begin operation: ([^()]+)\)',line)
        end=re.fullmatch(r'\(finish operation: ([^()]+)\)',line)
        if start:
            if active is not None or tail:raise ValueError('Nested operation or executable words outside framed operations')
            active={'label':start[1],'first_executable_block':index+1,'lines':[],'blocks':[]}
        elif end:
            if active is None or end[1]!=active['label']:raise ValueError('Mismatched operation delimiter')
            active['last_executable_block']=index;items.append(active);active=None
        else:
            if 'begin operation:' in line or 'finish operation:' in line:raise ValueError('Invalid operation delimiter')
            words=nc_words(raw);index+=len(words)
            if active is not None:active['lines'].append(raw);active['blocks'].extend(words)
            elif items:tail.extend(words)
            else:prefix.extend(words)
    if active is not None or not items:raise ValueError('Unfinished or missing native operations')
    if prefix!=nc_words(header) or tail!=nc_words(shutdown):raise ValueError('Ordered NC preamble, unloaded startup retract or shutdown differs')
    return items,total


def check_nc(text,plan):
    from .programming import nc_words
    from .facing_nc import check_nc as check_face
    from .helix_nc import check_nc as check_hole
    if (plan.get('capability'),plan.get('schema')) not in ((CAPABILITY,'genlaw.execution-plan.v9'),(PROFILED,'genlaw.execution-plan.v11'),(POCKETED,'genlaw.execution-plan.v12')):raise ValueError('Unsupported ordered NC contract')
    header=native_header(plan);shutdown=native_shutdown(plan);items,blocks=segments(text,header,shutdown);cursor=0;active_tool=None;checks=[];trace=[];mapping=[];tools=[];elapsed=0.
    work=program_view(plan)
    for i,op in enumerate(work['operations']):
        if (i==0)!=(op['type']=='face_milling'):raise ValueError('Material precondition requires facing before holes')
        if op['material_before_id']!=('raw' if i==0 else f'after-{i:02d}') or op['material_after_id']!=f'after-{i+1:02d}':
            raise ValueError('Broken material-state chain')
        n=op['tool_number'];p=op['parameters'];child=child_plan(work,op)
        if child['tool']['number']!=n:raise ValueError('Tool ID / NC number mismatch')
        if active_tool!=n:
            if cursor>=len(items):raise ValueError('Missing tool change')
            tc=items[cursor];cursor+=1
            wanted=[[('M',5)],[('M',6),('T',n)],[('M',3),('S',p['spindle_rpm'])]]
            if tc['label']!=f'TC_{n:02d}' or tc['blocks']!=wanted:raise ValueError('Native tool change or spindle differs from the card')
            active_tool=n;tools.append({'tool_number':n,'tool_id':op['tool_id'],'first_executable_block':tc['first_executable_block'],'last_executable_block':tc['last_executable_block']})
        if cursor>=len(items):raise ValueError('Missing machining operation')
        part=items[cursor];cursor+=1
        if part['label']!=label(i,op) or not part['blocks']:raise ValueError('Native operation order / feature association differs from the card')
        body='\n'.join(part['lines'])+'\n'
        wrapper=HEADER+f'M5\nM6 T1\nM3 S{p["spindle_rpm"]:g}\n'+body+SHUTDOWN
        if op['geometry']['kind']=='capsule_outline':
            from .profile_nc import check_nc as check_profile
            wrapper=native_header(child)+f'(begin operation: TC Profile T1)\nM5\nM6 T1\nM3 S{p["spindle_rpm"]:g}\n(finish operation: TC Profile T1)\n(begin operation: Profile)\n'+body+'(finish operation: Profile)\n'+SHUTDOWN
            check=check_profile(wrapper,child)
            shift=part['first_executable_block']-check['step_map'][0]['first_executable_block']
        else:
            from .pocket_nc import check_nc as check_pocket
            check=(check_face if i==0 else check_pocket if op['geometry']['kind']=='rounded_rectangular_pocket' else check_hole)(wrapper,child)
            shift=part['first_executable_block']-6
        for point in check['motion_trace']:
            point=trace_to_cad(point,plan)
            point={**point,'block':point['block']+shift,'feed_time_end_seconds':point['feed_time_end_seconds']+elapsed,
                   'step_id':op['step_id'],'feature_id':op['feature_id'],'tool_number':n}
            # A child starts with explicit clearance initialization; preserve the
            # previous actual position only for display, never to relax its guard.
            if trace:
                point['start']=[a if a is not None else b for a,b in zip(point['start'],trace[-1]['end'])]
                point['end']=[a if a is not None else b for a,b in zip(point['end'],point['start'])]
            trace.append(point)
        elapsed+=check['feed_time_seconds']
        summary={k:v for k,v in check.items() if k not in ('motion_trace','step_map','radial_passes')}
        summary.update(step_id=op['step_id'],feature_id=op['feature_id'],tool_number=n,actual_block_offset=shift,
            guard_projection='Single-operation verification wrapper only; original native NC unchanged.')
        checks.append(summary);mapping.append({'step_id':op['step_id'],'feature_id':op['feature_id'],'tool_number':n,
            'first_executable_block':part['first_executable_block'],'last_executable_block':part['last_executable_block'],
            'material_before_id':op['material_before_id'],'material_after_id':op['material_after_id']})
    if cursor!=len(items):raise ValueError('Additional unplanned native operation')
    if sum(len(item['blocks']) for item in items)+len(nc_words(header))+len(nc_words(shutdown))!=len(blocks):
        raise ValueError('Unaccounted executable words')
    return {'status':'checked_ordered_native_milling','text_modified':False,'executable_blocks':len(blocks),
        'step_map':mapping,'tool_transitions':tools,'operation_checks':checks,'motion_trace':trace,
        'feed_time_seconds':elapsed,'rapid_time_not_included':True,'card_order_checked':True,
        'startup':plan['setup']['startup'],'unloaded_clearance_before_first_tool_selection_checked':True,
        **({'motion_trace_frame':'original_cad','program_to_cad_translation_mm':plan['setup']['work_origin_cad_mm'],
            'initial_retract_frame':'machine_G53','geometric_guard_frame':'program_work_coordinates'} if enabled(plan) else {}),
        'scope':'Native framed face/pocket/helix XYZ reference subset; every executable word checked, no general NC interpreter.' if plan['capability']==POCKETED else 'Native framed face/profile/helix XYZ reference subset; every executable word checked, no general NC interpreter.' if plan['capability']==PROFILED else 'Native framed face/helix XYZ reference subset; every executable word checked, no general NC interpreter.',
        'material_preconditions':'Expected face clearing is checked geometrically; actual cumulative-prefix checkpoints are measured separately.'}


def check_prefix(prefix,full,plan,completed_count):
    """Prove a separate native export has the same executable prefix, then stops."""
    from .programming import nc_words
    if not 1<=completed_count<len(plan['operations']):raise ValueError('Not an intermediate operation prefix')
    full_check=check_nc(full,plan);partial=copy.deepcopy(plan);partial['operations']=partial['operations'][:completed_count]
    checked=check_nc(prefix,partial);last=full_check['step_map'][completed_count-1]['last_executable_block']
    if nc_words(prefix)!=nc_words(full)[:last]+nc_words(native_shutdown(plan)):raise ValueError('Native checkpoint export differs from the full NC prefix')
    return {'status':'identical_executable_prefix_then_standard_shutdown','completed_operations':completed_count,
        'full_nc_last_included_executable_block':last,'prefix_executable_blocks':checked['executable_blocks'],
        'postprocessor_exported':True,'nc_text_manually_spliced':False,
        'time_cutoff_used':False,'stock_reset_between_selected_operations':False}
