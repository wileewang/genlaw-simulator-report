"""Build a compact full actual-surface display in bounded groups of eight shards."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.run import bounded


def batch(surface_path,out,start,end):
    from genlaw_pipeline.sharded_surface import require_bounded_worker
    from genlaw_pipeline.sharded_measure import checked_surface
    from genlaw_pipeline.cell_windows import checked_file
    from genlaw_pipeline.planar_display import compact_window
    require_bounded_worker();surface=checked_surface(surface_path)
    if not 0<=start<end<=len(surface['windows']) or end-start>8:raise ValueError('Display batch exceeds eight-window limit')
    plan=checked_file(surface['plan'])
    for i in range(start,end):
        row=surface['windows'][i]
        compact_window(plan,checked_file(row['result']),checked_file(row['audit']),out/f'window-{i:03d}.json')


def build(surface,out):
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    parts=out/'parts';parts.mkdir();count=len(json.loads(surface.read_text())['windows'])
    for i in range(0,count,8):
        bounded(out,f'compact-{i:03d}',[sys.executable,Path(__file__),'--surface',surface,'--output',parts,
                                      '--batch-start',str(i),'--batch-end',str(min(i+8,count))])
        print(json.dumps({'display_windows_completed':min(i+8,count),'total':count}),flush=True)
    bounded(out,'combine',[sys.executable,ROOT/'genlaw_pipeline/planar_display.py','combine',
        '--surface',surface,'--directory',parts,'--output',out/'actual.mesh','--report',out/'display.json'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--surface',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--batch-start',type=int);p.add_argument('--batch-end',type=int);a=p.parse_args()
    if a.batch_start is None and a.batch_end is None:build(a.surface.resolve(),a.output)
    elif a.batch_start is not None and a.batch_end is not None:batch(a.surface.resolve(),a.output.resolve(),a.batch_start,a.batch_end)
    else:p.error('Supply both batch bounds')
