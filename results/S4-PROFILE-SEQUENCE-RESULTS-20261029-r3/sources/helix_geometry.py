"""Milling intent for a straight through-cylinder; CAD stays process-independent."""
from .drilling_geometry import MULTI

CAPABILITY='helical_hole_milling_xy'
REFERENCE=MULTI


def clearing_radii(hole_diameter,tool_diameter,step_over_percent):
    """Explicit radial coverage schedule for the declared native Inside strategy."""
    import math
    outer=(hole_diameter-tool_diameter)/2
    if outer<=0:raise ValueError('Helical milling requires a cutter smaller than the hole')
    if hole_diameter/2<=tool_diameter:return [outer]
    first=tool_diameter/2;step=tool_diameter*step_over_percent/100
    if abs((outer-first)/step)<1e-5:return [outer]
    count=max(math.ceil(outer/step),2)
    if count>32:raise ValueError('Radial pass budget exceeded')
    return [first+(outer-first)*i/(count-1) for i in range(count)]
