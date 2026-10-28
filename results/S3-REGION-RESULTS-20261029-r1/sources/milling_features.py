"""Explicit feature contracts shared by preparation; unknown capabilities fail closed.

V4 retains its original closed-pocket contract. V5 adds named planar features
without requiring each later feature to duplicate source and resource checks.
"""
from .preparation import InputError,number,vector
from .pocket_geometry import POCKET
from .step_geometry import STEP
from .region_geometry import REGION


CONTRACTS={
 'rounded_pocket_xy':{'version':'v4','adapter':'genlaw.preparation.rounded-pocket.v1','reference':POCKET,
   'kind':'rounded_rectangular_pocket','operation':'pocket_milling',
   'dimensions':['width_mm','height_mm','corner_radius_mm','depth_mm'],
   'fields':{'id','kind','axis','center_xy_mm','width_mm','height_mm','corner_radius_mm','depth_mm'},'extra_parameters':[],
   'extra_strategy':{}},
 'edge_step_xy':{'version':'v5','adapter':'genlaw.preparation.edge-step.v1','reference':STEP,
   'kind':'edge_step','operation':'step_milling','dimensions':['side','shoulder_coordinate_mm','depth_mm'],
   'fields':{'id','kind','axis','side','shoulder_coordinate_mm','depth_mm'},'extra_parameters':['edge_extension_mm'],
   'extra_strategy':{'edge_extension_mode':'all_three_open_stock_edges','extension_corners':True}},
 'planar_floor_xy':{'version':'v6','adapter':'genlaw.preparation.planar-floor.v1','reference':REGION,
   'kind':'planar_floor','operation':'region_milling','dimensions':['depth_mm'],
   'fields':{'id','kind','axis','depth_mm','floor_face','profile','open_edge_groups'},'extra_parameters':['edge_extension_mm'],
   'extra_strategy':{'edge_extension_mode':'declared_native_stock_edge_groups','extension_corners':True}}
}


def contract_for(manifest):
    contract=CONTRACTS.get(manifest.get('capability'))
    if contract is None or manifest.get('schema')!='genlaw.preparation-input.'+contract['version']:
        raise InputError('unsupported_capability','capability','该输入版本与加工能力组合尚未实现。')
    return contract


def validate_feature(contract,feature,bounds):
    if set(feature)!=contract['fields'] or feature.get('kind')!=contract['kind'] or vector(feature.get('axis'),'axis')!=[0,0,1]:
        raise InputError('unsupported_capability','feature','未知轮廓、方向或几何字段不能忽略。')
    low=vector(bounds.get('min'),'bounds/min');high=vector(bounds.get('max'),'bounds/max')
    depth=number(feature['depth_mm'],'depth_mm',True)
    if any(a>=b for a,b in zip(low,high)) or depth>=high[2]-low[2]:
        raise InputError('conflicting_information','depth_mm','该铣削特征必须保留底部。')
    if contract['kind']=='rounded_rectangular_pocket':
        xy=vector(feature['center_xy_mm'],'center_xy_mm',2)
        w,h,r=[number(feature[k],k,True) for k in ['width_mm','height_mm','corner_radius_mm']]
        if 2*r>=min(w,h):raise InputError('unsupported_capability','corner_radius_mm','当前圆角矩形要求内角半径小于半短边；胶囊形另行验收。')
        if not all(low[i]<xy[i]-size/2 and xy[i]+size/2<high[i] for i,size in enumerate([w,h])):
            raise InputError('unsupported_capability','feature','该适配器只接受封闭槽。')
    elif contract['kind']=='edge_step':
        if feature['side'] not in ('X+','X-','Y+','Y-'):raise InputError('unsupported_capability','side','台阶须明确沿 X 或 Y 的哪一侧开口。')
        coordinate=number(feature['shoulder_coordinate_mm'],'shoulder_coordinate_mm');axis='XY'.index(feature['side'][0])
        if not low[axis]<coordinate<high[axis]:raise InputError('conflicting_information','shoulder_coordinate_mm','台肩必须位于来料内部。')
    else:
        from .region_geometry import profile,nominal_area
        import re
        from shapely.geometry import box
        try:
            region=profile(feature,256);groups=feature['open_edge_groups']
            if not isinstance(feature['floor_face'],str) or not re.fullmatch(r'Face[1-9][0-9]{0,4}',feature['floor_face']):raise ValueError('Need a bound original floor face')
            if not isinstance(groups,list) or len(groups)>16 or any(not isinstance(g,list) or not 1<=len(g)<=64 for g in groups):raise ValueError('Open-edge group budget or structure invalid')
            edges=[i for g in groups for i in g]
            if any(isinstance(i,bool) or not isinstance(i,int) or i<1 for i in edges) or len(edges)!=len(set(edges)):raise ValueError('Duplicate or invalid native open edge')
            stock=box(*low[:2],*high[:2])
            if region.difference(stock.buffer(1e-8)).area>1e-8 or nominal_area(feature)<=0 or region.area>=stock.area:raise ValueError('Floor must be inside stock and retain uncut material')
        except (ValueError,KeyError,TypeError) as error:raise InputError('unsupported_capability','profile',str(error))
    return low,high,depth


def required_nominals(contract,feature):
    if contract['kind']=='rounded_rectangular_pocket':
        return {'profile_xy':{k:feature[k] for k in ['center_xy_mm','width_mm','height_mm','corner_radius_mm']},'pocket_depth':feature['depth_mm'],'floor_geometry':'flat'}
    if contract['kind']=='planar_floor':
        values={'contour_xy':feature['profile'],'cut_depth':feature['depth_mm'],'floor_geometry':'flat'}
        if feature['open_edge_groups']:values['open_edge_clearance']={'groups':len(feature['open_edge_groups']),'depth_mm':feature['depth_mm']}
        if feature['profile']['islands']:values['island_retention']={'island_count':len(feature['profile']['islands']),'unmachined_top':'stock_top'}
        return values
    return {'shoulder_position':{k:feature[k] for k in ['side','shoulder_coordinate_mm']},'step_depth':feature['depth_mm'],'floor_geometry':'flat',
            'edge_clearance':{'open_stock_edges':3,'depth_mm':feature['depth_mm']}}
