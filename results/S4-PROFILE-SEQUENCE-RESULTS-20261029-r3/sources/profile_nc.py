"""Check the complete native outside profile, including startup and full waste removal."""
import math
from .profile_geometry import CAPABILITY,profile
from .milling_nc import check_milling_nc,arc_points,EPS
from .sequence_nc import native_header,segments,HEADER,SHUTDOWN


def check_nc(text,plan):
    from shapely.geometry import box
    if plan.get('capability')!=CAPABILITY or plan.get('schema')!='genlaw.execution-plan.v10':
        raise ValueError('Unsupported capsule profile NC contract')
    if plan['strategy']['keep_tool_down'] is not True or plan['strategy']['layer_transition']!='closed_profile_feed_down':
        raise ValueError('Profile requires the explicitly declared closed-contour feed-down transition')
    items,blocks=segments(text,native_header(plan))
    if len(items)!=2 or [i['label'] for i in items]!=['TC Profile T1','Profile']:
        raise ValueError('Expected exactly the declared tool and outer profile operation')
    p=plan['parameters'];tool,operation=items
    if tool['blocks']!=[[('M',5)],[('M',6),('T',1)],[('M',3),('S',p['spindle_rpm'])]]:
        raise ValueError('Profile tool or spindle differs from the card')
    region=profile(plan['operations'][0]['geometry'],1024);radius=plan['tool']['diameter_mm']/2
    tolerance=2*p['path_tolerance_mm']+2e-5
    allowed=region.buffer(radius+tolerance,quad_segs=1024).difference(region.buffer(radius-tolerance,quad_segs=1024))
    stock=plan['stock']['bounds_mm'];waste=box(*stock['min'][:2],*stock['max'][:2]).difference(region)
    # Only this in-memory guard view uses the established single-tool wrapper.
    # CAMotics always receives the original file, including the startup retract.
    projected=HEADER+'\n'.join(tool['lines']+operation['lines'])+'\n'+SHUTDOWN
    result=check_milling_nc(projected,plan,allowed,waste)
    layers={};position=[None,None,None];cutting=False;ended=False
    for block in operation['blocks']:
        words=dict(block);code=words['G'];end=[words.get(axis,value) for axis,value in zip('XYZ',position)]
        if code==3:raise ValueError('Profile direction must remain CW')
        if code==0 and cutting:ended=True
        if code in (1,2) and end[2]<stock['max'][2]-EPS:
            if ended:raise ValueError('Unexpected layer retract; card declares continuous closed-profile feed-down')
            cutting=True
            if code==2 or math.dist(position[:2],end[:2])>EPS:
                level=round(end[2],5)
                points,length=arc_points(position,end,code,words) if code==2 else ([position[:2],end[:2]],math.dist(position[:2],end[:2]))
                record=layers.setdefault(level,{'start_xy':position[:2],'end_xy':None,'length_mm':0})
                record['end_xy']=end[:2];record['length_mm']+=length
        position=end
    f=plan['operations'][0]['geometry'];perimeter=2*(f['length_mm']-f['width_mm'])+2*math.pi*(f['width_mm']/2+radius)
    for row in layers.values():
        if math.dist(row['start_xy'],row['end_xy'])>EPS or abs(row['length_mm']-perimeter)>16*p['path_tolerance_mm']+1e-4:
            raise ValueError('Every depth must contain one complete closed compensated capsule perimeter')
    offset=operation['first_executable_block']-result['step_map'][0]['first_executable_block']
    for trace in result['motion_trace']:trace['block']+=offset
    result['step_map'][0].update(first_executable_block=operation['first_executable_block'],last_executable_block=operation['last_executable_block'])
    result.update(executable_blocks=len(blocks),initial_machine_state=plan['setup']['startup'],
                  original_nc_modified=False,guard_wrapper_only=True,
                  outside_profile_verified=True,full_stock_waste_area_mm2=waste.area,
                  closed_layers=[{'z_mm':z,**row} for z,row in layers.items()],layer_transition='closed_profile_feed_down',
                  semantics='CAM radius-compensated outside path; every depth clears all original exterior waste. Original target is retained, no disconnected waste is discarded.')
    return result
