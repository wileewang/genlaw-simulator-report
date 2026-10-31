"""Guard a native single-pass four-edge chamfer; never generate or rewrite NC."""
import math
from .chamfer_geometry import CAPABILITY
from .milling_nc import arc_points,EPS
from .sequence_nc import native_header,segments


def check_nc(text,plan):
    from shapely.geometry import box,LineString,Point
    if (plan.get('schema'),plan.get('capability'))!=('genlaw.execution-plan.v14',CAPABILITY):
        raise ValueError('Unsupported chamfer NC contract')
    items,blocks=segments(text,native_header(plan))
    if len(items)!=2 or [i['label'] for i in items]!=['TC Chamfer T1','ChamferEdges']:
        raise ValueError('Expected exactly one declared tool and one chamfer operation')
    p=plan['parameters'];tool,operation=items;op=plan['operations'][0]
    if tool['blocks']!=[[('M',5)],[('M',6),('T',1)],[('M',3),('S',p['spindle_rpm'])]]:
        raise ValueError('Chamfer tool or spindle differs from the card')
    low,high=[plan['target_bounds_mm'][k] for k in ['min','max']]
    final=op['final_z_mm'];offset=plan['tool']['tip_diameter_mm']/2+p['extra_depth_mm']
    if abs(final-(high[2]-op['geometry']['width_mm']-p['extra_depth_mm']))>EPS:
        raise ValueError('Chamfer depth inconsistent with geometry and tool')
    rectangle=box(*low[:2],*high[:2]);tol=2*p['path_tolerance_mm']+2e-5
    boundary=rectangle.buffer(offset,quad_segs=512).boundary
    allowed=boundary.buffer(tol)
    perimeter=2*(high[0]-low[0]+high[1]-low[1])+2*math.pi*offset
    position=[None,None,p['clearance_height_mm']];phase='approach';trace=[]
    first=None;last=None;length=0.;elapsed=0.;lines=0;arcs=0;plunges=0
    for index,block in enumerate(operation['blocks'],operation['first_executable_block']):
        if len({k for k,v in block})!=len(block):raise ValueError('Duplicate NC word')
        words=dict(block);code=words.get('G');end=[words.get(a,v) for a,v in zip('XYZ',position)]
        if code not in (0,1,2) or set(words)-set('GXYZFIJ') or not any(a in words for a in 'XYZ'):
            raise ValueError('Unsupported chamfer move or modal change')
        if code!=2 and ('I' in words or 'J' in words):raise ValueError('Arc words on a non-arc move')
        if end[2] is None or not final-EPS<=end[2]<=p['clearance_height_mm']+EPS:
            raise ValueError('Chamfer Z leaves declared depth/clearance')
        lateral=code==2 or any(a in words and (position[i] is None or abs(end[i]-position[i])>EPS) for i,a in enumerate('XY'))
        vertical=abs(end[2]-position[2])>EPS
        if code==0:
            if 'F' in words:raise ValueError('Rapid feed override is not declared')
            if lateral and min(position[2],end[2])<p['safe_height_mm']-EPS:
                raise ValueError('Lateral rapid below safe plane')
            if end[2]<p['safe_height_mm']-EPS:raise ValueError('Rapid descent below safe plane')
            if phase=='cut':
                if lateral or first is None or math.dist(first,last)>EPS or abs(length-perimeter)>16*p['path_tolerance_mm']+1e-4:
                    raise ValueError('Retract before one complete closed chamfer perimeter')
                phase='done'
        else:
            if phase=='done' or any(v is None for v in position+end):raise ValueError('Unplanned cutting move or uninitialized XYZ')
            if 'F' not in words or lateral==vertical:raise ValueError('Explicit feed and one planar/vertical motion required')
            expected_feed=p['feed_mm_min'] if lateral else p['plunge_mm_min']
            if abs(words['F']-expected_feed)>EPS:raise ValueError('Feed differs from resource preset')
            if abs(end[2]-final)>EPS:raise ValueError('Every chamfer cutting move must end at the declared depth')
            if not lateral:
                if phase!='approach' or code!=1 or position[2]<p['safe_height_mm']-EPS or not allowed.covers(Point(end[:2])):
                    raise ValueError('Expected one outside vertical entry from the safe plane')
                phase='cut';plunges+=1;distance=abs(end[2]-position[2])
            else:
                if phase!='cut' or abs(position[2]-final)>EPS:raise ValueError('Unexpected cutting level')
                if code==2:
                    points,distance=arc_points(position,end,code,words)
                    center=[position[0]+words['I'],position[1]+words['J']]
                    radius=math.dist(position[:2],center)
                    corners=[(x,y) for x in [low[0],high[0]] for y in [low[1],high[1]]]
                    if min(math.dist(center,corner) for corner in corners)>tol or abs(radius-offset)>tol or abs(distance-math.pi*offset/2)>4*tol:
                        raise ValueError('Chamfer corner must be a declared CW quarter-circle offset')
                    arcs+=1
                else:
                    if all(abs(end[i]-position[i])>EPS for i in (0,1)):raise ValueError('Diagonal straight chamfer path')
                    points=[position[:2],end[:2]];distance=math.dist(position[:2],end[:2]);lines+=1
                if not allowed.covers(LineString(points)):raise ValueError('Chamfer cutter center leaves the original perimeter offset')
                if first is None:first=list(position[:2])
                last=list(end[:2]);length+=distance
            elapsed+=60*distance/words['F']
        trace.append({'block':index,'motion':'G'+str(int(code)),'start':list(position),'end':list(end),
                      'feed_mm_min':words.get('F'),'feed_time_end_seconds':elapsed,
                      **({'i':words['I'],'j':words['J']} if code==2 else {})})
        position=end
    if (phase!='done' or plunges!=1 or lines!=4 or arcs!=4 or abs(position[2]-p['clearance_height_mm'])>EPS
        or abs(length-perimeter)>16*p['path_tolerance_mm']+1e-4):raise ValueError('Incomplete or additional four-edge chamfer path')
    return {'status':'checked_native_chamfer','original_nc_modified':False,'executable_blocks':len(blocks),
        'step_map':[{'step_id':op['step_id'],'feature_id':op['feature_id'],
            'first_executable_block':operation['first_executable_block'],'last_executable_block':operation['last_executable_block']}],
        'motion_trace':trace,'feed_time_seconds':elapsed,'rapid_time_not_included':True,
        'closed_perimeter_length_mm':length,'offset_mm':offset,'final_tip_z_mm':final,'straight_edges':lines,'corner_arcs':arcs,
        'initial_machine_state':plan['setup']['startup'],'unloaded_clearance_before_first_tool_selection_checked':True,
        'scope':'One complete rectangular four-edge 45-degree chamfer, fixed Z, finite 90-degree frustum; no general NC interpreter.'}
