"""Pack an integer cell box into balanced Cartesian tiles under fixed budgets.

This proposes cell ownership only. It neither classifies material nor proves
that a native STL will fit its file/triangle limits. Global coordinates and
the original engine's XY sampling limit remain unchanged.
"""
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import require_bounded_worker
from .cell_windows import MAX_CELLS,MAX_XY_SAMPLES,MAX_WINDOWS,window_budget


def ceildiv(a,b): return (a+b-1)//b


def layout(widths,maximum_tiles=MAX_WINDOWS):
    """Minimize tile count within balanced Cartesian partitions, not all tilings."""
    require_bounded_worker()
    if (not isinstance(widths,list) or len(widths)!=3
            or any(type(n) is not int or not 1<=n<=2**32-1 for n in widths)
            or type(maximum_tiles) is not int or not 1<=maximum_tiles<=MAX_WINDOWS
            or math.prod(widths)>2**64-1):
        raise ValueError('Invalid cell extent or unchanged tile budget')
    x,y,z=widths; best=None; examined=0
    for nx in range(1,min(x,maximum_tiles)+1):
        dx=ceildiv(x,nx)
        max_dy=min(y,MAX_XY_SAMPLES//(dx+1)-1,MAX_CELLS//dx)
        if max_dy<1: continue
        first=ceildiv(y,max_dy)
        for ny in range(first,min(y,maximum_tiles//nx)+1):
            examined+=1; dy=ceildiv(y,ny); max_dz=MAX_CELLS//(dx*dy)
            if max_dz<1: continue
            nz=ceildiv(z,max_dz); count=nx*ny*nz
            if count>maximum_tiles: continue
            dz=ceildiv(z,nz)
            if (dx+1)*(dy+1)>MAX_XY_SAMPLES or dx*dy*dz>MAX_CELLS:
                raise ValueError('Internal tile search exceeded a fixed native budget')
            score=(count,(x+nx)*(y+ny)*(z+nz),dx*dy*dz,nz,ny,nx)
            if best is None or score<best[0]: best=(score,[nx,ny,nz],[dx,dy,dz])
    if best is None:
        raise ValueError('No balanced Cartesian partition fits the unchanged cell/XY/tile budgets')
    score,counts,spans=best
    return {'counts':counts,'tiles':score[0],'summed_vertex_samples':score[1],
        'maximum_cell_count':score[2],'maximum_xy_samples':(spans[0]+1)*(spans[1]+1),
        'maximum_spans':spans,'layouts_examined':examined,
        'optimality_scope':'minimum count among balanced Cartesian partitions',
        'output_triangle_or_file_budget_proven':False}


def tiles(box,steps,maximum_tiles=MAX_WINDOWS):
    window_budget(box,steps)  # Validate original integer bounds even when oversized.
    widths=[box[i+3]-box[i] for i in range(3)]; selected=layout(widths,maximum_tiles)
    cuts=[[box[axis]+i*widths[axis]//count for i in range(count+1)]
          for axis,count in enumerate(selected['counts'])]
    result=[]
    for ix in range(selected['counts'][0]):
        for iy in range(selected['counts'][1]):
            for iz in range(selected['counts'][2]):
                indices=[ix,iy,iz]
                result.append([cuts[k][indices[k]] for k in range(3)]+[cuts[k][indices[k]+1] for k in range(3)])
    if (len(result)!=selected['tiles'] or any(not window_budget(b,steps)['allowed'] for b in result)
            or sum(math.prod(b[k+3]-b[k] for k in range(3)) for b in result)!=math.prod(widths)):
        raise ValueError('Generated tile coverage differs from its Cartesian ownership')
    return selected,result
