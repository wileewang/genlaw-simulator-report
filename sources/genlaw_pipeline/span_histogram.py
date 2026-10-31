"""Exact height occupancy from marginal endpoint counts, without rereading meshes.

For an open interval (a,b), active spans = count(low < b) - count(high <= a).
Native float32 endpoints are promoted exactly; CAD/section bounds remain float64.
"""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,require_bounded_worker
from .sparse_sections import checked_levels,interval_groups


def build(heights):
    require_bounded_worker()
    if (heights.dtype!=np.dtype('<f4') or heights.shape!=(len(heights),2)
            or len(heights)>500000 or not np.isfinite(heights).all()
            or np.any(heights[:,0]>=heights[:,1])):
        raise ValueError('Invalid original native height spans')
    result={'triangles':len(heights)}
    for i,key in enumerate(['lower','upper']):
        z,counts=np.unique(heights[:,i],return_counts=True)
        if len(z)>4096:raise ValueError('Native height index exceeds original level limit')
        result[key]={'z_mm':z.astype('<f8').tolist(),'counts':counts.tolist()}
    return result


def checked(histogram):
    n=histogram.get('triangles');result={}
    if type(n) is not int or not 0<=n<=500000:raise ValueError('Invalid height-index count')
    for key in ['lower','upper']:
        row=histogram[key];values=row['z_mm'];counts=row['counts']
        z=np.asarray(values,dtype='<f8');c=np.asarray(counts,dtype='<i8')
        if (z.ndim!=1 or c.ndim!=1 or len(z)!=len(c) or len(z)>4096
                or not np.isfinite(z).all() or np.any(np.diff(z)<=0)
                or not np.array_equal(z,z.astype('<f4').astype('<f8'))
                or any(type(v) is not int or v<=0 for v in counts) or int(c.sum())!=n):
            raise ValueError('Invalid exact native height histogram')
        result[key]=(z,np.r_[np.int64(0),np.cumsum(c)])
    return result


def active(histogram,low,high):
    if not np.isfinite([low,high]).all() or low>=high:raise ValueError('Invalid open query interval')
    data=checked(histogram);a,ac=data['lower'];b,bc=data['upper']
    value=int(ac[np.searchsorted(a,high,side='left')]-bc[np.searchsorted(b,low,side='right')])
    if not 0<=value<=histogram['triangles']:raise ValueError('Inconsistent native endpoint counts')
    return value


def costs(levels,histograms):
    require_bounded_worker();levels=checked_levels(levels);n=len(levels)-1
    result=np.zeros((min(16,n),n),dtype='<i8');total=0
    # This bound counts artifact files, not native execution windows. Ownership
    # and the unchanged 1024-window bound are independently checked by callers.
    for index,histogram in enumerate(histograms):
        if index>=64*1024:raise ValueError('Too many files for bounded native windows')
        data=checked(histogram);total+=histogram['triangles']
        if total>500000*1024:raise ValueError('Global surface exceeds existing record bound')
        for key in data:
            z,_=data[key]
            if len(z) and (z[0]<levels[0] or z[-1]>levels[-1]):
                raise ValueError('Measurement levels do not cover all native height spans')
        a,ac=data['lower'];b,bc=data['upper']
        for offset in range(len(result)):
            width=offset+1;starts=levels[:-width];ends=levels[width:]
            values=ac[np.searchsorted(a,ends,side='left')]-bc[np.searchsorted(b,starts,side='right')]
            if np.any(values<0) or np.any(values>histogram['triangles']):raise ValueError('Invalid interval occupancy')
            result[offset,:n-offset]+=values
    for offset in range(len(result)):result[offset,n-offset:]=-1
    return result
