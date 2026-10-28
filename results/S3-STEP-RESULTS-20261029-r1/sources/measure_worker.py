"""Measure a new CAMotics output against this run's native CAD observation."""
import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'web/tools'))
from genlaw_pipeline.drilling_geometry import SINGLE, MULTI, DRILLED, reference_holes
from genlaw_pipeline.reference_sections import DrilledReference, integrate_variable
from genlaw_pipeline.pocket_geometry import POCKET,PocketReference
from genlaw_pipeline.step_geometry import STEP,StepReference
from mesh_metrics import (require_bounded_worker,load_mesh,write_indexed,Sections,integrate,
                          sha,controls,np,box,Point)


def section_height(levels, requested):
    """Avoid vertex/coplanar levels, just as the volume quadrature does."""
    index=max(0,min(len(levels)-2,bisect.bisect_right(levels,requested)-1))
    lo,hi=levels[index:index+2]
    if not hi>lo:raise ValueError('Invalid section interval')
    return float(lo+(hi-lo)*0.3819660112501051)


def measure(run, actual, output, display=False, interval_start=None, interval_end=None, parts=None, plan_only=False):
    require_bounded_worker()
    ref=json.loads((run/'native-geometry.json').read_text())
    if ref['status']!='verified' or ref['reference_class'] not in (SINGLE,MULTI,DRILLED,POCKET,STEP):
        raise ValueError('Missing verified native reference')
    vertices,faces,inspection=load_mesh(actual)
    if display:
        write_indexed(run/'actual.mesh',vertices,faces)
        tv,tf,_=load_mesh(run/'target-display.stl'); write_indexed(run/'target.mesh',tv,tf)
    zlo,zhi=ref['bounds']['min'][2],ref['bounds']['max'][2]
    variable=ref['reference_class'] in (DRILLED,POCKET,STEP)
    if variable:
        reference_type={POCKET:PocketReference,STEP:StepReference,DRILLED:DrilledReference}[ref['reference_class']]
        target=reference_type(ref,2048);coarse_target=reference_type(ref,1024)
        if abs(target.analytic_volume-ref['volume_mm3'])>1e-8:raise ValueError('Analytic target volume disagrees with native CAD')
    levels=np.unique(np.concatenate((vertices[:,2].astype(np.float64),target.breaks if variable else [zlo,zhi])))
    levels_hash=hashlib.sha256(levels.astype('<f8').tobytes()).hexdigest()
    if len(levels)>4096:raise ValueError('Total sectional measurement budget exceeded')
    binding={'actual_sha256':sha(actual),'native_geometry_sha256':sha(run/'native-geometry.json'),
             'levels_sha256':levels_hash,'source_code_sha256':sha(Path(__file__))}
    if variable:binding['reference_source_code_sha256']=sha(ROOT/'genlaw_pipeline/reference_sections.py')
    if ref['reference_class']==POCKET:binding['pocket_reference_source_code_sha256']=sha(ROOT/'genlaw_pipeline/pocket_geometry.py')
    if ref['reference_class']==STEP:binding['step_reference_source_code_sha256']=sha(ROOT/'genlaw_pipeline/step_geometry.py')
    if plan_only:
        result={'schema':'genlaw.section-partition-plan.v1',**binding,'levels':len(levels),
                'parts':[[i,min(i+128,len(levels)-1)] for i in range(0,len(levels)-1,128)]}
        with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
        print(json.dumps({'levels':len(levels),'parts':len(result['parts'])}));return
    partial=interval_start is not None or interval_end is not None
    if partial:
        if interval_start is None or interval_end is None or not 0<=interval_start<interval_end<len(levels) or interval_end-interval_start>128:
            raise ValueError('Invalid bounded interval partition')
    elif len(levels)>192 and parts is None:
        raise ValueError('Section count requires bounded partitions; no levels were dropped')
    if not variable:
        rectangle=box(*ref['bounds']['min'][:2],*ref['bounds']['max'][:2])
        target=rectangle;coarse_target=rectangle
        for hole in reference_holes(ref):
            center,radius=hole['center_xy_mm'],hole['diameter_mm']/2
            target=target.difference(Point(*center).buffer(radius,quad_segs=2048))
            coarse_target=coarse_target.difference(Point(*center).buffer(radius,quad_segs=1024))
    sections=Sections(vertices,faces)
    if parts is not None:
        partition=load_partition(parts,binding,len(levels))
        coarse=combine_volumes([p['coarse'] for p in partition],2)
        refined=combine_volumes([p['refined'] for p in partition],3)
    else:
        selected_levels=levels[interval_start:interval_end+1] if partial else levels
        if variable:
            coarse=integrate_variable(sections,selected_levels,coarse_target,2)
            refined=integrate_variable(sections,selected_levels,target,3)
        else:
            coarse=integrate(sections,selected_levels,coarse_target,(zlo,zhi),2)
            refined=integrate(sections,selected_levels,target,(zlo,zhi),3)
    if partial:
        result={'schema':'genlaw.sectional-iou-part.v1',**binding,'interval_start':interval_start,
                'interval_end':interval_end,'coarse':coarse,'refined':refined}
        with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
        print(json.dumps({'intervals':[interval_start,interval_end]}));return
    delta=abs(coarse['iou']-refined['iou'])
    if delta>1e-5 or abs(refined['actual_volume_mm3']-inspection['volume_mm3'])>1e-4:
        raise ValueError('Geometry estimate failed stability/volume check')
    if variable and abs(refined['target_volume_mm3']-ref['volume_mm3'])>1e-5:
        raise ValueError('Height-dependent reference integration disagrees with native CAD volume')
    hole_samples=[]
    for fraction in (0.1,0.5,0.9):
        requested=zlo+(zhi-zlo)*fraction
        z=section_height(levels,requested); material=sections.at(z)
        holes=[p for p in material.geoms] if material.geom_type=='MultiPolygon' else [material]
        from shapely.geometry import Polygon
        rings=[Polygon(r) for p in holes for r in p.interiors]
        if ref['reference_class']==SINGLE and len(rings)==1:
            center=ref['hole_center_xy']
            h=rings[0]; c=h.centroid
            hole_samples.append({'requested_z_mm':requested,'z_mm':z,'interior_loops':1,'area_mm2':h.area,
                                 'area_equivalent_diameter_mm':2*math.sqrt(h.area/math.pi),
                                 'centroid_xy_mm':[c.x,c.y],
                                 'center_error_mm':math.hypot(c.x-center[0],c.y-center[1])})
        else:
            hole_samples.append({'requested_z_mm':requested,'z_mm':z,'interior_loops':len(rings),
                                 'holes':[{'centroid_xy_mm':[h.centroid.x,h.centroid.y],
                                           'area_equivalent_diameter_mm':2*math.sqrt(h.area/math.pi)} for h in rings]})
    result={'schema':'genlaw.sectional-iou.v2','status':'measured_numerical_estimate',
            'iou':refined['iou'],'iou_percent':100*refined['iou'],'volumes':refined,
            'excess_material_mm3':refined['actual_volume_mm3']-refined['intersection_mm3'],
            'missing_material_mm3':refined['target_volume_mm3']-refined['intersection_mm3'],
            'actual_mesh':inspection,'reference_cad':ref,'native_geometry_sha256':sha(run/'native-geometry.json'),
            'absolute_iou_refinement_difference':delta,'section_levels':len(levels),
            'controls':controls(),'section_hole_observations':hole_samples,
            'method':'Closed STL cross-sections, all vertex-height intervals, 3-point Gauss integration against native-checked analytic CAD section.',
            'registration':'none; same CAD/G54 coordinates',
            'full_tolerance_acceptance':False,'production_approved':False,
            'limits':['Numerical estimate, not a certified error bound.','Section diameters are area-equivalent, not a GD&T acceptance.','Three interior slices do not certify through-hole or roughness acceptance.']}
    if parts is not None:
        result['bounded_partition_count']=len(partition)
        result['all_vertex_height_intervals_preserved']=True
        result['partition_files_sha256']={p.name:sha(p) for p in sorted(parts.glob('part-*.json'))}
    if variable:
        result['reference_sections']='Height-dependent cylinders and conical bottoms; CAD critical heights included.'
        result['reference_source_code_sha256']=binding['reference_source_code_sha256']
    if ref['reference_class']==POCKET:
        result['reference_sections']='Box stock minus a rounded rectangular pocket above its original CAD floor; CAD critical heights included.'
        result['pocket_reference_source_code_sha256']=binding['pocket_reference_source_code_sha256']
        result['section_void_observations']=result.pop('section_hole_observations')
        result['limits']=['Numerical estimate, not a certified error bound.','Interior contour descriptors are topology checks, not manufacturing acceptance.','Use the separate original-profile and floor measurements for pocket acceptance.']
    if ref['reference_class']==STEP:
        result['reference_sections']='Box stock minus an open edge step above its original CAD floor; CAD critical heights included.'
        result['step_reference_source_code_sha256']=binding['step_reference_source_code_sha256']
        result['section_topology_observations']=result.pop('section_hole_observations')
        result['limits']=['Numerical estimate, not a certified error bound.','Open-step section topology is descriptive, not manufacturing acceptance.','Use the separate shoulder, floor and near-edge measurements for step acceptance.']
    with output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({'iou_percent':result['iou_percent'],'triangles':inspection['triangles'],'refinement_difference':delta}))


def combine_volumes(values,order):
    """Sum interval integrals, not per-part IoU ratios."""
    fields=['actual_volume_mm3','target_volume_mm3','intersection_mm3','alternative_intersection_mm3']
    result={field:math.fsum(v[field] for v in values) for field in fields}
    a,b,intersection=(result[k] for k in fields[:3]);union=a+b-intersection
    if union<=0 or not -1e-8<=intersection<=min(a,b)+1e-8:raise ValueError('Invalid combined volumes')
    result.update(union_mm3=union,iou=intersection/union,quadrature_order=order)
    return result


def load_partition(directory,binding,level_count):
    values=[json.loads(p.read_text()) for p in sorted(directory.glob('part-*.json'))]
    values.sort(key=lambda p:p['interval_start']);cursor=0
    for value in values:
        if value.get('schema')!='genlaw.sectional-iou-part.v1' or any(value.get(k)!=v for k,v in binding.items()):
            raise ValueError('Partition source binding changed')
        if value['interval_start']!=cursor or not cursor<value['interval_end']<=min(cursor+128,level_count-1):
            raise ValueError('Missing, overlapping or out-of-budget intervals')
        cursor=value['interval_end']
    if cursor!=level_count-1:raise ValueError('Incomplete interval coverage')
    return values


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--actual',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--display',action='store_true')
    parser.add_argument('--plan-only',action='store_true');parser.add_argument('--parts',type=Path)
    parser.add_argument('--interval-start',type=int);parser.add_argument('--interval-end',type=int)
    args=parser.parse_args(); measure(args.run,args.actual,args.output,args.display,args.interval_start,args.interval_end,args.parts,args.plan_only)
