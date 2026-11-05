"""Allow bounded air travel past stock edges while protecting the retained shoulder."""
from .milling_nc import check_milling_nc
from .step_geometry import CAPABILITY,profile,retained_profile


def check_nc(text,plan):
    from shapely.geometry import box
    if plan.get('capability')!=CAPABILITY:raise ValueError('Unsupported step capability')
    feature=plan['operations'][0]['geometry'];bounds=plan['stock']['bounds_mm'];p=plan['parameters'];radius=plan['tool']['diameter_mm']/2
    tolerance=2*p['path_tolerance_mm']+2e-5
    if radius<=tolerance:raise ValueError('Tool too small for declared path tolerance')
    travel=box(*bounds['min'][:2],*bounds['max'][:2]).buffer(p['edge_extension_mm']+tolerance,join_style='mitre')
    protected=retained_profile(feature,bounds).buffer(radius-tolerance,quad_segs=1024)
    allowed=travel.difference(protected)
    result=check_milling_nc(text,plan,allowed,profile(feature,bounds))
    result['open_edge_semantics']='Tool may leave bounded stock footprint; retained shoulder offset by cutter radius remains protected.'
    return result
