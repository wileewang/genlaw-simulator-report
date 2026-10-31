"""The unchanged section kernel over typed exact-planar complete native surfaces."""
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,require_bounded_worker
from .cell_windows import checked_file
from .planar_material import checked_cache
from .planar_directory import checked_surface
from .compact_sections import CompactedSections
from .sparse_sections import checked_levels,interval_groups
from .span_histogram import active,costs,build


def cache_arrays(binding,plan_binding,entry):
    cache=checked_cache(binding,plan_binding,entry);n=cache['triangles']
    array=np.load(checked_file(cache['array']),mmap_mode='r',allow_pickle=False)
    heights=np.load(checked_file(cache['heights']),mmap_mode='r',allow_pickle=False)
    if (array.dtype!=np.dtype('<f4') or array.shape!=(n,3,3)
            or heights.dtype!=np.dtype('<f4') or heights.shape!=(n,2)
            or not np.isfinite(array).all() or not np.isfinite(heights).all()
            or np.any(heights[:,0]>=heights[:,1])
            or not np.array_equal(heights[:,0],array[:,:,2].min(axis=1))
            or not np.array_equal(heights[:,1],array[:,:,2].max(axis=1))
            or build(heights)!=cache['height_histogram']):
        raise ValueError('Native cache coordinates or exact height index changed')
    return array,heights


def schedule(surface_path,levels):
    require_bounded_worker();surface,plan,_=checked_surface(surface_path)
    histograms=(checked_cache(binding,surface['material_plan'],entry)['height_histogram']
        for binding,entry in zip(surface['caches'],plan['chunks']))
    table=costs(levels,histograms)
    return {'levels_mm':checked_levels(levels).tolist(),'groups':interval_groups(table),
        'active_triangle_counts_by_span':table.tolist(),'maximum_active_triangles':500000,
        'maximum_section_segments':32768,'maximum_intervals_per_worker':16,
        'original_intervals_removed':0,'native_coordinates_modified':False}


class PlanarSections(CompactedSections):
    def __init__(self,surface_path,low,high):
        require_bounded_worker()
        if not math.isfinite(low) or not math.isfinite(high) or low>=high:raise ValueError('Invalid native section interval')
        surface,plan,_=checked_surface(surface_path);pieces=[];count=0
        for binding,entry in zip(surface['caches'],plan['chunks']):
            cache=checked_cache(binding,surface['material_plan'],entry)
            expected=active(cache['height_histogram'],low,high)
            if not expected:continue
            if count+expected>500000:raise ValueError('Native section exceeds unchanged active triangle budget')
            array,heights=cache_arrays(binding,surface['material_plan'],entry)
            z=np.asarray(heights,dtype='<f8');mask=(z[:,0]<high)&(z[:,1]>low);selected=int(mask.sum())
            if selected!=expected:raise ValueError('Native height occupancy index is incorrect')
            count+=selected;pieces.append(np.asarray(array[mask],dtype='<f8'))
        self.triangles=np.concatenate(pieces) if pieces else np.empty((0,3,3),dtype='<f8')
        self.zmin=self.triangles[:,:,2].min(axis=1);self.zmax=self.triangles[:,:,2].max(axis=1)
        self.low,self.high=float(low),float(high);self.calls=0
        self.maximum_observed_segments=0;self.maximum_compacted_segments=0;self.last_stats=None

    def at(self,z):
        if not self.low<z<self.high:raise ValueError('Section outside loaded native height interval')
        return super().at(z)
