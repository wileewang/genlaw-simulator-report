import copy
import math
import unittest
from shapely.affinity import translate
from shapely.geometry import Point, box
from shapely.ops import unary_union
from genlaw_pipeline.planar_dimensions import (
    POLICY, compile_requirements, observe_section, evaluate_dimensions, evaluate_band,
)


def reference(axis='X', center=(50., 7.), length=100., width=14., holes=True):
    major = 'XY'.index(axis)
    outline = {'id': 'C1', 'kind': 'capsule_outline', 'major_axis': axis,
               'center_xy_mm': list(center), 'length_mm': length, 'width_mm': width}
    positions = []
    if holes:
        for i, fraction in enumerate((.1, .5, .9)):
            xy = list(center); xy[major] += (fraction - .5) * length
            positions.append({'id': 'H' + str(i + 1), 'kind': 'through_hole',
                              'center_xy_mm': xy, 'diameter_mm': width * .5})
    return {'cad_sha256': 'a' * 64, 'profile_feature': outline, 'features': positions}


def shape(ref, radius_delta=0., hole_moves=None):
    """Independent analytic union of two disks and a straight rectangle."""
    p = ref['profile_feature']; axis = 'XY'.index(p['major_axis'])
    radius = p['width_mm'] / 2 + radius_delta
    low = list(p['center_xy_mm']); high = list(low)
    low[axis] -= (p['length_mm'] - p['width_mm']) / 2
    high[axis] += (p['length_mm'] - p['width_mm']) / 2
    bounds = [min(low[0], high[0]), min(low[1], high[1]), max(low[0], high[0]), max(low[1], high[1])]
    bounds[1 - axis] -= radius; bounds[3 - axis] += radius
    body = unary_union([Point(*low).buffer(radius, quad_segs=512),
                        Point(*high).buffer(radius, quad_segs=512), box(*bounds)])
    for hole in ref['features']:
        xy = list(hole['center_xy_mm']); delta = (hole_moves or {}).get(hole['id'], (0., 0.))
        body = body.difference(Point(xy[0] + delta[0], xy[1] + delta[1]).buffer(hole['diameter_mm'] / 2, quad_segs=512))
    return body


def request(ref):
    p = ref['profile_feature']; axis = p['major_axis']; major = 'XY'.index(axis)
    items = [
        {'id': 'length', 'kind': 'support_span', 'axis': axis, 'nominal_mm': p['length_mm'], 'tol': .3},
        {'id': 'width', 'kind': 'support_span', 'axis': 'YX'[major], 'nominal_mm': p['width_mm'], 'tol': .2},
        *[{'id': side + '-radius', 'kind': 'end_radius', 'axis': axis, 'side': side,
           'nominal_mm': p['width_mm'] / 2, 'tol': .2} for side in ('low', 'high')],
    ]
    if ref['features']:
        items.extend([
            {'id': 'right-offset', 'kind': 'end_offset', 'axis': axis, 'side': 'high', 'hole_id': 'H3',
             'nominal_mm': p['length_mm'] * .1, 'tol': .2},
            *[{'id': 'pitch-' + str(i), 'kind': 'center_pitch', 'axis': axis, 'hole_ids': ['H' + str(i), 'H' + str(i + 1)],
               'nominal_mm': p['length_mm'] * .4, 'tol': .3} for i in (1, 2)],
        ])
    for item in items:
        item['profile_id'] = 'C1'; tol = item.pop('tol')
        item['acceptance'] = {'schema': 'genlaw.numeric-acceptance.v1', 'scope': 'software_regression',
            'units': 'mm', 'basis': 'Predeclared analytic observer qualification', 'method': item['kind'],
            'min_mm': item['nominal_mm'] - tol, 'max_mm': item['nominal_mm'] + tol}
    return {'schema': 'genlaw.planar-dimension-requirements.v1', 'units': 'mm',
            'cad_sha256': ref['cad_sha256'], 'measurement_policy': copy.deepcopy(POLICY), 'items': items}


class PlanarDimensionTests(unittest.TestCase):
    def measure(self, ref=None, actual=None):
        ref = ref or reference(); actual = shape(ref) if actual is None else actual
        observation = observe_section(actual, ref, .025)
        result = evaluate_dimensions(request(ref), ref, [observation] * 11, .025)
        return observation, {r['id']: r for r in result['requirements']}, result

    def test_independent_analytic_dimensions(self):
        obs, rows, result = self.measure()
        self.assertTrue(result['dimension_inspection_passed'])
        self.assertEqual(len(rows), 7)
        for row in rows.values():
            self.assertAlmostEqual(sum(row['observed_band_mm']) / 2, row['nominal_mm'], places=4)
        self.assertGreater(obs['ends']['low']['coverage_degrees'], 179)
        self.assertFalse(result['production_approved'])

    def test_y_axis_and_nonzero_original_coordinates(self):
        ref = reference('Y', (-31., 125.))
        _, rows, result = self.measure(ref)
        self.assertTrue(result['dimension_inspection_passed'])
        self.assertAlmostEqual(rows['right-offset']['observed_band_mm'][0], 10, places=5)

    def test_measured_radius_is_not_assigned_from_nominal_width(self):
        ref = reference(); obs, rows, result = self.measure(ref, shape(ref, radius_delta=.5))
        self.assertAlmostEqual(obs['ends']['low']['fit_radius_mm'], 7.5, places=4)
        self.assertEqual(rows['low-radius']['status'], 'numeric_fail')
        self.assertEqual(rows['high-radius']['status'], 'numeric_fail')
        self.assertEqual(rows['width']['status'], 'numeric_fail')
        self.assertFalse(result['dimension_inspection_passed'])

    def test_right_end_datum_does_not_become_left_hole_offset(self):
        ref = reference(); _, rows, _ = self.measure(ref, shape(ref, hole_moves={'H1': (-1., 0.)}))
        self.assertEqual(rows['right-offset']['status'], 'numeric_pass')
        self.assertEqual(rows['pitch-1']['status'], 'numeric_fail')
        _, rows, _ = self.measure(ref, shape(ref, hole_moves={'H3': (1., 0.)}))
        self.assertEqual(rows['right-offset']['status'], 'numeric_fail')
        self.assertEqual(rows['pitch-2']['status'], 'numeric_fail')

    def test_projected_pitch_is_not_euclidean_hole_distance(self):
        ref = reference(); _, rows, _ = self.measure(ref, shape(ref, hole_moves={'H2': (0., .5)}))
        self.assertAlmostEqual(rows['pitch-1']['observed_band_mm'][0], 40, places=5)
        self.assertEqual(rows['pitch-1']['status'], 'numeric_pass')
        # Original CAD hole-position verification is separate and must still reject the Y shift.

    def test_relative_dimensions_do_not_register_the_material_to_cad(self):
        ref = reference(); actual = translate(shape(ref), xoff=.2, yoff=.1)
        obs, rows, _ = self.measure(ref, actual)
        self.assertAlmostEqual(obs['support_bounds_xy_mm'][0], .2, places=6)
        self.assertAlmostEqual(rows['right-offset']['observed_band_mm'][0], 10, places=5)
        self.assertFalse(obs['registration_applied'])

    def test_missing_hole_is_not_replaced_by_a_neighbor(self):
        ref = reference(); actual = shape(ref).union(Point(*ref['features'][2]['center_xy_mm']).buffer(3.6, quad_segs=512))
        obs, rows, result = self.measure(ref, actual)
        self.assertFalse(obs['complete_topology'])
        self.assertEqual(rows['right-offset']['status'], 'not_measurable')
        self.assertFalse(result['dimension_inspection_passed'])

    def test_extra_hole_is_not_discarded_when_dimensions_pass(self):
        ref = reference(); actual = shape(ref).difference(Point(30, 7).buffer(.5, quad_segs=128))
        obs, rows, result = self.measure(ref, actual)
        self.assertTrue(result['all_declared_dimensions_passed'])
        self.assertEqual(obs['unassigned_contours'], 1)
        self.assertFalse(result['dimension_inspection_passed'])

    def test_detached_piece_is_not_removed(self):
        ref = reference(); actual = shape(ref).union(box(120, 0, 121, 1))
        obs, rows, result = self.measure(ref, actual)
        self.assertEqual(obs['body_count'], 2)
        self.assertTrue(all(r['status'] == 'not_measurable' for r in rows.values()))
        self.assertFalse(result['dimension_inspection_passed'])

    def test_rectangle_corners_cannot_pass_as_end_radii(self):
        ref = reference(holes=False); obs, rows, result = self.measure(ref, box(0, 0, 100, 14))
        self.assertEqual(rows['length']['status'], 'numeric_pass')
        self.assertEqual(rows['low-radius']['status'], 'not_measurable')
        self.assertFalse(result['dimension_inspection_passed'])

    def test_all_height_observations_contribute(self):
        ref = reference(); normal = observe_section(shape(ref), ref, .025)
        defective = observe_section(shape(ref, hole_moves={'H3': (1., 0.)}), ref, .025)
        result = evaluate_dimensions(request(ref), ref, [normal] * 10 + [defective], .025)
        row = next(r for r in result['requirements'] if r['id'] == 'right-offset')
        self.assertEqual(row['status'], 'numeric_fail')
        self.assertAlmostEqual(row['observed_band_mm'][0], 9, places=5)
        self.assertAlmostEqual(row['observed_band_mm'][1], 10, places=5)

    def test_guard_keeps_borderline_indeterminate(self):
        rule = {'min_mm': 9.8, 'max_mm': 10.2}
        for band, expected in [([10, 10], 'numeric_pass'), ([10.15, 10.15], 'indeterminate'),
                               ([10.31, 10.31], 'numeric_fail'), ([9.69, 10], 'numeric_fail')]:
            self.assertEqual(evaluate_band(band, rule, .1)['status'], expected)
        self.assertEqual(evaluate_band([10, 10], None, .1)['status'], 'pending_criteria')
        self.assertEqual(evaluate_band(None, rule, .1)['status'], 'not_measurable')

    def test_wrong_datum_nominal_and_duplicate_id_are_rejected(self):
        ref = reference(); req = request(ref)
        req['items'][4]['hole_id'] = 'H1'
        with self.assertRaisesRegex(ValueError, 'nominal disagrees'): compile_requirements(req, ref)
        req = request(ref); req['items'].append(copy.deepcopy(req['items'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate requirement'): compile_requirements(req, ref)

    def test_foreign_cad_changed_policy_and_missing_heights_rejected(self):
        ref = reference(); req = request(ref); req['cad_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'bind the original'): compile_requirements(req, ref)
        req = request(ref); req['measurement_policy']['radius_guard_cells'] = 0
        with self.assertRaisesRegex(ValueError, 'fixed policy'): compile_requirements(req, ref)
        with self.assertRaisesRegex(ValueError, 'eleven'): evaluate_dimensions(request(ref), ref, [], .025)

    def test_unknown_criteria_and_nonfinite_values_rejected(self):
        ref = reference(); req = request(ref); req['items'][0]['acceptance']['method'] = 'iou'
        with self.assertRaisesRegex(ValueError, 'criterion'): compile_requirements(req, ref)
        req = request(ref); req['items'][0]['acceptance']['max_mm'] = math.inf
        with self.assertRaisesRegex(ValueError, 'finite'): compile_requirements(req, ref)


if __name__ == '__main__': unittest.main()
