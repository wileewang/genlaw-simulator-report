"""Single-worker-at-a-time orchestration for complete native material auditing."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.run import write

WORKER=ROOT/'genlaw_pipeline/native_material.py'
LOAD=lambda p:json.loads(p.read_text())


def chunk_groups(entries):
    groups=[];current=[];total=0
    for entry in entries:
        if current and (total+entry['triangles']>333333 or len(current)==64):
            groups.append(current);current=[];total=0
        current.append(entry['index']);total+=entry['triangles']
    if current:groups.append(current)
    return groups


def assemble(project,build,results,out,partition=None):
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    plan=out/'plan.json'
    command=[sys.executable,WORKER,'prepare','--project',project,'--build',build,
        '--results',*results,'--output',plan]
    if partition is not None:command+=['--partition',partition]
    admitted_bounded(out,'prepare',command)
    audits=[]
    for number,indices in enumerate(chunk_groups(LOAD(plan)['chunks'])):
        if len(indices)==1:
            index=indices[0];folder=out/f'chunk-{index:05d}'
            cmd=[sys.executable,WORKER,'chunk','--plan',plan,'--index',str(index),'--output',folder]
        else:
            cmd=[sys.executable,WORKER,'chunks','--plan',plan,'--indices',*map(str,indices),'--output',out]
        admitted_bounded(out,f'chunks-{number:04d}',cmd)
        audits.extend(out/f'chunk-{i:05d}/audit.json' for i in indices)
    folder=out/'catalog';catalog=folder/'catalog.json'
    write(out/'audit-index.json',{'audits':[file_binding(p) for p in audits]})
    admitted_bounded(out,'catalog',[sys.executable,WORKER,'catalog','--plan',plan,
        '--audit-index',out/'audit-index.json','--output',folder])
    data=LOAD(catalog);results=[]
    for kind in sorted(data['requests']):
        for index,binding in enumerate(data['requests'][kind]):
            request=LOAD(checked_file(binding));parts=[]
            if len(request['source_batches'])>1:
                for part in range(len(request['source_batches'])):
                    label=f'{kind}-{index:04d}-part-{part:03d}';path=out/(label+'.json')
                    admitted_bounded(out,label,[sys.executable,WORKER,'bucket-part','--catalog',catalog,
                        '--kind',kind,'--index',str(index),'--part',str(part),'--output',path]);parts.append(path)
            label=f'{kind}-{index:04d}';path=out/(label+'.json')
            cmd=[sys.executable,WORKER,'bucket','--catalog',catalog,'--kind',kind,'--index',str(index),'--output',path]
            if parts:cmd+=['--parts',*parts]
            admitted_bounded(out,label,cmd);results.append(path)
    write(out/'bucket-index.json',{'results':[file_binding(p) for p in results]})
    admitted_bounded(out,'finish',[sys.executable,WORKER,'finish','--catalog',catalog,'--result-index',out/'bucket-index.json',
        '--output',out/'surface.json'])
    print(json.dumps({'native_material_complete':str(out/'surface.json')}),flush=True)
    return out/'surface.json'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['project','build','output']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--results',type=Path,nargs='+',required=True);p.add_argument('--partition',type=Path)
    a=p.parse_args();assemble(a.project.resolve(),a.build.resolve(),[p.resolve() for p in a.results],a.output,a.partition)
