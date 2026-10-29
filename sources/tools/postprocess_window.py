"""Audit one original surface shard and build its exact section acceleration cache."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from genlaw_pipeline.sharded_surface import audit_window
from genlaw_pipeline.sharded_measure import cache_window

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',required=True,type=Path);p.add_argument('--directory',required=True,type=Path)
    a=p.parse_args()
    audit_window(a.plan,a.directory/'result.json',a.directory/'audit.json')
    cache_window(a.plan,a.directory/'result.json',a.directory/'sections.json')
