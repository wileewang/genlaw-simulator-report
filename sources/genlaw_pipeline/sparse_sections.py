"""Original-coordinate sections of audited sparse material, with bounded batches.

Sparse cache provenance remains explicit. The intersection and polygonization
implementation is the qualified CompactedSections kernel; only cache loading
and interval scheduling differ. No native triangle is displaced or discarded.
"""
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np, MAX_TRIANGLES, require_bounded_worker
from .cell_windows import checked_file, file_binding, MAX_WINDOWS
from .compact_sections import CompactedSections
from .sparse_stream_surface import checked_catalog, checked_audit
from .sharded_measure import MAX_ACTIVE_TRIANGLES, MAX_SECTION_SEGMENTS, INTERVALS_PER_WORKER

LOAD=lambda p: json.loads(p.read_text())
PRODUCER=ROOT/'genlaw_pipeline/sparse_stream_surface.py'


def checked_surface(path):
    surface=LOAD(path)
    if (surface.get('schema')!='genlaw.sparse-stream-surface.v1' or surface.get('status')!='closed_oriented_surface'
            or surface.get('source_code')!=file_binding(PRODUCER)
            or surface.get('whole_mesh_assembled') is not False or surface.get('geometry_modified') is not False
            or surface.get('artificial_caps') is not False or surface.get('synthetic_empty_stls')!=0
            or surface.get('all_global_cells_covered_once') is not True):
        raise ValueError('A complete, unmodified sparse surface audit is required')
    catalog_path=checked_file(surface['catalog']); catalog,plan=checked_catalog(catalog_path)
    if (surface['stream_plan']!=catalog['stream_plan'] or surface['sparse_plan']!=plan['sparse_plan']
            or surface['coverage']!=plan['coverage'] or surface['caches']!=[r['cache'] for r in catalog['leaves']]
            or any(surface[k]!=v for k,v in catalog['statistics'].items())):
        raise ValueError('Sparse surface, coverage or cache identity changed')
    observed={}; totals=dict.fromkeys(plan['bucket_counts'],0); unique=dict.fromkeys(totals,0)
    for binding in surface['bucket_audits']:
        row=LOAD(checked_file(binding)); kind=row.get('kind'); index=row.get('index')
        if (row.get('schema')!='genlaw.surface-bucket-audit.v1' or kind not in totals
                or type(index) is not int or not 0<=index<plan['bucket_counts'][kind]
                or (kind,index) in observed or row.get('catalog')!=surface['catalog']
                or row.get('source_code')!=file_binding(PRODUCER)
                or row.get('request')!=catalog['requests'][kind][index]):
            raise ValueError('Missing, duplicate or foreign sparse bucket audit')
        request=LOAD(checked_file(row['request']))
        if row['records']!=request['records']:
            raise ValueError('Sparse bucket dropped material records')
        observed[kind,index]=True; totals[kind]+=row['records']; unique[kind]+=row['unique_keys']
    if (len(observed)!=sum(plan['bucket_counts'].values()) or totals!=catalog['record_totals']
            or unique['facets']!=surface['triangles'] or unique['edges']!=surface['unique_edges']
            or 2*unique['edges']!=3*unique['facets']):
        raise ValueError('Incomplete global sparse surface topology')
    for entry,leaf in zip(plan['native_leaves'],catalog['leaves']):
        audit=checked_audit(checked_file(leaf['audit']),checked_file(catalog['stream_plan']),plan,entry)
        if leaf['cache']!=audit['cache']:
            raise ValueError('Sparse cache lacks its original native leaf audit')
    return surface,plan,catalog


def cache_arrays(binding,stream_binding,entry):
    """Check each native cache before using its index to select any triangles."""
    cache=LOAD(checked_file(binding)); n=cache.get('triangles')
    if (cache.get('schema')!='genlaw.sparse-section-cache.v1' or cache.get('stream_plan')!=stream_binding
            or cache.get('index')!=entry['index'] or cache.get('native_result')!=entry['result']
            or cache.get('original_surface')!=entry['surface'] or cache.get('geometry_modified') is not False
            or cache.get('source_code')!=file_binding(PRODUCER)
            or type(n) is not int or not 0<=n<=entry['triangles']<=MAX_TRIANGLES
            or type(cache.get('omitted_exactly_horizontal_triangles')) is not int
            or n+cache['omitted_exactly_horizontal_triangles']!=entry['triangles']):
        raise ValueError('Foreign or modified sparse section cache')
    array=np.load(checked_file(cache['array']),mmap_mode='r',allow_pickle=False)
    heights=np.load(checked_file(cache['heights']),mmap_mode='r',allow_pickle=False)
    if (array.shape!=(n,3,3) or array.dtype!=np.dtype('<f4') or heights.shape!=(n,2)
            or heights.dtype!=np.dtype('<f4') or not np.isfinite(array).all()
            or not np.isfinite(heights).all() or np.any(heights[:,0]>=heights[:,1])
            or not np.array_equal(heights[:,0],array[:,:,2].min(axis=1))
            or not np.array_equal(heights[:,1],array[:,:,2].max(axis=1))):
        raise ValueError('Sparse cache coordinates or height index changed')
    return array,heights


def checked_levels(levels):
    value=np.asarray(levels,dtype='<f8')
    if value.ndim!=1 or not 2<=len(value)<=4096 or not np.isfinite(value).all() or np.any(np.diff(value)<=0):
        raise ValueError('Invalid complete material height levels')
    return value


def interval_costs(levels,height_chunks):
    """Exact union counts for every possible group of at most sixteen intervals.

For a group [i,i+k), a triangle is active iff its height span intersects the
group's open bounds. Difference arrays count it once, even across many levels.
Storage is at most 16*4095 int64 counters plus one native leaf's index.
"""
    require_bounded_worker(); levels=checked_levels(levels); n=len(levels)-1
    costs=np.zeros((min(INTERVALS_PER_WORKER,n),n),dtype='<i8')
    for index,chunk in enumerate(height_chunks):
        if index>=MAX_WINDOWS:
            raise ValueError('Height-index chunks exceed the original native leaf budget')
        if (chunk.dtype!=np.dtype('<f4') or chunk.shape!=(len(chunk),2) or len(chunk)>MAX_TRIANGLES
                or not np.isfinite(chunk).all() or np.any(chunk[:,0]>=chunk[:,1])):
            raise ValueError('Invalid native height-index chunk')
        z=np.asarray(chunk,dtype='<f8')
        if len(z) and (z[:,0].min()<levels[0] or z[:,1].max()>levels[-1]):
            raise ValueError('Height levels do not cover all native material')
        lo=np.searchsorted(levels,z[:,0],side='right')-1
        hi=np.searchsorted(levels,z[:,1],side='left')
        for offset in range(len(costs)):
            width=offset+1; valid_starts=n-width+1
            starts=np.maximum(lo-offset,0); ends=np.minimum(hi,valid_starts)
            keep=starts<ends
            delta=np.bincount(starts[keep],minlength=valid_starts+1)-np.bincount(ends[keep],minlength=valid_starts+1)
            costs[offset,:valid_starts]+=np.cumsum(delta[:-1])
    for offset in range(len(costs)):
        costs[offset,n-offset:]=-1
    return costs


def interval_groups(costs):
    if (not isinstance(costs,np.ndarray) or costs.dtype!=np.dtype('<i8') or costs.ndim!=2
            or not 1<=costs.shape[1]<=4095 or costs.shape[0]!=min(INTERVALS_PER_WORKER,costs.shape[1])):
        raise ValueError('Invalid native interval cost table')
    n=costs.shape[1]
    for offset,row in enumerate(costs):
        if np.any(row[:n-offset]<0) or np.any(row[n-offset:]!=-1):
            raise ValueError('Invalid native interval cost entries')
    if np.any(costs[0]>MAX_ACTIVE_TRIANGLES):
        raise ValueError('One original height interval exceeds the unchanged active triangle budget')
    groups=[]; start=0
    while start<n:
        width=min(INTERVALS_PER_WORKER,n-start)
        while costs[width-1,start]>MAX_ACTIVE_TRIANGLES: width-=1
        groups.append({'interval':[start,start+width],'active_triangles':int(costs[width-1,start])})
        start+=width
    return groups


def schedule(surface_path,levels):
    require_bounded_worker(); surface,plan,_=checked_surface(surface_path)
    def chunks():
        for binding,entry in zip(surface['caches'],plan['native_leaves']):
            _,heights=cache_arrays(binding,surface['stream_plan'],entry)
            yield heights
    costs=interval_costs(levels,chunks())
    return {'levels_mm':checked_levels(levels).tolist(),'groups':interval_groups(costs),
        'active_triangle_counts_by_span':costs.tolist(),'maximum_active_triangles':MAX_ACTIVE_TRIANGLES,
        'maximum_section_segments':MAX_SECTION_SEGMENTS,'maximum_intervals_per_worker':INTERVALS_PER_WORKER,
        'original_intervals_removed':0,'native_coordinates_modified':False}


class SparseSections(CompactedSections):
    """The original section kernel, supplied with truthful sparse native caches."""
    def __init__(self,surface_path,low,high):
        require_bounded_worker()
        if not math.isfinite(low) or not math.isfinite(high) or low>=high:
            raise ValueError('Invalid sparse section window')
        surface,plan,_=checked_surface(surface_path)
        pieces=[]; count=0
        for binding,entry in zip(surface['caches'],plan['native_leaves']):
            array,heights=cache_arrays(binding,surface['stream_plan'],entry)
            z=np.asarray(heights,dtype='<f8')  # Preserve thin intervals across float32-native/CAD breaks.
            mask=(z[:,0]<high)&(z[:,1]>low); selected=int(mask.sum())
            count+=selected
            if count>MAX_ACTIVE_TRIANGLES:
                raise ValueError('Sparse section group exceeds the unchanged active triangle budget')
            if selected: pieces.append(np.asarray(array[mask],dtype='<f8'))
        self.triangles=np.concatenate(pieces) if pieces else np.empty((0,3,3),dtype='<f8')
        self.zmin=self.triangles[:,:,2].min(axis=1); self.zmax=self.triangles[:,:,2].max(axis=1)
        self.low,self.high=float(low),float(high)
        self.calls=0; self.maximum_observed_segments=0; self.maximum_compacted_segments=0; self.last_stats=None

    def at(self,z):
        if not self.low<z<self.high:
            raise ValueError('Requested section lies outside the loaded open height window')
        return super().at(z)
