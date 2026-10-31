"""Finite batches of the unchanged exact-planar material audit workers.

Control receipts identify observed commands and output bytes; they do not
pretend to be native producer receipts. Failed/partial workers are retained.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file,file_binding,MAX_WINDOWS
from genlaw_pipeline.native_collection_entry import pending
from genlaw_pipeline.native_material_entry import chunk_groups
from genlaw_pipeline.bucket_directory import MAX_SOURCES
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.run import write

ORIGINAL=ROOT/'genlaw_pipeline/native_material.py'
MATERIAL=ROOT/'genlaw_pipeline/planar_material.py'
DIRECTORY=ROOT/'genlaw_pipeline/planar_directory.py'
LOAD=lambda p:json.loads(p.read_text())
PRODUCERS=[ORIGINAL,MATERIAL,DIRECTORY,ROOT/'genlaw_pipeline/native_material_entry.py',
    ROOT/'genlaw_pipeline/native_collection_entry.py',ROOT/'genlaw_pipeline/planar_directory_entry.py',
    ROOT/'genlaw_pipeline/bucket_directory.py',ROOT/'genlaw_pipeline/native_planar.py']


class BatchLimitReached(Exception):pass


def index_file(path,value):
    if path.exists():
        if LOAD(path)!=value:raise ValueError('Existing material index differs; do not overwrite it')
    else:
        if len(json.dumps(value).encode())>64*1024**2:raise ValueError('Index exceeds the unchanged file limit')
        write(path,value)


class Steps:
    def __init__(self,material,control,request,max_new):
        self.material=material;self.control=control;self.request=request;self.max_new=max_new
        self.started=[];self.reused=[];self.receipts=[]
        (control/'jobs').mkdir(exist_ok=True)

    def step(self,label,command,artifacts,owned=()):
        fresh=pending(self.material,label,command,artifacts[0])
        if fresh:
            if any(p.exists() for p in [*artifacts,*owned]):
                raise ValueError('Partial material worker outputs require explicit investigation')
            if len(self.started)>=self.max_new:raise BatchLimitReached()
            admitted_bounded(self.material,label,command);self.started.append(label)
        else:self.reused.append(label)
        if any(not p.is_file() for p in artifacts):raise ValueError('Completed worker lacks an output file')
        if any(p.stat().st_size>64*1024**2 for p in artifacts):
            raise ValueError('Completed output exceeds the unchanged file limit')
        value={'schema':'genlaw.material-audit-step-observation.v1','request':self.request,
            'label':label,'command':list(map(str,command)),
            'resources':file_binding(self.material/(label+'-resources.json')),
            'artifacts':[file_binding(p) for p in artifacts],
            'source_code':file_binding(Path(__file__))}
        path=self.control/'jobs'/(label+'.json');index_file(path,value);self.receipts.append(file_binding(path))


def initial_command(request):
    out=ROOT/request['material_directory']
    cmd=[sys.executable,ORIGINAL,'prepare','--project',checked_file(request['project']),
        '--build',checked_file(request['build']),'--results',
        *[checked_file(r) for r in request['native_results']],'--output',out/'original-plan.json']
    if request['partition'] is not None:cmd+=['--partition',checked_file(request['partition'])]
    return cmd


def initialize(inputs,material,control):
    material=material.resolve();control=control.resolve();material.relative_to(ROOT);control.relative_to(ROOT)
    if control==material or material in control.parents or control in material.parents:
        raise ValueError('Material and control directories must be distinct siblings or separate paths')
    names=['project','build','native_results','candidates','partition']
    if any(k not in inputs for k in names):raise ValueError('Missing original material inputs')
    if (not isinstance(inputs['native_results'],list) or not 0<len(inputs['native_results'])<=MAX_WINDOWS
            or not isinstance(inputs['candidates'],list) or not 0<len(inputs['candidates'])<=MAX_WINDOWS*64):
        raise ValueError('Original material input counts exceed the existing bounds')
    for binding in [inputs['project'],inputs['build'],*inputs['native_results'],*inputs['candidates']]:
        checked_file(binding)
    if inputs['partition'] is not None:checked_file(inputs['partition'])
    value={'schema':'genlaw.material-audit-batch-request.v1',**{k:inputs[k] for k in names},
        'material_directory':str(material.relative_to(ROOT)),
        'producers':[file_binding(p) for p in PRODUCERS],'source_code':file_binding(Path(__file__)),
        'new_native_executions':0,'numerical_producers_modified':False,
        'whole_process_card_complete':False,'production_approved':False}
    control.mkdir(parents=True,exist_ok=False)
    write(control/'request.json',value)
    return control/'request.json'


def checked_request(path):
    value=LOAD(path)
    if (value.get('schema')!='genlaw.material-audit-batch-request.v1'
            or value.get('source_code')!=file_binding(Path(__file__))
            or value.get('producers')!=[file_binding(p) for p in PRODUCERS]
            or value.get('new_native_executions')!=0
            or any(value.get(k) is not False for k in
                ['numerical_producers_modified','whole_process_card_complete','production_approved'])):
        raise ValueError('Changed material audit request, producers or declared scope')
    for binding in [value['project'],value['build'],*value['native_results'],*value['candidates']]:checked_file(binding)
    if value['partition'] is not None:checked_file(value['partition'])
    out=(ROOT/value['material_directory']).resolve();out.relative_to(ROOT)
    return value,out


def perform(request,steps):
    out=steps.material;original=out/'original-plan.json';plan=out/'plan.json'
    steps.step('original-plan',initial_command(request),[original])
    candidates=out/'candidate-index.json';index_file(candidates,{'candidates':request['candidates']})
    steps.step('plan',[sys.executable,MATERIAL,'prepare','--native-plan',original,
        '--candidates',candidates,'--output',plan],[plan])
    audits=[];entries=[{**row,'triangles':row['original_triangles']} for row in LOAD(plan)['chunks']]
    for number,indices in enumerate(chunk_groups(entries)):
        folders=[out/f'chunk-{i:05d}' for i in indices]
        if len(indices)==1:
            command=[sys.executable,MATERIAL,'chunk','--plan',plan,'--index',str(indices[0]),'--output',folders[0]]
        else:command=[sys.executable,MATERIAL,'chunks','--plan',plan,'--indices',*map(str,indices),'--output',out]
        outputs=[folder/name for folder in folders for name in
            ['audit.json','cache.json','facets.bin','edges.bin','sections.npy','heights.npy']]
        steps.step(f'chunks-{number:04d}',command,outputs,folders)
        audits.extend(folder/'audit.json' for folder in folders)
    audit_index=out/'audit-index.json';index_file(audit_index,{'audits':[file_binding(p) for p in audits]})
    pages=[]
    for i in range((len(entries)+MAX_SOURCES-1)//MAX_SOURCES):
        folder=out/f'page-{i:04d}'
        outputs=[folder/name for name in ['page.json','facets.json','edges.json','facets.bin','edges.bin']]
        steps.step(folder.name,[sys.executable,DIRECTORY,'page','--plan',plan,
            '--audit-index',audit_index,'--index',str(i),'--output',folder],outputs,[folder])
        pages.append(folder/'page.json')
    page_index=out/'page-index.json';index_file(page_index,{'pages':[file_binding(p) for p in pages]})
    catalog=out/'catalog.json'
    steps.step('catalog',[sys.executable,DIRECTORY,'catalog','--plan',plan,'--page-index',page_index,
        '--output',catalog],[catalog]);results=[]
    for kind,counts in sorted(LOAD(catalog)['bucket_sources'].items()):
        for i,count in enumerate(counts):
            parts=[];batches=(count+MAX_WINDOWS-1)//MAX_WINDOWS
            if batches>1:
                for part in range(batches):
                    label=f'{kind}-{i:04d}-part-{part:03d}';path=out/(label+'.json')
                    steps.step(label,[sys.executable,DIRECTORY,'bucket-part','--catalog',catalog,
                        '--kind',kind,'--index',str(i),'--part',str(part),'--output',path],
                        [path,path.with_suffix('.bin')]);parts.append(path)
            label=f'{kind}-{i:04d}';path=out/(label+'.json')
            command=[sys.executable,DIRECTORY,'bucket','--catalog',catalog,'--kind',kind,
                '--index',str(i),'--output',path]
            if parts:command+=['--parts',*parts]
            steps.step(label,command,[path]);results.append(path)
    bucket_index=out/'bucket-index.json';index_file(bucket_index,{'results':[file_binding(p) for p in results]})
    steps.step('finish',[sys.executable,DIRECTORY,'finish','--catalog',catalog,
        '--result-index',bucket_index,'--output',out/'surface.json'],[out/'surface.json'])


def run(request_path,max_new):
    request_path=request_path.resolve();request_path.relative_to(ROOT)
    if type(max_new) is not int or not 0<=max_new<=25:raise ValueError('Each driver may start 0–25 bounded workers')
    request,material=checked_request(request_path);control=request_path.parent
    lock_path=ROOT/'.runtime'/('material-audit-'+hashlib.sha256(str(material).encode()).hexdigest()+'.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        runs=control/'runs';runs.mkdir(exist_ok=True);previous=sorted(runs.glob('run-*/owner.json'))
        if previous:
            identity=LOAD(previous[-1])['process']
            if process_identity(identity['pid'])==identity:raise ValueError('Prior audit driver is still live')
        record=runs/f'run-{len(previous)+1:04d}';record.mkdir()
        write(record/'owner.json',{'process':process_identity(os.getpid()),'request':file_binding(request_path),
            'source_code':file_binding(Path(__file__)),'observed_live':True})
        material.mkdir(parents=True,exist_ok=True);steps=Steps(material,control,file_binding(request_path),max_new)
        try:
            try:perform(request,steps);status='all_material_workers_complete'
            except BatchLimitReached:status='batch_finished_material_incomplete'
            write(record/'result.json',{'status':status,'request':file_binding(request_path),
                'newly_started_jobs':steps.started,'reused_jobs':steps.reused,'completed_job_observations':steps.receipts,
                'batch_limit':max_new,'new_native_executions':0,'started_failures_repeated':0,
                'surface':file_binding(material/'surface.json') if status=='all_material_workers_complete' else None,
                'whole_process_card_complete':False,'production_approved':False,'source_code':file_binding(Path(__file__))})
            print(json.dumps({'status':status,'started':len(steps.started),'reused':len(steps.reused)}),flush=True)
        except Exception as error:
            write(record/'stopped.json',{'status':'stopped_without_repeating_started_work','error':str(error),
                'error_type':type(error).__name__,'newly_started_jobs':steps.started,
                'request':file_binding(request_path),'existing_outputs_retained':True})
            raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['initialize','run'])
    for name in ['inputs','material','control','request']:p.add_argument('--'+name,type=Path)
    p.add_argument('--max-new',type=int,default=25);a=p.parse_args()
    needed=['inputs','material','control'] if a.action=='initialize' else ['request']
    if any(getattr(a,name) is None for name in needed):p.error('Required options: '+', '.join('--'+n for n in needed))
    if a.action=='initialize':print(initialize(LOAD(a.inputs),a.material,a.control))
    else:run(a.request,a.max_new)
