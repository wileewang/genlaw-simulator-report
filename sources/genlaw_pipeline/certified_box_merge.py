"""Coalesce adjacent equal-section boxes into proposals for NEW native proofs.

Only already certified homogeneous states are considered. A proposed union is
an exact rectangular union, never a bounding hull over gaps. The caller must
reinspect every merged box; this module cannot issue a material certificate.
"""
from collections import defaultdict

HOMOGENEOUS={'uncut_solid','empty_outside_stock'}


def proposals(leaves):
    boxes=[]
    for index,leaf in enumerate(leaves):
        if leaf['state'] not in HOMOGENEOUS: continue
        b=leaf['bounds']
        if (len(b)!=6 or any(type(x) is not int for x in b)
                or any(b[k]>=b[k+3] for k in range(3))):
            raise ValueError('Invalid certified integer box')
        boxes.append({'bounds':list(b),'state':leaf['state'],'members':[index]})
    changed=True
    while changed:
        changed=False
        for axis in range(3):
            groups=defaultdict(list)
            for item in boxes:
                b=item['bounds']; key=(item['state'],*(b[k] for k in range(6) if k not in [axis,axis+3]))
                groups[key].append(item)
            merged=[]
            for group in groups.values():
                group.sort(key=lambda item:item['bounds'][axis]); current=None
                for item in group:
                    if current is not None and item['bounds'][axis]<current['bounds'][axis+3]:
                        raise ValueError('Overlapping homogeneous certificates')
                    if current is not None and item['bounds'][axis]==current['bounds'][axis+3]:
                        current={'bounds':list(current['bounds']),'state':current['state'],
                            'members':current['members']+item['members']}
                        current['bounds'][axis+3]=item['bounds'][axis+3]; changed=True
                    else:
                        if current is not None: merged.append(current)
                        current=item
                if current is not None: merged.append(current)
            boxes=merged
    result=[b for b in boxes if len(b['members'])>1]
    return sorted(result,key=lambda b:(b['state'],b['bounds']))
