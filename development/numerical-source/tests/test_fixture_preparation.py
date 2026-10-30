import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from genlaw_pipeline.preparation import prepare, digest
from genlaw_pipeline.coordinate_setup import setup_requirements
from genlaw_pipeline.fixture_setup import enabled, FILE_KEY
from genlaw_pipeline.fixture_runtime import selected_observer

ROOT=Path(__file__).resolve().parents[1]
INPUTS=ROOT/'validation/feature-inputs/S4-FIXTURE-CLEARANCE-20261030-r3'
load=lambda p:json.loads(p.read_text())


class FixturePreparationTests(unittest.TestCase):
    def setUp(self):
        root=ROOT/'.runtime/fixture-preparation-tests';root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.tmp.cleanup)
        self.case=Path(self.tmp.name)/'input';shutil.copytree(INPUTS/'clear-jaws',self.case)
        self.entry=self.case/'input.json'

    def edit(self,key,change):
        entry=load(self.entry);p=self.case/entry['files'][key]['path'];data=load(p);change(data)
        p.write_text(json.dumps(data,ensure_ascii=False));entry['files'][key]['sha256']=digest(p)
        self.entry.write_text(json.dumps(entry,ensure_ascii=False))

    def edit_model_with_card_and_resource_binding(self,change):
        self.edit(FILE_KEY,change);sha=load(self.entry)['files'][FILE_KEY]['sha256']
        self.edit('resources',lambda r:r['setup'][FILE_KEY].update(sha256=sha))
        setup=load(self.case/'resources-software-reference.json')['setup']
        self.edit('process_plan',lambda c:c['operations'][1]['steps'][0]['constraints'].update(coordinate_setup=setup_requirements(setup)))

    def test_all_five_archived_inputs_require_inspection_before_material_removal(self):
        for entry in INPUTS.glob('*/input.json'):
            result=prepare(entry)
            self.assertEqual(result['status'],'ready_for_declared_scope',result['issues'])
            self.assertEqual(result['execution_plan'],load(entry.parent/'preparation-preview.json')['execution_plan'])
            plan=result['execution_plan'];self.assertTrue(enabled(plan))
            self.assertEqual(plan['fixture_inspection']['stage'],'after_native_nc_before_material_removal')
            self.assertTrue(plan['fixture_inspection']['collision_or_indeterminate_blocks_material_simulation'])
            self.assertEqual(plan['fixture_inspection']['minimum_clearance_mm'],.05)
            self.assertEqual(plan['fixture_inspection']['model_ref']['sha256'],digest(entry.parent/'fixture-model.json'))

    def test_twelve_previous_coordinate_tool_length_and_pocket_plans_are_identical(self):
        count=0
        for prefix in ['WORK-COORDINATE','TOOL-LENGTH','POCKET-SEQUENCE']:
            runs=ROOT/f'validation/runs/S4-{prefix}-SUITE-20261030-r1'
            for request in runs.glob('*/request.json'):
                entry=Path(load(request)['input'])
                result=prepare(entry);self.assertEqual(result['status'],'ready_for_declared_scope',result['issues'])
                self.assertEqual(result['execution_plan'],load(runs/entry.parent.name/'execution-plan.json'))
                self.assertNotIn('fixture_inspection',result['execution_plan']);count+=1
        self.assertEqual(count,12)

    def test_missing_fixture_geometry_cannot_silently_disable_the_check(self):
        entry=load(self.entry);entry['files'].pop(FILE_KEY);self.entry.write_text(json.dumps(entry))
        self.assertEqual(prepare(self.entry)['status'],'missing_information')

    def test_model_change_requires_matching_card_and_resource_revision(self):
        self.edit(FILE_KEY,lambda m:m['fixtures'][0]['bounds_mm']['max'].__setitem__(0,-.7))
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_consistently_forged_cutting_radius_cannot_replace_cam_tool(self):
        self.edit_model_with_card_and_resource_binding(lambda m:m['tools'][0]['segments'][0].update(radius_mm=.4))
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_consistently_forged_actual_length_is_not_inferred_from_envelope(self):
        def change(m):
            m['tools'][0]['gauge_to_tip_mm']=31;m['tools'][0]['segments'][-1]['z_max_mm']=31
        self.edit_model_with_card_and_resource_binding(change)
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_unmodeled_shank_gap_is_not_ignored(self):
        self.edit_model_with_card_and_resource_binding(lambda m:m['tools'][0]['segments'][1].update(z_min_mm=5.1))
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_fixture_embedded_inside_raw_stock_is_rejected(self):
        self.edit_model_with_card_and_resource_binding(lambda m:m['fixtures'][0].update(bounds_mm={'min':[1,1,-1],'max':[2,2,-.5]}))
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_top_surface_contact_is_not_volumetric_overlap(self):
        entry=INPUTS/'cutting-clamp/input.json'
        self.assertEqual(prepare(entry)['status'],'ready_for_declared_scope')

    def test_global_exclusion_cannot_contradict_required_fixture_check(self):
        entry=load(self.entry);entry['scope']['excluded_checks'].append('fixture_collision');self.entry.write_text(json.dumps(entry))
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_original_frame_is_not_replaced_by_machine_frame(self):
        self.edit_model_with_card_and_resource_binding(lambda m:m.update(frame='machine'))
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_ignored_shape_extensions_are_rejected(self):
        self.edit_model_with_card_and_resource_binding(lambda m:m['fixtures'][0].update(rotation_degrees=[0,0,45]))
        self.assertEqual(prepare(self.entry)['status'],'conflicting_information')

    def test_fixture_source_document_must_exist(self):
        entry=load(self.entry);entry['source_documents'].pop('FIXTURE');self.entry.write_text(json.dumps(entry))
        self.assertEqual(prepare(self.entry)['status'],'missing_information')

    def test_new_observer_selection_is_independently_qualified(self):
        binary,env,selection,manifest=selected_observer()
        self.assertTrue(binary.is_file());self.assertEqual(selection['engine']['sha256'],digest(manifest))
        from genlaw_pipeline.simulation_engine import selected_engine
        self.assertNotEqual(binary,selected_engine()['binary'])


if __name__=='__main__':unittest.main()
