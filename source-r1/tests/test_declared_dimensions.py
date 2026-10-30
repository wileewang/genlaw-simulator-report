import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from genlaw_pipeline.preparation import prepare,digest,canonical
from genlaw_pipeline.programming import checked_plan
from genlaw_pipeline.dimension_contract import compile_requirements as compile_input,POLICY
from genlaw_pipeline.planar_dimensions import compile_requirements as compile_observer

ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/'validation/feature-inputs/S4-DIMENSION-CHAIN-20261030-r2'
LOAD=lambda p:json.loads(p.read_text())


class DeclaredDimensionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.runtime/tmp');self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'case';shutil.copytree(INPUT/'two-holes',self.folder)
        self.entry=self.folder/'input.json'

    def change_manifest(self,operation):
        data=LOAD(self.entry);operation(data);self.entry.write_text(json.dumps(data))

    def change_requirements(self,operation):
        data=LOAD(self.folder/'dimension-requirements.json');operation(data)
        (self.folder/'dimension-requirements.json').write_text(json.dumps(data))
        self.change_manifest(lambda m:m['files']['dimension_requirements'].update(sha256=digest(self.folder/'dimension-requirements.json')))

    def blocked(self,code,field=None):
        result=prepare(self.entry);self.assertIsNone(result['execution_plan'])
        self.assertTrue(any(i['code']==code and (field is None or field in i['field']) for i in result['issues']),result)

    def test_all_four_declarations_bind_before_execution(self):
        for case in LOAD(INPUT/'cases.json')['cases']:
            result=prepare(ROOT/case['input']['path']);self.assertEqual(result['status'],'ready_for_declared_scope')
            plan=result['execution_plan'];declaration=plan['declared_inspection']
            self.assertEqual(declaration['cad_sha256'],plan['cad_ref']['sha256'])
            self.assertEqual(len(declaration['requirement_ids']),case['dimension_count'])
            self.assertEqual(declaration['requirements_ref'],result['input_bindings']['dimension_requirements'])
            self.assertEqual(set(declaration['source_documents']),{'DIMENSIONS'})
            self.assertFalse(plan['production_approved'])

    def test_existing_execution_plans_remain_identical(self):
        runs=[c['source_run'] for c in LOAD(INPUT/'cases.json')['cases']]+[
            'validation/runs/S1-CHAIN-20261028-r3',
            'validation/runs/S4-MULTI-POCKET-SUITE-20261030-r1/equal-depth',
            'validation/runs/S4-CONNECTION-REFERENCE-20261029-r1']
        for name in runs:
            run=ROOT/name;input_path=Path(LOAD(run/'request.json')['input'])
            self.assertEqual(canonical(checked_plan(input_path,run/'preparation-result.json')),
                             canonical(LOAD(run/'execution-plan.json')))

    def test_orphan_attachment_is_not_silently_ignored(self):
        self.change_manifest(lambda m:m.pop('inspection'))
        self.blocked('unsupported_capability','inspection')

    def test_declared_missing_attachment_is_blocked(self):
        self.change_manifest(lambda m:m['files'].pop('dimension_requirements'))
        self.blocked('missing_information','dimension_requirements')

    def test_foreign_cad_cannot_supply_acceptance(self):
        self.change_requirements(lambda r:r.update(cad_sha256='f'*64))
        self.blocked('conflicting_information','inspection')

    def test_wrong_end_hole_cannot_reuse_a_symmetric_distance(self):
        self.change_requirements(lambda r:next(d for d in r['items'] if d['kind']=='end_offset').update(hole_id='H1'))
        self.blocked('conflicting_information','inspection')

    def test_changed_axis_nominal_is_blocked(self):
        self.change_requirements(lambda r:r['items'][0].update(axis='Y'))
        self.blocked('conflicting_information','inspection')

    def test_missing_source_document_is_blocked(self):
        self.change_requirements(lambda r:r['items'][0]['source'].update(document_id='UNKNOWN'))
        self.blocked('missing_information','source')

    def test_invalid_source_page_is_blocked(self):
        self.change_requirements(lambda r:r['items'][0]['source'].update(page=0))
        self.blocked('invalid_input','source')

    def test_extra_criterion_constraints_are_not_ignored(self):
        self.change_requirements(lambda r:r['items'][0]['acceptance'].update(confidence=.999))
        self.blocked('unsupported_capability','acceptance')

    def test_nominal_outside_acceptance_is_blocked(self):
        self.change_requirements(lambda r:r['items'][0]['acceptance'].update(min_mm=8.1,max_mm=8.5))
        self.blocked('conflicting_information','acceptance')

    def test_impossible_resolution_does_not_start_native_work(self):
        self.change_requirements(lambda r:r['items'][0]['acceptance'].update(min_mm=7.99,max_mm=8.01))
        self.blocked('unsupported_capability','resolution')

    def test_duplicate_across_requirement_documents_is_blocked(self):
        prior=LOAD(self.folder/'requirements.json')['items'][0]['id']
        self.change_requirements(lambda r:r['items'][0].update(id=prior))
        self.blocked('conflicting_information','inspection')

    def test_pending_criterion_remains_unapproved(self):
        self.change_requirements(lambda r:r['items'][0].update(acceptance=None))
        result=prepare(self.entry);self.assertEqual(result['status'],'ready_for_declared_scope')
        item=next(r for r in result['requirement_readiness'] if r['id']=='length')
        self.assertFalse(item['acceptance_defined']);self.assertEqual(item['verification_status'],'not_verified')
        self.assertTrue(any(i['severity']=='acceptance' and i['field']=='inspection/length/acceptance' for i in result['issues']))
        self.assertFalse(result['production_approved'])

    def test_changed_attachment_after_preparation_is_rejected(self):
        result=prepare(self.entry);frozen=self.folder/'prepared.json';frozen.write_text(json.dumps(result))
        self.change_requirements(lambda r:r['items'][0]['source'].update(location='different transcription'))
        with self.assertRaises(ValueError):checked_plan(self.entry,frozen)

    def test_observer_and_lightweight_contracts_agree(self):
        for case in LOAD(INPUT/'cases.json')['cases']:
            reference=LOAD(ROOT/case['original_native_cad']['path']);request=LOAD(INPUT/case['id']/'dimension-requirements.json')
            self.assertEqual(compile_input(request,reference),compile_observer(request,reference))
            self.assertEqual(request['measurement_policy'],POLICY)
            for bad in [dict(request,cad_sha256='0'*64),dict(request,units='inch'),dict(request,measurement_policy={})]:
                for compiler in (compile_input,compile_observer):
                    with self.assertRaises(ValueError):compiler(bad,reference)


if __name__=='__main__':unittest.main()
