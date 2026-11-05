"""Independent checks for the declared absolute-XYZ milling NC subset.

This is a guard for the reference postprocessor, not a replacement for CAMotics'
interpreter. Arcs retain I/J semantics; only planar G17 arcs are supported.
"""
import math
from .pocket_geometry import profile,CAPABILITY

EPS=5.1e-6


def arc_points(start,end,code,words,chord_tolerance=1e-5):
    if abs(end[2]-start[2])>EPS:raise ValueError('Helical interpolation is not declared')
    if 'R' in words or 'K' in words or 'I' not in words or 'J' not in words:raise ValueError('Need explicit incremental I/J arc center')
    cx,cy=start[0]+words['I'],start[1]+words['J'];a=math.atan2(start[1]-cy,start[0]-cx);b=math.atan2(end[1]-cy,end[0]-cx)
    radius=math.hypot(start[0]-cx,start[1]-cy);radius_end=math.hypot(end[0]-cx,end[1]-cy)
    if radius<=EPS or abs(radius-radius_end)>3e-5:raise ValueError('Arc center/radius mismatch after five-decimal output')
    sweep=(b-a)%(2*math.pi) if code==3 else -((a-b)%(2*math.pi))
    if abs(sweep)<1e-10:sweep=2*math.pi if code==3 else -2*math.pi
    max_angle=2*math.acos(max(-1.,1-chord_tolerance/radius));count=max(2,math.ceil(abs(sweep)/max_angle))
    if count>8192:raise ValueError('Arc sampling budget exceeded')
    # Include cardinal extrema so envelope checks do not miss a short bulge.
    fractions=[i/count for i in range(count+1)]
    for angle in [0,math.pi/2,math.pi,3*math.pi/2]:
        travel=(angle-a)%(2*math.pi) if code==3 else -((a-angle)%(2*math.pi))
        if 0<travel/sweep<1:fractions.append(travel/sweep)
    points=[(cx+radius*math.cos(a+sweep*f),cy+radius*math.sin(a+sweep*f)) for f in sorted(fractions)]
    points[0]=tuple(start[:2]);points[-1]=tuple(end[:2])
    return points,abs(sweep)*radius


def check_nc(text,plan):
    from .programming import nc_words
    from shapely.geometry import Point,LineString
    from shapely.ops import unary_union
    if plan.get('capability')!=CAPABILITY:raise ValueError('Unsupported milling capability')
    if len(text)>1024*1024:raise ValueError('NC text budget exceeded')
    blocks=nc_words(text)
    if len(blocks)>20000:raise ValueError('NC block budget exceeded')
    p=plan['parameters'];op=plan['operations'][0];f=op['geometry'];top=plan['stock']['bounds_mm']['max'][2];floor=op['final_z_mm'];tol=2*p['path_tolerance_mm']+2e-5
    prefix=[[('G',17),('G',54),('G',40),('G',49),('G',80),('G',90)],[('G',21)],[('M',5)],[('M',6),('T',1)],[('M',3),('S',p['spindle_rpm'])]]
    suffix=[[('M',5)],[('G',17),('G',54),('G',90),('G',80),('G',40)],[('M',2)]]
    if len(blocks)<=len(prefix)+len(suffix) or blocks[:5]!=prefix or blocks[-3:]!=suffix:raise ValueError('Milling header/tool/mode or shutdown differs from declared contract')
    allowed=profile(f,1024,inset=plan['tool']['diameter_mm']/2).buffer(tol)
    position=[None,None,None];levels=[];layer_paths={};seconds=0.;cutting_moves=0;arc_count=0;trace=[];deepest=top
    def uncovered_at(z):
        paths=layer_paths.get(round(z,5),[])
        if not paths:raise ValueError('No clearing path at a declared cutting layer')
        swept=unary_union(paths).buffer(plan['tool']['diameter_mm']/2,quad_segs=256)
        return profile(f,1024).difference(swept.buffer(tol)).area
    for index,block in enumerate(blocks[5:-3],6):
        if len({k for k,_ in block})!=len(block):raise ValueError('Duplicate NC word')
        words=dict(block);code=words.get('G')
        if code not in (0,1,2,3) or set(words)-set('GXYZFIJ'):raise ValueError('Unknown milling instruction or modal change')
        if code in (0,1) and ('I' in words or 'J' in words):raise ValueError('Arc words on a non-arc move')
        end=[words.get(axis,value) for axis,value in zip('XYZ',position)]
        if end[2] is None or end[2]<floor-EPS or end[2]>p['clearance_height_mm']+EPS:raise ValueError('Z outside declared milling depth or clearance')
        if code==0:
            if 'F' in words:raise ValueError('Rapid feed override is not declared')
            lateral=any(axis in words and (position[i] is None or abs(end[i]-position[i])>EPS) for i,axis in enumerate('XY'))
            if lateral and (position[2] is None or min(position[2],end[2])<p['safe_height_mm']-EPS):raise ValueError('Lateral rapid below safe plane')
            if end[2]<p['safe_height_mm']-EPS:raise ValueError('Rapid descent into material')
        else:
            if any(x is None for x in position+end):raise ValueError('Feed move before XYZ initialization')
            if 'F' not in words:raise ValueError('Explicit feed required on every reference cutting block')
            horizontal=math.dist(position[:2],end[:2])>EPS or code in (2,3);vertical=abs(end[2]-position[2])>EPS
            if horizontal and vertical:raise ValueError('Ramping / helical entry is not declared')
            expected_feed=p['feed_mm_min'] if horizontal else p['plunge_mm_min']
            if abs(words['F']-expected_feed)>EPS:raise ValueError('Feed does not match resource preset')
            if code in (2,3):points,length=arc_points(position,end,code,words);arc_count+=1
            else:points=[tuple(position[:2]),tuple(end[:2])];length=math.dist(position,end)
            if min(position[2],end[2])<top-EPS:
                centerline=LineString(points) if horizontal else Point(end[:2])
                if not allowed.covers(centerline):raise ValueError('Cutter center leaves the declared pocket envelope')
                if end[2]<deepest-EPS:
                    if deepest-end[2]>p['step_down_mm']+EPS:raise ValueError('Actual descent exceeds declared step-down')
                    if deepest<top-EPS and uncovered_at(deepest)>1e-8:raise ValueError('Previous layer was not cleared before deeper cutting')
                    deepest=end[2]
                cutting_moves+=1
                if horizontal:
                    levels.append(end[2]);layer_paths.setdefault(round(end[2],5),[]).append(centerline)
            seconds+=length/words['F']*60
        trace.append({'block':index,'motion':'G'+str(int(code)),'start':list(position),'end':list(end),'feed_mm_min':words.get('F'),'feed_time_end_seconds':seconds})
        position=end
    if abs(position[2]-p['clearance_height_mm'])>EPS:raise ValueError('Final retract missing')
    unique=sorted(set(round(z,5) for z in levels),reverse=True)
    if not unique or abs(unique[-1]-floor)>EPS:raise ValueError('Final milling depth missing')
    for a,b in zip([top,*unique],unique):
        if a-b>p['step_down_mm']+EPS:raise ValueError('Depth increments exceed declared step-down')
    uncovered=uncovered_at(floor)
    if uncovered>1e-8:raise ValueError('Declared floor not covered by final-depth tool sweeps')
    return {'status':'checked_declared_milling_envelope','executable_blocks':len(blocks),'post_decimal_places':5,'text_modified':False,
      'scope':'G17/G54 absolute XYZ, incremental I/J planar arcs, one flat endmill; bounded envelope/depth/feed/coverage checks, not a general NC interpreter.',
      'step_map':[{'step_id':op['step_id'],'feature_id':op['feature_id'],'first_executable_block':6,'last_executable_block':len(blocks)-3}],
      'cutting_moves':cutting_moves,'arc_moves':arc_count,'cutting_levels_mm':unique,'path_envelope_allowance_mm':tol,
      'feed_time_seconds':seconds,'rapid_time_not_included':True,'motion_trace':trace,'final_uncovered_profile_mm2':uncovered}
