"""Review only permutations of identical retract-separated cuts within a layer.

This proves material-removal equivalence in the declared geometric subset.
It does not claim identical NC, cycle time, cutting forces or machine behavior.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc,nc_words
from genlaw_pipeline.run import write


def feed_groups(text,plan):
    checked=check_nc(text,plan)  # Includes all rapid, mode, tool, feed and coverage guards.
    blocks=nc_words(text);groups=[];current=[]
    def close():
        if not current:return
        depth=current[0]['end'][2]
        if current[0]['start'][2]<plan['parameters']['safe_height_mm']:
            raise ValueError('Feed group does not start at the safe plane')
        if any(c['end'][2]!=depth for c in current):raise ValueError('Multiple depths within a feed group')
        groups.append((depth,json.dumps(current,sort_keys=True)));current.clear()
    for move in checked['motion_trace']:
        if move['motion']=='G0':close();continue
        words=dict(blocks[move['block']-1])
        current.append({k:move[k] for k in ['motion','start','end','feed_mm_min']} |
                       {'arc_center_offsets':{k:words[k] for k in ['I','J'] if k in words}})
    close()
    layers=[]
    for depth,signature in groups:
        if not layers or layers[-1][0]!=depth:layers.append((depth,Counter()))
        layers[-1][1][signature]+=1
    return layers,checked


def compare(old,new,plan):
    a,ca=feed_groups(old,plan);b,cb=feed_groups(new,plan)
    if a!=b:raise ValueError('Feed segments, arc centers, counts or layer order differ')
    if abs(ca['feed_time_seconds']-cb['feed_time_seconds'])>1e-9:raise ValueError('Feed duration differs')
    return {'layers':[z for z,_ in a],'feed_groups_per_layer':[sum(c.values()) for _,c in a],
            'same_feed_segments_in_each_layer':True,'rapid_moves_checked_above_safe_plane':True,
            'feed_time_seconds':ca['feed_time_seconds'],'cycle_time_equivalence_claimed':False}


def main():
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['old','new','output']:p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();read=lambda p:json.loads(p.read_text())
    old=args.old.resolve();new=args.new.resolve();out=args.output.resolve()
    if not out.is_relative_to(ROOT) or out.exists():raise ValueError('Need a new review file in project')
    plan=read(old/'execution-plan.json')
    assert plan==read(new/'execution-plan.json') and read(old/'simulation.camotics')==read(new/'simulation.camotics')
    assert read(old/'simulation-result.json')['engine_binary_sha256']==read(new/'simulation-result.json')['engine_binary_sha256']
    old_text=(old/'program.nc').read_text();new_text=(new/'program.nc').read_text()
    assert nc_words(old_text)!=nc_words(new_text)
    review=compare(old_text,new_text,plan)
    # Counterfactuals exercise the review itself; no new native simulation.
    rejected=[]
    for name,changed in [('feed',new_text.replace('F120.00000','F121.00000',1)),
                         ('depth',new_text.replace('Z-0.25000','Z-0.24000',1)),
                         ('arc-center',new_text.replace('I1.30628','I1.30728',1)),
                         ('rapid-height',new_text.replace('Z2.00000','Z-0.10000',1))]:
        assert changed!=new_text,name
        try:compare(old_text,changed,plan)
        except ValueError:rejected.append(name)
        else:raise AssertionError('Changed NC was not rejected: '+name)
    write(out,{'status':'passed','suite':'s3-region','old_nc_sha256':digest(old/'program.nc'),
        'new_nc_sha256':digest(new/'program.nc'),'execution_plan_sha256':digest(old/'execution-plan.json'),
        'simulation_configuration_unchanged':True,'same_complete_engine':True,
        'executable_nc_words_unchanged':False,'equivalence':review,'review_counterfactuals_rejected':rejected,
        'old_run':str(old.relative_to(ROOT)),'new_run':str(new.relative_to(ROOT)),
        'source_code_sha256':digest(Path(__file__)),
        'scope':'Only original-coordinate geometric removal by identical feed segments, reordered inside a cutting layer with safe retracts; no force or cycle-time claim.'})
    print(json.dumps({'status':'passed',**review,'counterfactuals_rejected':rejected}))


if __name__=='__main__':main()
