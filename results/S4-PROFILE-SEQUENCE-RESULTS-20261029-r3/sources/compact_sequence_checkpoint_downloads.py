"""Repackage unpublished intermediate STL downloads losslessly within site size.

The initial package and native runs remain immutable. Only a new package's
intermediate download encoding changes from gzip to xz; displayed geometry,
native NC, acceptance and numerical results are copied byte-for-byte.
"""
import argparse,gzip,hashlib,json,lzma,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.preparation import digest


def main():
    require_bounded_worker()
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--final-downloads',action='store_true');a=p.parse_args()
    source,out=a.source.resolve(),a.output.resolve()
    if not source.is_relative_to(ROOT) or not out.is_relative_to(ROOT):raise ValueError('Keep packages in project')
    load=lambda p:json.loads(p.read_text())
    original=load(source/'manifest.json')
    for name,entry in original['files'].items():
        if digest(source/name)!=entry['sha256']:raise ValueError('Source package changed: '+name)
    shutil.copytree(source,out)
    def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    summary=load(out/'summary.json');records=[]
    for row in ([] if a.final_downloads else summary['normal_cases']):
        case=out/row['id'];replay=load(case/'replay/frames.json')
        for frame in replay['frames']:
            path=(case/'replay'/(frame['stl']+'.gz')).resolve()
            if not path.is_relative_to(out) or '/checkpoints/' not in str(path):raise ValueError('Only original cumulative checkpoint downloads may change')
            raw=gzip.decompress(path.read_bytes())
            if hashlib.sha256(raw).hexdigest()!=frame['stl_sha256']:raise ValueError('Intermediate raw STL binding differs')
            target=path.with_suffix('.xz');encoded=lzma.compress(raw,format=lzma.FORMAT_XZ,preset=3)
            if lzma.decompress(encoded)!=raw:raise ValueError('Lossless encoding mismatch')
            target.write_bytes(encoded)
            records.append({'source_path':str(path.relative_to(out)),'source_gzip_sha256':digest(path),'source_bytes':path.stat().st_size,
                'path':str(target.relative_to(out)),'encoding':'xz','bytes':len(encoded),'sha256':digest(target),
                'decoded_bytes':len(raw),'decoded_sha256':frame['stl_sha256']})
            path.unlink();frame['stl_download_encoding']='xz'
        for frame in row['frames']:frame['stl_download_encoding']='xz'
        replay['archive_path_reuse']='Lossless xz downloads at original checkpoint paths; original native STL bytes unchanged.'
        write(case/'replay/frames.json',replay)
    if a.final_downloads:
        shutil.copy2(out/'download-encoding.json',out/'checkpoint-download-encoding.json')
        shutil.copy2(out/'sources'/Path(__file__).name,out/'sources/compact_sequence_checkpoint_downloads-before-final.py')
        for row in summary['normal_cases']+summary['controls']:
            case=out/row['id'];path=case/'simulated.stl.gz';target=case/'simulated.stl.xz'
            raw=gzip.decompress(path.read_bytes());expected=load(case/'verification.json')['mesh_sha256']
            if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Final native STL binding differs')
            encoded=lzma.compress(raw,format=lzma.FORMAT_XZ,preset=3)
            if lzma.decompress(encoded)!=raw:raise ValueError('Final lossless encoding mismatch')
            target.write_bytes(encoded)
            records.append({'source_path':str(path.relative_to(out)),'source_gzip_sha256':digest(path),'source_bytes':path.stat().st_size,
                'path':str(target.relative_to(out)),'encoding':'xz','bytes':len(encoded),'sha256':digest(target),
                'decoded_bytes':len(raw),'decoded_sha256':expected})
            path.unlink();row['final_download_encoding']='xz'
        summary['final_download_encoding']='xz'
        summary['checkpoint_download_encoding_report']='checkpoint-download-encoding.json'
    summary['id']='S4-PROFILE-SEQUENCE-RESULTS-20261029-r3' if a.final_downloads else 'S4-PROFILE-SEQUENCE-RESULTS-20261029-r2';summary['checkpoint_download_encoding']='xz'
    report={'schema':'genlaw.checkpoint-download-encoding.v1','status':'verified_lossless',
        'source_archive_manifest_sha256':digest(source/'manifest.json'),'source_archive_retained':True,
        'native_quantitative_mesh_modified':False,'display_meshes_modified':False,'files':records,
        'bytes_saved':sum(r['source_bytes']-r['bytes'] for r in records),'source_code_sha256':digest(Path(__file__))}
    write(out/'download-encoding.json',report);shutil.copy2(Path(__file__),out/'sources'/Path(__file__).name)
    summary['download_encoding_report']='download-encoding.json';write(out/'summary.json',summary)
    write(out/'manifest.json',{'schema':'genlaw.stage-result-archive.v1','id':summary['id'],
        'files':{str(f.relative_to(out)):{'bytes':f.stat().st_size,'sha256':digest(f)} for f in sorted(out.rglob('*')) if f.is_file() and f.name!='manifest.json'}})
    # Nested input manifests remain records too, unlike this top-level manifest.
    manifest=load(out/'manifest.json')
    for f in out.glob('*/manifest.json'):manifest['files'][str(f.relative_to(out))]={'bytes':f.stat().st_size,'sha256':digest(f)}
    write(out/'manifest.json',manifest)
    print(json.dumps({'status':report['status'],'files':len(records),'bytes_saved':report['bytes_saved']}))


if __name__=='__main__':main()
