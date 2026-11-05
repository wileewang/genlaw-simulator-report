"""Geometric guard for fixed-axis circular helical hole clearing.

Check complete bottom circles and the union of their radial cutting intervals;
do not infer full hole clearing just from an outer wall or an overall IoU.
"""
import math
from .milling_nc import EPS
from .helix_geometry import CAPABILITY


def check_nc(text,plan):
    from .programming import nc_words
    if plan.get('capability')!=CAPABILITY or len(plan['operations'])!=1:raise ValueError('Unsupported helical scope')
    blocks=nc_words(text)
    if len(text)>1024**2 or len(blocks)>20000:raise ValueError('NC budget exceeded')
    p=plan['parameters'];op=plan['operations'][0];cx,cy=op['hole_center_xy_mm']
    hole_radius=op['diameter_mm']/2;tool_radius=plan['tool']['diameter_mm']/2
    entry=op['start_z_mm'];floor=op['final_z_mm'];tol=2*p['path_tolerance_mm']+2e-5
    prefix=[[('G',17),('G',54),('G',40),('G',49),('G',80),('G',90)],[('G',21)],[('M',5)],[('M',6),('T',1)],[('M',3),('S',p['spindle_rpm'])]]
    suffix=[[('M',5)],[('G',17),('G',54),('G',90),('G',80),('G',40)],[('M',2)]]
    if blocks[:5]!=prefix or blocks[-3:]!=suffix:raise ValueError('Helix header, tool, modes or shutdown differ')
    position=[None,None,None];trace=[];passes=[];active=None;cleared=0.;seconds=0.;helices=0;arcs=0
    def finish():
        nonlocal active,cleared
        if active is None:return
        if active['bottom_angle_radians']<2*math.pi-1e-6 or not active['descending_arc_count']:
            raise ValueError('Need a descending helix and a complete final-depth circle')
        r=active['radius_mm'];inner=max(0,r-tool_radius)
        if inner>cleared+tol:raise ValueError('Uncut central material or a gap between radial passes')
        if passes and r-passes[-1]['radius_mm']>plan['tool']['diameter_mm']*p['step_over_percent']/100+tol:
            raise ValueError('Radial engagement exceeds the declared step-over')
        if passes and r<passes[-1]['radius_mm']-EPS:raise ValueError('Inside-first radial ordering changed')
        cleared=max(cleared,r+tool_radius);passes.append(active);active=None
    for index,block in enumerate(blocks[5:-3],6):
        if len({k for k,_ in block})!=len(block):raise ValueError('Duplicate NC word')
        words=dict(block);code=words.get('G')
        if code not in (0,1,3) or set(words)-set('GXYZFIJ'):raise ValueError('Unknown instruction or wrong helical cut direction')
        if code in (0,1) and any(k in words for k in ['I','J']):raise ValueError('Arc center on linear motion')
        end=[words.get(axis,v) for axis,v in zip('XYZ',position)]
        if end[2] is None or end[2]<floor-EPS or end[2]>p['clearance_height_mm']+EPS:raise ValueError('Z outside declared machining range')
        details={};length=0.
        if code==0:
            finish()
            if 'F' in words:raise ValueError('Rapid feed override is not declared')
            lateral=any(k in words and (position[i] is None or abs(end[i]-position[i])>EPS) for i,k in enumerate('XY'))
            if lateral and position[2] is None:raise ValueError('Lateral rapid before initialized Z')
            if lateral and min(position[2],end[2])<entry-EPS:
                if (abs(position[2]-end[2])>EPS or abs(end[2]-floor)>EPS or
                    any(v is None for v in position+end) or
                    max(math.hypot(v[0]-cx,v[1]-cy) for v in [position,end])+tool_radius>cleared+tol):
                    raise ValueError('Rapid inside material is outside already proven cleared volume')
            if position[2] is not None and end[2]<position[2]-EPS and end[2]<entry-EPS:
                raise ValueError('Rapid descent into stock')
        else:
            if any(v is None for v in position+end) or 'F' not in words:raise ValueError('Feed before initialized XYZ or without explicit feed')
            if code==1:
                if active is not None or math.dist(position[:2],end[:2])>EPS or abs(end[2]-entry)>EPS or position[2]<entry-EPS:
                    raise ValueError('Only a vertical approach to the declared air entry plane is allowed')
                if abs(words['F']-p['plunge_mm_min'])>EPS:raise ValueError('Entry feed differs')
                radius=math.hypot(end[0]-cx,end[1]-cy)
                if radius<=EPS or radius+tool_radius>hole_radius+tol:raise ValueError('Helix radius leaves target hole')
                active={'radius_mm':radius,'first_executable_block':index,'last_executable_block':index,
                        'bottom_angle_radians':0.,'descending_arc_count':0,'max_pitch_mm_per_turn':0.}
                length=math.dist(position,end)
            else:
                if active is None or 'I' not in words or 'J' not in words:raise ValueError('Arc without an explicit radial-pass entry or incremental center')
                if abs(words['F']-p['feed_mm_min'])>EPS:raise ValueError('Helical feed differs')
                center=[position[0]+words['I'],position[1]+words['J']]
                r=math.dist(position[:2],center);rend=math.dist(end[:2],center)
                if math.dist(center,[cx,cy])>3e-5 or abs(r-rend)>3e-5 or abs(r-active['radius_mm'])>3e-5:
                    raise ValueError('Arc center, radius or original hole position differs')
                a=math.atan2(position[1]-cy,position[0]-cx);b=math.atan2(end[1]-cy,end[0]-cx)
                angle=(b-a)%(2*math.pi)
                if angle<1e-10:angle=2*math.pi
                dz=end[2]-position[2]
                if dz>EPS:raise ValueError('Upward helical cutting is not declared')
                pitch=-dz*2*math.pi/angle
                if pitch>p['step_down_mm']+3e-5:raise ValueError('Helical pitch exceeds declared maximum')
                if abs(dz)<=EPS:
                    if abs(end[2]-floor)>EPS:raise ValueError('Planar finishing circle must be at declared final depth')
                    active['bottom_angle_radians']+=angle
                else:
                    if active['bottom_angle_radians']:raise ValueError('Descent after a finishing circle')
                    active['descending_arc_count']+=1;helices+=1
                active['max_pitch_mm_per_turn']=max(active['max_pitch_mm_per_turn'],pitch)
                active['last_executable_block']=index;arcs+=1;length=math.hypot(r*angle,dz)
                details={'arc_center_xy_mm':center,'radius_mm':r,'sweep_radians':angle,'helical_pitch_mm_per_turn':pitch}
            seconds+=length/words['F']*60
        trace.append({'block':index,'motion':'G'+str(int(code)),'start':list(position),'end':list(end),
                      'feed_mm_min':words.get('F'),'feed_time_end_seconds':seconds,**details})
        position=end
    finish()
    if abs(position[2]-p['clearance_height_mm'])>EPS:raise ValueError('Final clearance retract missing')
    if not passes or abs(cleared-hole_radius)>tol:raise ValueError('Final swept circles do not clear the complete target diameter')
    return {'status':'checked_declared_helical_hole','text_modified':False,'executable_blocks':len(blocks),
            'scope':'Fixed-axis G17/G54, incremental I/J, CCW helical clearing with explicit air entry and breakthrough; no general NC interpreter.',
            'step_map':[{'step_id':op['step_id'],'feature_id':op['feature_id'],'first_executable_block':6,'last_executable_block':len(blocks)-3}],
            'radial_passes':passes,'cleared_radius_mm':cleared,'arc_moves':arcs,'helical_arc_moves':helices,
            'feed_time_seconds':seconds,'rapid_time_not_included':True,'motion_trace':trace,
            'entry_height_mm':entry,'final_depth_mm':floor,'rapid_in_stock_requires_previously_cleared_volume':True}
