"""Check full face coverage at every depth against the explicit incoming stock."""
from .facing_geometry import CAPABILITY
from .milling_nc import check_milling_nc


def check_nc(text,plan):
    from shapely.geometry import box
    if plan.get('capability')!=CAPABILITY:raise ValueError('Unsupported facing capability')
    bounds=plan['stock']['bounds_mm'];p=plan['parameters'];radius=plan['tool']['diameter_mm']/2
    tolerance=2*p['path_tolerance_mm']+2e-5
    clearing=box(*bounds['min'][:2],*bounds['max'][:2])
    # MillFace Boundbox/ClearEdges expands its envelope by radius + 0.1 mm.
    # Keep that source-derived limit explicit; do not enlarge NC or the stock.
    allowed=clearing.buffer(radius+.1+tolerance,join_style='mitre')
    result=check_milling_nc(text,plan,allowed,clearing)
    result.update(incoming_stock_bounds=bounds,target_bounds=plan['target_bounds_mm'],
                  facing_semantics='Clear the complete incoming XY footprint to the original CAD top, with the declared step-down and unchanged NC.')
    return result
