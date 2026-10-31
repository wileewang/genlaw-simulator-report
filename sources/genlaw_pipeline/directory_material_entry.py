"""Serial bounded workers for paged complete native material auditing.

assemble() starts from actual native results. assemble_audits() reuses bound
original chunk audits when only the catalog/consumer representation changes.
Neither entry executes NC, modifies geometry, or accepts partial coverage.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding,MAX_WINDOWS
from genlaw_pipeline.native_material_entry import chunk_groups
from genlaw_pipeline.bucket_directory import MAX_SOURCES
from genlaw_pipeline.run import write

WORKER=ROOT/'genlaw_pipeline/directory_material.py'
ORIGINAL=ROOT/'genlaw_pipeline/native_material.py'
LOAD=lambda p:json.loads(p.read_text())


def _indexed(plan,audit_index,out):
    pages=[]
    for index in range((len(LOAD(plan)['chunks'])+MAX_SOURCES-1)//MAX_SOURCES):
        folder=out/f'page-{index:04d}'
        admitted_bounded(out,folder.name,[sys.executable,WORKER,'page','--plan',plan,
            '--audit-index',audit_index,'--index',str(index),'--output',folder])
        pages.append(folder/'page.json')
    page_index=out/'page-index.json';catalog=out/'catalog.json'
    write(page_index,{'pages':[file_binding(p) for p in pages]})
    admitted_bounded(out,'catalog',[sys.executable,WORKER,'catalog','--plan',plan,
        '--page-index',page_index,'--output',catalog])
    data=LOAD(catalog);results=[]
    for kind,counts in sorted(data['bucket_sources'].items()):
        for index,count in enumerate(counts):
            parts=[];batches=(count+MAX_WINDOWS-1)//MAX_WINDOWS
            if batches>1:
                for part in range(batches):
                    label=f'{kind}-{index:04d}-part-{part:03d}';path=out/(label+'.json')
                    admitted_bounded(out,label,[sys.executable,WORKER,'bucket-part','--catalog',catalog,
                        '--kind',kind,'--index',str(index),'--part',str(part),'--output',path]);parts.append(path)
            label=f'{kind}-{index:04d}';path=out/(label+'.json')
            command=[sys.executable,WORKER,'bucket','--catalog',catalog,
                '--kind',kind,'--index',str(index),'--output',path]
            if parts:command+=['--parts',*parts]
            admitted_bounded(out,label,command);results.append(path)
    index=out/'bucket-index.json';write(index,{'results':[file_binding(p) for p in results]})
    admitted_bounded(out,'finish',[sys.executable,WORKER,'finish','--catalog',catalog,
        '--result-index',index,'--output',out/'surface.json'])
    print(json.dumps({'directory_material_complete':str(out/'surface.json')}),flush=True)
    return out/'surface.json'


def assemble_audits(plan,audit_index,out):
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    return _indexed(plan.resolve(),audit_index.resolve(),out)


def assemble(project,build,results,out,partition=None):
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    plan=out/'plan.json'
    command=[sys.executable,ORIGINAL,'prepare','--project',project,'--build',build,
        '--results',*results,'--output',plan]
    if partition is not None:command+=['--partition',partition]
    admitted_bounded(out,'prepare',command)
    audits=[]
    for number,indices in enumerate(chunk_groups(LOAD(plan)['chunks'])):
        if len(indices)==1:
            i=indices[0]
            command=[sys.executable,ORIGINAL,'chunk','--plan',plan,'--index',str(i),'--output',out/f'chunk-{i:05d}']
        else:
            command=[sys.executable,ORIGINAL,'chunks','--plan',plan,'--indices',*map(str,indices),'--output',out]
        admitted_bounded(out,f'chunks-{number:04d}',command)
        audits.extend(out/f'chunk-{i:05d}/audit.json' for i in indices)
    audit_index=out/'audit-index.json';write(audit_index,{'audits':[file_binding(p) for p in audits]})
    return _indexed(plan,audit_index,out)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('assemble')
    for name in ['project','build','output']:a.add_argument('--'+name,type=Path,required=True)
    a.add_argument('--results',type=Path,nargs='+',required=True);a.add_argument('--partition',type=Path)
    a=sub.add_parser('reuse-audits')
    for name in ['plan','audit-index','output']:a.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    if args.action=='assemble':assemble(args.project,args.build,args.results,args.output,args.partition)
    else:assemble_audits(args.plan,args.audit_index,args.output)
