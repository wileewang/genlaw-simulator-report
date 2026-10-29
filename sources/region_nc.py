"""Guard original planar clearing regions and their protected islands/walls."""
from .region_geometry import CAPABILITY,profile
from .milling_nc import check_milling_nc


def check_nc(text,plan):
    from shapely.geometry import box
    from shapely.ops import unary_union
    if plan.get('capability')!=CAPABILITY:raise ValueError('Unsupported planar floor capability')
    f=plan['operations'][0]['geometry'];bounds=plan['stock']['bounds_mm'];p=plan['parameters'];radius=plan['tool']['diameter_mm']/2
    tol=2*p['path_tolerance_mm']+2e-5
    if radius<=tol:raise ValueError('Tool smaller than declared envelope tolerance')
    clearing=profile(f);interior=clearing.buffer(-radius+tol)
    if interior.is_empty:raise ValueError('Tool has no accessible center region')
    if f['open_edge_groups']:
        stock=box(*bounds['min'][:2],*bounds['max'][:2]);protected=stock.difference(clearing).buffer(radius-tol,quad_segs=1024)
        candidates=stock.buffer(p['edge_extension_mm']+tol,join_style='mitre').difference(protected)
        pieces=list(candidates.geoms) if candidates.geom_type=='MultiPolygon' else [candidates]
        allowed=unary_union([g for g in pieces if g.intersects(interior)])
    else:allowed=interior
    result=check_milling_nc(text,plan,allowed,clearing)
    result['profile_semantics']='Original line/circular floor loops, protected islands, and only air regions connected through declared stock openings.'
    return result
