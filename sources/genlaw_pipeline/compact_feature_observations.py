"""Original feature equations separated from the material-section reader.

The equations follow sharded_verification.section/finish and
chamfer_shards.section/verify. Callers supply audited original-coordinate
sections and bound ray records. This module does not repair geometry or select
new measurement locations, tolerances, or circle-fit sample phases.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from mesh_metrics import np, require_bounded_worker
from shapely.geometry import Polygon, LineString, box
from .chamfer_shards import target as chamfer_target
from .chamfer_verification import evaluate as chamfer_evaluate
from .profile_geometry import profile
from .region_verification import topology
from .sequence_verification import evaluate as planar_evaluate
from .sharded_verification import holes
from .verification import fit_ring, match_rings, summarize_rays


def section_observation(kind, plan, reference, height, actual):
    require_bounded_worker()
    if not actual.is_valid or not math.isfinite(height['z_mm']):
        raise ValueError('Invalid original material section')
    if kind == 'planar':
        pieces = list(actual.geoms) if actual.geom_type == 'MultiPolygon' else [actual]
        rings = [ring for piece in pieces if piece.geom_type == 'Polygon' for ring in piece.interiors]
        features = holes(reference)
        matched = match_rings(rings, features, plan['resolution_mm']); hole_rows = {}
        for feature in features:
            row = {**height, 'interior_loops': len(rings), 'matched': feature['id'] in matched}
            if row['matched']:
                row.update(fit_ring(rings[matched[feature['id']]]))
                row['center_deviation_mm'] = math.dist(row['fit_center_xy_mm'], feature['center_xy_mm'])
            hole_rows[feature['id']] = row
        valid = len(pieces) == 1 and not actual.is_empty and pieces[0].geom_type == 'Polygon'
        expected = profile(reference['profile_feature'], 2048) if 'profile_feature' in reference else box(
            *reference['bounds']['min'][:2], *reference['bounds']['max'][:2])
        outer = Polygon(pieces[0].exterior) if valid else None
        face = {**height, 'retained_body_count': len(pieces), 'topology_matches': valid,
            'holes_checked_separately': True,
            'boundary_deviation_mm': outer.boundary.hausdorff_distance(expected.boundary) if valid else None}
        return {'holes': hole_rows, 'face': face, 'counts': {'z_mm': height['z_mm'],
            'expected_holes': len(features), 'observed_holes': len(rings),
            'matched_features': list(matched), 'unassigned_contours': len(rings)-len(matched)}}
    if kind != 'chamfer':
        raise ValueError('Unsupported compacted-section observer')
    obj = chamfer_target(reference); z = height['z_mm']; expected = obj.at(z); lo, hi = obj.low, obj.high
    contour = {**height, 'expected_topology': topology(expected), 'observed_topology': topology(actual),
        'topology_matches': topology(actual) == topology(expected),
        'boundary_deviation_mm': actual.boundary.hausdorff_distance(expected.boundary) if not actual.is_empty else None,
        'missing_material_area_mm2': expected.difference(actual).area,
        'excess_material_area_mm2': actual.difference(expected).area}
    midline = None
    if height['region'] == 'bevel':
        xline = actual.intersection(LineString([(lo[0]-1, (lo[1]+hi[1])/2), (hi[0]+1, (lo[1]+hi[1])/2)]))
        yline = actual.intersection(LineString([((lo[0]+hi[0])/2, lo[1]-1), ((lo[0]+hi[0])/2, hi[1]+1)]))
        valid = all(g.geom_type == 'LineString' and not g.is_empty for g in [xline, yline])
        midline = {'z_mm': z, 'status': 'measured' if valid else 'ambiguous_midline',
            'insets_mm': {'xmin': xline.bounds[0]-lo[0], 'xmax': hi[0]-xline.bounds[2],
                         'ymin': yline.bounds[1]-lo[1], 'ymax': hi[1]-yline.bounds[3]} if valid else None}
    return {'contour': contour, 'midline': midline}


def checked_parts(kind, plan, sections, rays):
    if kind not in ('planar', 'chamfer'):
        raise ValueError('Unsupported compacted-section observer')
    if len(sections) != len(plan['heights']) or len(sections) != (14 if kind == 'chamfer' else 11):
        raise ValueError('Missing original feature observation heights')
    height_key = 'contour' if kind == 'chamfer' else 'face'
    for index, (row, height) in enumerate(zip(sections, plan['heights'])):
        if type(row['index']) is not int or row['index'] != index or any(row[height_key][k] != v for k, v in height.items()):
            raise ValueError('Missing, reordered or moved feature observation plane')
    ids = [probe['id'] for probe in plan['probes']]
    if len(ids) != len(set(ids)) or len(rays) != len(plan['ray_groups']):
        raise ValueError('Missing or duplicate original ray observations')
    key = 'interval' if kind == 'chamfer' else 'window_interval'
    hits = {i: [] for i in ids}
    for index, (row, interval) in enumerate(zip(rays, plan['ray_groups'])):
        if (type(row['index']) is not int or row['index'] != index or row[key] != interval
                or [p['id'] for p in row['hits']] != ids or not 0 < interval[1]-interval[0] <= 8):
            raise ValueError('Missing, reordered or foreign ray group')
        for probe in row['hits']:
            if not all(type(z) in (int, float) and math.isfinite(z) for z in probe['surface_z_mm']):
                raise ValueError('Nonfinite original ray observation')
            hits[probe['id']].extend(probe['surface_z_mm'])
    return hits


def planar_result(plan, surface, ref, policy, section_rows, hits):
    all_hits = [{**p, 'surface_z_mm': sorted(set(hits[p['id']]))} for p in plan['probes']]
    guard = 2*plan['resolution_mm']; features = {}; low = ref['bounds']['min']
    for feature in holes(ref):
        rows = [r['holes'][feature['id']] for r in section_rows]; complete = all(row['matched'] for row in rows)
        rays = [{'xy_mm': p['xy_mm'], 'surface_z_mm': p['surface_z_mm']} for p in all_hits if p['feature_id'] == feature['id']]
        features[feature['id']] = {'sections': rows, 'section_count': len(rows), 'all_sections_have_matched_hole': complete,
            'diameter': {'radial_band_mm': [min(s['radial_diameter_band_mm'][0] for s in rows), max(s['radial_diameter_band_mm'][1] for s in rows)],
                        'fit_range_mm': [min(s['fit_diameter_mm'] for s in rows), max(s['fit_diameter_mm'] for s in rows)]} if complete else None,
            'position': {'max_center_deviation_mm': max(s['center_deviation_mm'] for s in rows),
                         'centers_xy_mm': [s['fit_center_xy_mm'] for s in rows]} if complete else None,
            'through': summarize_rays(rays, guard), 'resolution_mm': plan['resolution_mm'], 'numerical_guard_mm': guard,
            'guard_is_certified_uncertainty': False, 'registration_applied': False}
    probes = []
    for probe in all_hits:
        if probe['role'] != 'retained_face':
            continue
        zs = probe['surface_z_mm']; valid = len(zs) == 2
        probes.append({'xy_mm': probe['xy_mm'], 'surface_z_mm': zs, 'status': 'measured' if valid else 'ambiguous_intersections',
            'top_z_mm': zs[-1] if valid else None, 'thickness_mm': zs[-1]-zs[0] if valid else None,
            'bottom_deviation_mm': abs(zs[0]-low[2]) if valid else None})
    contours = [r['face'] for r in section_rows]; complete = all(p['status'] == 'measured' for p in probes)
    contours_complete = all(c['boundary_deviation_mm'] is not None for c in contours)
    tops = [p['top_z_mm'] for p in probes] if complete else []; thicknesses = [p['thickness_mm'] for p in probes] if complete else []
    face = {'probes': probes, 'probe_count': len(probes), 'complete': complete, 'sections': contours, 'section_count': 11,
        'contours_complete': contours_complete, 'topology_matches': all(c['topology_matches'] for c in contours),
        'max_boundary_deviation_mm': max(c['boundary_deviation_mm'] for c in contours) if contours_complete else None,
        'top_z_band_mm': [min(tops), max(tops)] if complete else None,
        'thickness_band_mm': [min(thicknesses), max(thicknesses)] if complete else None,
        'top_flatness_range_mm': max(tops)-min(tops) if complete else None,
        'max_bottom_deviation_mm': max([p['bottom_deviation_mm'] for p in probes]+[abs(surface['bounds']['min'][2]-low[2])]) if complete else None,
        'mesh_max_z_mm': surface['bounds']['max'][2], 'resolution_mm': plan['resolution_mm'], 'numerical_guard_mm': guard,
        'guard_is_certified_uncertainty': False, 'registration_applied': False}
    features[ref['facing_feature']['id']] = face
    if 'profile_feature' in ref:
        waste = []
        for probe in all_hits:
            if probe['role'] != 'exterior_clearance':
                continue
            zs = probe['surface_z_mm']; lengths = [b-a for a, b in zip(zs[::2], zs[1::2])]
            state = 'clear' if not zs else 'ambiguous' if len(zs) % 2 else 'blocked' if max(lengths) > guard else 'sub_guard_signal'
            waste.append({'xy_mm': probe['xy_mm'], 'surface_z_mm': zs, 'observation': state,
                          'maximum_material_interval_mm': max(lengths, default=0)})
        if len(waste) != 20:
            raise ValueError('Missing original capsule exterior probes')
        fields = ['probes', 'probe_count', 'complete', 'sections', 'section_count', 'contours_complete', 'topology_matches',
            'max_boundary_deviation_mm', 'thickness_band_mm', 'resolution_mm', 'numerical_guard_mm',
            'guard_is_certified_uncertainty', 'registration_applied']
        item = {k: face[k] for k in fields}; high = ref['bounds']['max']
        item.update(max_plane_deviation_mm=max(face['max_bottom_deviation_mm'],
            *[abs(z-high[2]) for z in face['top_z_band_mm']], abs(face['mesh_max_z_mm']-high[2])) if face['complete'] else None,
            exterior_probes=waste, exterior_probe_count=len(waste), exterior_all_clear=all(p['observation'] == 'clear' for p in waste),
            disconnected_exterior_material_discarded=False,
            probe_selection='Original capsule retained solid, excluding all intended holes; never adapted to simulated material.')
        features[ref['profile_feature']['id']] = item
    if set(features) != set(policy['features']):
        raise ValueError('Original policy does not cover the exact measured features')
    checks = [{'feature_id': fid, 'kind': kind, **planar_evaluate(kind, features[fid], rule)}
              for fid, rules in policy['features'].items() for kind, rule in rules.items()]
    unexpected = any(r['counts']['unassigned_contours'] for r in section_rows) or not face['topology_matches']
    return {'software_regression': checks,
        'observations': {'features': features, 'unassigned_contours_detected': unexpected,
                         'section_feature_counts': [r['counts'] for r in section_rows]},
        'all_software_geometry_checks_passed': all(r['status'] == 'numeric_pass' for r in checks) and not unexpected}


def chamfer_result(plan, surface, reference, policy, sections, hits):
    obj = chamfer_target(reference); lo, hi = obj.low, obj.high
    contours = [row['contour'] for row in sections]
    midlines = [row['midline'] for row in sections if row['midline'] is not None]
    if len(midlines) != 11:
        raise ValueError('Missing original chamfer midlines')
    widths = []; width_complete = all(row['status'] == 'measured' for row in midlines)
    if width_complete:
        zs = np.array([row['z_mm']-hi[2] for row in midlines])
        for edge in ['xmin', 'xmax', 'ymin', 'ymax']:
            values = np.array([row['insets_mm'][edge] for row in midlines]); slope, intercept = np.polyfit(zs, values, 1)
            widths.append({'edge': edge, 'extrapolated_top_width_mm': float(intercept), 'nominal_width_mm': obj.width,
                'width_error_mm': abs(float(intercept)-obj.width),
                'max_fit_residual_mm': float(max(abs(values-(slope*zs+intercept)))),
                'estimated_angle_degrees': math.degrees(math.atan2(1, float(slope))), 'angle_manufacturing_acceptance': False})
    probes = []
    for probe in plan['probes']:
        zs = sorted(set(hits[probe['id']])); valid = len(zs) == 2
        probes.append({'xy_mm': probe['xy_mm'], 'surface_z_mm': zs, 'status': 'measured' if valid else 'ambiguous_intersections',
            'thickness_mm': zs[-1]-zs[0] if valid else None,
            'plane_deviation_mm': max(abs(zs[0]-lo[2]), abs(zs[-1]-hi[2])) if valid else None})
    complete = all(p['status'] == 'measured' for p in probes)
    contours_complete = all(row['boundary_deviation_mm'] is not None for row in contours)
    thickness = [p['thickness_mm'] for p in probes] if complete else []
    item = {'sections': contours, 'section_count': len(contours), 'bevel_section_count': 11,
        'contours_complete': contours_complete, 'topology_matches': all(row['topology_matches'] for row in contours),
        'max_boundary_deviation_mm': max(row['boundary_deviation_mm'] for row in contours) if contours_complete else None,
        'midlines': midlines, 'widths': widths, 'width_complete': width_complete,
        'max_width_deviation_mm': max(max(row['width_error_mm'], row['max_fit_residual_mm']) for row in widths) if width_complete else None,
        'probes': probes, 'probe_count': 25, 'complete': complete,
        'thickness_band_mm': [min(thickness), max(thickness)] if complete else None,
        'max_plane_deviation_mm': max([p['plane_deviation_mm'] for p in probes]+
            [abs(surface['bounds']['min'][2]-lo[2]), abs(surface['bounds']['max'][2]-hi[2])]) if complete else None,
        'resolution_mm': plan['resolution_mm'], 'numerical_guard_mm': 2*plan['resolution_mm'],
        'guard_is_certified_uncertainty': False, 'registration_applied': False, 'disconnected_material_discarded': False}
    fid = reference['features'][0]['id']
    if set(policy['features']) != {fid}:
        raise ValueError('Original policy does not cover the exact chamfer')
    checks = [{'feature_id': fid, 'kind': kind, **chamfer_evaluate(kind, item, rule)} for kind, rule in policy['features'][fid].items()]
    return {'observations': {'features': {fid: item}, 'unassigned_contours_detected': not item['topology_matches']},
            'software_regression': checks,
            'all_software_geometry_checks_passed': all(row['status'] == 'numeric_pass' for row in checks)}


def assemble_observations(kind, plan, surface, reference, policy, sections, rays):
    require_bounded_worker()
    if kind == 'chamfer':
        if len(plan['probes']) != 25:
            raise ValueError('Missing original retained-top probes')
    elif kind == 'planar':
        expected = {('retained_face', reference['facing_feature']['id']): 25}
        expected.update({('through', feature['id']): 9 for feature in holes(reference)})
        if 'profile_feature' in reference:
            expected['exterior_clearance', reference['profile_feature']['id']] = 20
        observed = {}
        for probe in plan['probes']:
            key = probe['role'], probe['feature_id']
            observed[key] = observed.get(key, 0)+1
        if observed != expected:
            raise ValueError('Missing or foreign original feature probes')
    hits = checked_parts(kind, plan, sections, rays)
    if kind == 'chamfer':
        return chamfer_result(plan, surface, reference, policy, sections, hits)
    return planar_result(plan, surface, reference, policy, sections, hits)
