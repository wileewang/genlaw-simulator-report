"""Apply the explicit vertical feed to native Surface Path commands, before post.

FreeCAD Surface currently emits horizontal feed for its planar plunge points.
Only F is changed; native command order and every geometric parameter are kept.
"""
import math
import re


def adapt(commands,start,horizontal,vertical):
    # FreeCAD Path stores speeds in mm/s; linuxcnc_post exports mm/min.
    horizontal/=60.;vertical/=60.
    position=list(start);result=[];changes=[]
    for index,item in enumerate(commands):
        name=item['name'];parameters=dict(item['parameters'])
        if (re.fullmatch(r'\([^()]*\)',name) or re.fullmatch(r'N \([^()]*\)',name)) and not parameters:
            result.append({'name':name,'parameters':parameters});continue
        if name not in ('G0','G1'):raise ValueError('Feed adapter only supports native linear Surface commands')
        if set(parameters)-set('XYZF'):raise ValueError('Unsupported native motion word')
        if any(not math.isfinite(v) for v in parameters.values()):raise ValueError('Nonfinite native motion')
        end=[parameters.get(a,v) for a,v in zip('XYZ',position)]
        if name=='G1':
            expected=horizontal if any(abs(end[i]-position[i])>1e-8 for i in [0,1]) else vertical
            if 'F' not in parameters or min(abs(parameters['F']-horizontal),abs(parameters['F']-vertical))>1e-8:
                raise ValueError('Unrecognized original Surface feed')
            if abs(parameters['F']-expected)>1e-8:
                changes.append({'command_index':index,'from_mm_min':parameters['F']*60,'to_mm_min':expected*60})
                parameters['F']=expected
        result.append({'name':name,'parameters':parameters});position=end
    before=[(r['name'],{k:v for k,v in r['parameters'].items() if k!='F'}) for r in commands]
    after=[(r['name'],{k:v for k,v in r['parameters'].items() if k!='F'}) for r in result]
    if before!=after:raise ValueError('Feed adapter changed native geometry or order')
    return result,{'schema':'genlaw.surface-feed-adaptation.v1','changes':changes,
        'native_path_feed_units':'mm/s','resource_and_post_feed_units':'mm/min',
        'native_command_count':len(commands),'native_geometry_and_command_order_unchanged':True,
        'changes_applied_before_native_post':True,'exported_nc_text_modified':False}
