"""Check the original Surface post, all XYZ moves and stock clearance of rapids."""
import math
from .sequence_nc import native_header,segments


def check_nc(text,plan):
    from shapely.geometry import Point,LineString,box
    if plan['schema']!='genlaw.round-surface-execution-plan.v1':raise ValueError('Unsupported Surface NC plan')
    items,blocks=segments(text,native_header(plan));p=plan['parameters'];lo,hi=[plan['stock']['bounds_mm'][k] for k in ['min','max']]
    if len(items)!=2 or [i['label'] for i in items]!=['TC Ball T1','RoundSurface']:
        raise ValueError('Require one native ball tool and one Surface operation')
    if items[0]['blocks']!=[[('M',5)],[('M',6),('T',1)],[('M',3),('S',p['spindle_rpm'])]]:
        raise ValueError('Unexpected tool selection or spindle')
    radius=plan['tool']['diameter_mm']/2;rect=box(*lo[:2],*hi[:2]);margin=p['boundary_adjustment_mm']+2*radius+.01
    position=[0.,0.,p['clearance_height_mm']];trace=[];feed_count=0;rapid_clear=0;retractions=0;feed_time=0.
    for index,block in enumerate(items[1]['blocks'],items[1]['first_executable_block']):
        if len({k for k,v in block})!=len(block):raise ValueError('Duplicate NC word')
        words=dict(block);code=words.get('G');end=[words.get(a,v) for a,v in zip('XYZ',position)]
        if code not in (0,1) or set(words)-set('GXYZF') or not any(a in words for a in 'XYZ'):
            raise ValueError('Only explicit native G0/G1 XYZ Surface motion is supported')
        in_air=min(position[2],end[2])>=hi[2]-1e-5
        xy_low=[min(0,lo[i]-margin) if in_air else lo[i]-margin for i in [0,1]]
        xy_high=[max(0,hi[i]+margin) if in_air else hi[i]+margin for i in [0,1]]
        if not lo[2]-1e-5<=end[2]<=p['clearance_height_mm']+1e-5 or any(not xy_low[i]<=end[i]<=xy_high[i] for i in [0,1]):
            raise ValueError('NC exceeds the predeclared Surface cutting envelope')
        length=math.dist(position,end);lateral=any(abs(end[i]-position[i])>1e-8 for i in [0,1])
        if code==0:
            if 'F' in words:raise ValueError('Unexpected rapid feed override')
            line=LineString([position[:2],end[:2]]) if lateral else Point(end[:2])
            # A vertical lift from the immediately preceding feed remains in
            # the already swept ball/cylinder or above stock, provided that
            # the modeled cutting length originally reached above its top.
            retract=(not lateral and end[2]>=position[2] and trace and trace[-1]['motion']=='G1'
                and position[2]+plan['tool']['cutting_length_mm']>=hi[2]+1e-5)
            if not in_air and line.distance(rect)<=radius+1e-5 and not retract:
                raise ValueError('Rapid move can intersect original material')
            if retract and not in_air:retractions+=1
            rapid_clear+=1
        else:
            expected=p['feed_mm_min'] if lateral else p['plunge_mm_min']
            if 'F' not in words or abs(words['F']-expected)>1e-5:raise ValueError('Feed differs from explicit resource card')
            feed_count+=1;feed_time+=60*length/words['F']
        trace.append({'block':index,'motion':'G'+str(int(code)),'start':position,'end':end,
                      'feed_mm_min':words.get('F'),'feed_time_end_seconds':feed_time});position=end
    if not feed_count or abs(position[2]-p['clearance_height_mm'])>1e-5:raise ValueError('Incomplete Surface program')
    return {'status':'checked_native_surface_words_and_envelope','original_nc_modified':False,
        'executable_blocks':len(blocks),'feed_moves':feed_count,'validated_rapid_moves':rapid_clear,
        'vertical_retractions_with_previous_cutter_containment':retractions,
        'motion_trace':trace,'feed_time_seconds':feed_time,'geometric_result_not_inferred_from_NC_guard':True,
        'physical_fixture_clearance_checked':False,'continuous_rotary_motion':False}
