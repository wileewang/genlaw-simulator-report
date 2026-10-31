"""The existing section construction with spatially indexed ring containment.

All triangles, rounding and polygonization are unchanged. STRtree uses exactly
the same strict point-in-polygon predicate as the former all-rings scan.
"""
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import polygonize_full,unary_union
from shapely.strtree import STRtree
from mesh_metrics import Sections


class IndexedSections(Sections):
    def at(self,z):
        self.calls+=1
        tri=self.triangles[(self.zmin<z)&(self.zmax>z)]
        if len(tri)==0:return Polygon()
        if len(tri)>12000:raise ValueError('Cross section exceeds local line budget')
        points=np.empty((len(tri),2,2),dtype=np.float64);added=np.zeros(len(tri),dtype=np.int8)
        for i,j in ((0,1),(1,2),(2,0)):
            a,b=tri[:,i],tri[:,j]
            hit=((a[:,2]<z)&(b[:,2]>z))|((b[:,2]<z)&(a[:,2]>z))
            ids=np.flatnonzero(hit);ratio=(z-a[hit,2])/(b[hit,2]-a[hit,2])
            xy=a[hit,:2]+ratio[:,None]*(b[hit,:2]-a[hit,:2])
            points[ids,added[ids]]=xy;added[ids]+=1
        if not np.all(added==2):raise ValueError('Ambiguous section edge')
        points=np.round(points,8);points=points[np.any(points[:,0]!=points[:,1],axis=1)]
        polygons,cuts,dangles,invalid=polygonize_full(points)
        if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
            raise ValueError('Unclosed or invalid section')
        pieces=list(polygons.geoms);outer_rings=[Polygon(piece.exterior) for piece in pieces]
        tree=STRtree(outer_rings);selected=[];matches=0
        for piece in pieces:
            containers=tree.query(piece.representative_point(),predicate='within')
            matches+=len(containers)
            if matches>250000:raise ValueError('Section containment budget exceeded')
            if len(containers)%2==1:selected.append(piece)
        section=unary_union(selected)
        if not section.is_valid:raise ValueError('Invalid material section')
        return section
