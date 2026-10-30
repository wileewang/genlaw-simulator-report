import copy
import math
import unittest

from genlaw_pipeline.fixture_clearance import (
    classify_sweep, cylinder_box_minimum, inspect_path, parameter_interval,
    segment_rectangle_minimum, validate_model, validate_path,
)


def model():
    return {'schema': 'genlaw.fixture-clearance-model.v1', 'units': 'mm',
            'frame': 'original_cad_material', 'scope': 'modeled_tool_assembly_vs_static_fixtures',
            'minimum_clearance_mm': .05, 'basis': 'Independent analytic fixture, not a physical machining approval.',
            'tools': [{'tool_number': 1, 'gauge_to_tip_mm': 3., 'segments': [
                {'id': 'tip', 'role': 'cutting', 'radius_mm': .5, 'z_min_mm': 0., 'z_max_mm': 1.},
                {'id': 'shank', 'role': 'shank', 'radius_mm': .5, 'z_min_mm': 1., 'z_max_mm': 2.},
                {'id': 'holder', 'role': 'holder', 'radius_mm': 1., 'z_min_mm': 2., 'z_max_mm': 3.}]}],
            'fixtures': [{'id': 'wall', 'shape': 'box', 'bounds_mm': {'min': [-.1, -.1, .2], 'max': [.1, .1, .8]}}]}


def path(start, end, kind='cutting'):
    return {'schema': 'genlaw.native-toolpath.v1', 'units': 'mm', 'frame': 'original_cad_material_tip',
            'nc_executed': True, 'surface_computed': False, 'native_max_arc_error_mm': .01,
            'moves': [{'index': 0, 'line': 19, 'tool_number': 1, 'type': kind,
                       'start_mm': start, 'end_mm': end,
                       'gauge_start_mm': [start[0]+10, start[1]+20, start[2]+33],
                       'gauge_end_mm': [end[0]+10, end[1]+20, end[2]+33]}]}


class ContinuousSweptCylinderTests(unittest.TestCase):
    def setUp(self):
        self.segment = model()['tools'][0]['segments'][0]
        self.box = model()['fixtures'][0]['bounds_mm']

    def test_line_crosses_thin_fixture_between_clear_endpoints(self):
        for x in (-100, 100):
            self.assertEqual(classify_sweep([x, 0, 0], [x, 0, 0], self.segment, self.box, .05, .01)['status'], 'clear')
        result = classify_sweep([-100, 0, 0], [100, 0, 0], self.segment, self.box, .05, .01)
        self.assertEqual(result['status'], 'collision')
        self.assertLess(abs(result['witness']['parameter']-.5), .001)

    def test_diagonal_corner_distance_is_not_axis_aligned_envelope(self):
        box = {'min': [0, 0, 0], 'max': [1, 1, 1]}
        near = segment_rectangle_minimum([2, 0, 0], [0, 2, 0], box, (0, 1))
        far = segment_rectangle_minimum([3, 0, 0], [0, 3, 0], box, (0, 1))
        self.assertEqual(near['minimum_squared_xy_mm2'], 0.)
        self.assertAlmostEqual(far['minimum_squared_xy_mm2'], .5)
        self.assertEqual(far['parameter'], .5)

    def test_height_and_xy_overlap_must_occur_at_same_time(self):
        # XY reaches the wall only after the tool has risen past it.
        result = classify_sweep([-10, 0, 0], [0, 0, 10], self.segment, self.box, .05, .01)
        self.assertEqual(result['status'], 'clear')

    def test_vertical_plunge_into_obstacle_detected(self):
        result = classify_sweep([0, 0, 5], [0, 0, -5], self.segment, self.box, .05, .01)
        self.assertEqual(result['status'], 'collision')

    def test_height_only_clearance_and_contact(self):
        for z, wanted in [(1., 'clear'), (.8, 'indeterminate'), (.7, 'collision')]:
            result = classify_sweep([0, 0, z], [0, 0, z], self.segment, self.box, .05, .01)
            self.assertEqual(result['status'], wanted, z)

    def test_small_radial_gap_stays_indeterminate(self):
        for y, wanted in [(.7, 'clear'), (.63, 'indeterminate'), (.6, 'indeterminate'), (.58, 'collision')]:
            result = classify_sweep([-2, y, 0], [2, y, 0], self.segment, self.box, .05, .01)
            self.assertEqual(result['status'], wanted, y)

    def test_endpoint_hit_is_included(self):
        self.assertEqual(classify_sweep([-2, 0, 0], [0, 0, 0], self.segment, self.box, .05, .01)['status'], 'collision')

    def test_reverse_direction_preserves_classification(self):
        a, b = [-4, -.2, -2], [4, .2, 2]
        first = cylinder_box_minimum(a, b, self.segment, self.box)
        reverse = cylinder_box_minimum(b, a, self.segment, self.box)
        self.assertAlmostEqual(first['radial_gap_mm'], reverse['radial_gap_mm'])
        self.assertEqual(classify_sweep(a, b, self.segment, self.box, .05, .01)['status'],
                         classify_sweep(b, a, self.segment, self.box, .05, .01)['status'])

    def test_stationary_axes_and_degenerate_time_interval(self):
        self.assertEqual(parameter_interval(3, 3, 2, 4), (0, 1))
        self.assertIsNone(parameter_interval(3, 3, 4, 5))
        self.assertEqual(parameter_interval(0, 2, 2, 3), (1, 1))
        result = segment_rectangle_minimum([2, 2, 0], [2, 2, 0], self.box, (.5, .5))
        self.assertAlmostEqual(result['axis_to_rectangle_mm'], 1.9*math.sqrt(2))

    def test_arc_error_prevents_unsafe_clearance_claim(self):
        p = [0, .655, 0]
        exact = classify_sweep(p, p, self.segment, self.box, .05, 0)
        conservative = classify_sweep(p, p, self.segment, self.box, .05, .01)
        self.assertEqual(exact['status'], 'clear')
        self.assertEqual(conservative['status'], 'indeterminate')


class FixtureResourceAndCoverageTests(unittest.TestCase):
    def test_all_motion_segments_and_components_are_counted(self):
        result = inspect_path(path([-100, 0, 0], [100, 0, 0]), model(), [10, 20, 30])
        self.assertEqual(result['status'], 'collision')
        self.assertEqual(result['tested_pairs'], 3)
        self.assertEqual(sum(result['pair_counts'].values()), 3)
        self.assertEqual(result['findings'][0]['first_nc_line'], 19)
        self.assertFalse(result['endpoint_only_sampling'])

    def test_rapid_collision_is_checked_like_cutting(self):
        result = inspect_path(path([-100, 0, 0], [100, 0, 0], 'rapid'), model(), [10, 20, 30])
        self.assertEqual(result['status'], 'collision')
        self.assertEqual(result['findings'][0]['first_motion_type'], 'rapid')

    def test_holder_only_collision_is_distinguished(self):
        config = model(); config['fixtures'][0]['bounds_mm'] = {'min': [-.1, .7, 2.2], 'max': [.1, .8, 2.8]}
        result = inspect_path(path([-2, 0, 0], [2, 0, 0]), config, [10, 20, 30])
        self.assertEqual(result['status'], 'collision')
        self.assertEqual([r['role'] for r in result['findings']], ['holder'])

    def test_partial_or_wrong_frame_export_cannot_pass(self):
        for key, value in [('nc_executed', False), ('surface_computed', True), ('frame', 'registered_to_target'), ('native_max_arc_error_mm', 0)]:
            p = path([-2, 0, 0], [2, 0, 0]); p[key] = value
            with self.assertRaises(ValueError): validate_path(p, model(), [10, 20, 30])

    def test_unknown_tool_and_wrong_actual_length_are_rejected(self):
        p = path([-2, 0, 0], [2, 0, 0]); p['moves'][0]['tool_number'] = 2
        with self.assertRaises(ValueError): validate_path(p, model(), [10, 20, 30])
        p['moves'][0]['tool_number'] = 1
        p['moves'][0]['gauge_end_mm'][2] += .2
        with self.assertRaises(ValueError): validate_path(p, model(), [10, 20, 30])

    def test_unmodeled_tool_segment_gap_is_rejected(self):
        for field, value in [('z_min_mm', 1.1), ('z_min_mm', .9), ('z_max_mm', 1.)]:
            m = model(); m['tools'][0]['segments'][1][field] = value
            with self.assertRaises(ValueError): validate_model(m)

    def test_unknown_resource_fields_and_nonfinite_values_are_rejected(self):
        for mutation in [lambda m: m.update(ignored_obstacles=[]),
                         lambda m: m.update(minimum_clearance_mm=float('nan')),
                         lambda m: m['fixtures'][0].update(shape='approximate_box'),
                         lambda m: m['tools'][0]['segments'][0].update(radius_mm=-1),
                         lambda m: m['fixtures'][0]['bounds_mm']['max'].__setitem__(2, .2)]:
            m = model(); mutation(m)
            with self.assertRaises(ValueError): validate_model(m)

    def test_native_gauge_discontinuity_is_not_interpolated(self):
        p = path([-2, 0, 0], [2, 0, 0]); other = copy.deepcopy(p['moves'][0])
        other['index'] = 1; p['moves'].append(other)
        with self.assertRaisesRegex(ValueError, 'discontinuity'): validate_path(p, model(), [10, 20, 30])

    def test_empty_spindle_is_explicitly_excluded(self):
        p = path([-2, 0, 0], [2, 0, 0]); loaded = p['moves'][0]
        empty = copy.deepcopy(loaded); empty.update(tool_number=-1, type='rapid')
        empty['start_mm'] = [-2, 0, 3]; empty['end_mm'] = [-2, 0, 3]
        empty['gauge_end_mm'] = empty['gauge_start_mm']
        loaded['index'] = 1; p['moves'].insert(0, empty)
        result = inspect_path(p, model(), [10, 20, 30])
        self.assertEqual(result['unloaded_spindle_moves_excluded'], 1)
        self.assertEqual(result['tested_pairs'], 3)


if __name__ == '__main__':
    unittest.main()
