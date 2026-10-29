"""Closed-pocket envelope for the shared bounded XYZ NC guard."""
from .milling_nc import arc_points,check_milling_nc
from .pocket_geometry import profile,CAPABILITY


def check_nc(text,plan):
    if plan.get('capability')!=CAPABILITY:raise ValueError('Unsupported milling capability')
    feature=plan['operations'][0]['geometry'];tol=2*plan['parameters']['path_tolerance_mm']+2e-5
    allowed=profile(feature,1024,inset=plan['tool']['diameter_mm']/2).buffer(tol)
    return check_milling_nc(text,plan,allowed,profile(feature,1024))
