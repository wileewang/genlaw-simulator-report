"""Bounded exact indexes for existing audited facet/edge record ranges.

This is a storage/indexing component, not a topology or manufacturing verdict.
Each page indexes at most 64 source files. Original bucket bytes, hashes and
source identities are retained; queries reproduce the existing range protocol.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file,file_binding,MAX_WINDOWS
from genlaw_pipeline.surface_records import DTYPES,LIMITS
from genlaw_pipeline.run import write

ROW=np.dtype([('bucket','<u4'),('offset','<u4'),('records','<u4'),('sha256','V32')])
MAX_SOURCES=64
MAX_BUCKETS=4*MAX_WINDOWS
MAX_ROWS=MAX_SOURCES*MAX_BUCKETS
LOAD=lambda p:json.loads(p.read_text())


def check_layout(kind,count):
    if kind not in DTYPES or type(count) is not int or count<1 or count>MAX_BUCKETS or count&(count-1):
        raise ValueError('Invalid original record kind or bucket count')


def encode(part,kind,count):
    check_layout(kind,count)
    n=part.get('records');rows=part.get('buckets')
    if (type(n) is not int or not 0<=n<=LIMITS[kind]
            or not isinstance(rows,list) or len(rows)>count):
        raise ValueError('Invalid original source record count')
    data=np.empty(len(rows),dtype=ROW);offset=0;previous=-1
    for i,row in enumerate(rows):
        bucket=row.get('index');size=row.get('records');digest=row.get('sha256')
        if (type(bucket) is not int or not previous<bucket<count
                or type(size) is not int or size<=0 or size>LIMITS[kind]
                or type(row.get('offset')) is not int or row['offset']!=offset
                or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)):
            raise ValueError('Missing, reordered or invalid original source ranges')
        data[i]=(bucket,offset,size,bytes.fromhex(digest))
        offset+=size*DTYPES[kind].itemsize;previous=bucket
    if offset!=n*DTYPES[kind].itemsize:raise ValueError('Source ranges do not cover all original records')
    return data


def write_page(sources,kind,count,output):
    require_bounded_worker();check_layout(kind,count)
    if not 1<=len(sources)<=MAX_SOURCES:raise ValueError('Index page exceeds 64 original sources')
    ids=[s.get('source_id') for s in sources]
    if any(type(i) is not int or not 0<=i<64*MAX_WINDOWS for i in ids) or ids!=sorted(set(ids)):
        raise ValueError('Missing, duplicate or reordered source identity')
    output=output.resolve();output.relative_to(ROOT)
    payload=output.with_suffix('.bin');table=[];total=0;records=0;digest=hashlib.sha256()
    with payload.open('xb') as stream:
        for item in sources:
            if set(item)!={'source_id','audit'}:raise ValueError('Unexpected source identity fields')
            audit=LOAD(checked_file(item['audit']));part=audit['records'][kind]
            original=checked_file(part['file']);data=encode(part,kind,count)
            if original.stat().st_size!=part['records']*DTYPES[kind].itemsize:
                raise ValueError('Original record file has missing or added bytes')
            value=data.tobytes();stream.write(value);digest.update(value)
            table.append({**item,'file':part['file'],'records':part['records'],
                'interval':[total,total+len(data)]})
            total+=len(data);records+=part['records']
    if total>MAX_ROWS:raise ValueError('Index page exceeds bounded metadata rows')
    write(output,{'schema':'genlaw.bucket-directory-page.v1','kind':kind,'bucket_count':count,
        'sources':table,'rows':total,'record_count':records,'row_bytes':ROW.itemsize,
        'data':{'path':str(payload.relative_to(ROOT)),'sha256':digest.hexdigest()},
        'source_code':file_binding(Path(__file__)),'geometry_acceptance':False,
        'record_bytes_modified':False,'maximum_sources':MAX_SOURCES,'maximum_rows':MAX_ROWS})


class BucketPage:
    """Validate a bounded page once, then retrieve exact original source ranges."""
    def __init__(self,path):
        require_bounded_worker();self.path=path;self.value=LOAD(path);v=self.value
        if (v.get('schema')!='genlaw.bucket-directory-page.v1'
                or v.get('source_code')!=file_binding(Path(__file__))
                or v.get('geometry_acceptance') is not False or v.get('record_bytes_modified') is not False
                or v.get('maximum_sources')!=MAX_SOURCES or v.get('maximum_rows')!=MAX_ROWS
                or v.get('row_bytes')!=ROW.itemsize):
            raise ValueError('Unsupported or overstated bucket directory page')
        kind=v['kind'];count=v['bucket_count'];check_layout(kind,count)
        sources=v['sources'];n=v['rows']
        if not 1<=len(sources)<=MAX_SOURCES or type(n) is not int or not 0<=n<=MAX_ROWS:
            raise ValueError('Index page exceeds unchanged source or metadata bounds')
        data_path=checked_file(v['data'])
        if data_path.stat().st_size!=n*ROW.itemsize:raise ValueError('Index data has missing or added rows')
        self.rows=np.memmap(data_path,dtype=ROW,mode='r',shape=(n,)) if n else np.empty(0,dtype=ROW)
        previous=-1;offset=0;records=0;self.blocks=[]
        for source in sources:
            index=source.get('source_id')
            if type(index) is not int or not previous<index<64*MAX_WINDOWS:
                raise ValueError('Duplicate or reordered original source identity')
            audit=LOAD(checked_file(source['audit']));part=audit['records'][kind]
            expected=encode(part,kind,count);end=offset+len(expected)
            if (source.get('file')!=part['file'] or source.get('records')!=part['records']
                    or source.get('interval')!=[offset,end]
                    or not np.array_equal(self.rows[offset:end],expected)):
                raise ValueError('Compact index differs from original audited range metadata')
            self.blocks.append((source,self.rows[offset:end]));offset=end
            records+=part['records'];previous=index
        if offset!=n or records!=v['record_count']:raise ValueError('Index omits or adds original source records')

    def sources(self,index):
        if type(index) is not int or not 0<=index<self.value['bucket_count']:
            raise ValueError('Invalid bucket query')
        result=[]
        for source,rows in self.blocks:
            p=int(np.searchsorted(rows['bucket'],index))
            if p==len(rows) or int(rows['bucket'][p])!=index:continue
            row=rows[p]
            result.append({'index':index,'offset':int(row['offset']),'records':int(row['records']),
                'sha256':row['sha256'].tobytes().hex(),'file':source['file'],
                'chunk_index':source['source_id'],'audit':source['audit']})
        return result


def source_batches(pages,kind,count,index):
    """Yield at most 1024 contributors; page count never becomes execution count."""
    require_bounded_worker();check_layout(kind,count)
    if type(index) is not int or not 0<=index<count:raise ValueError('Invalid bucket query')
    previous=-1;batch=[];records=0;page_count=0
    for path in pages:
        page=BucketPage(path);page_count+=1
        if page_count>1024 or page.value['kind']!=kind or page.value['bucket_count']!=count:
            raise ValueError('Foreign or excessive bucket directory pages')
        ids=[s['source_id'] for s in page.value['sources']]
        if ids[0]<=previous:raise ValueError('Duplicate or overlapping source page membership')
        previous=ids[-1]
        for source in page.sources(index):
            records+=source['records']
            if records>LIMITS[kind]:raise ValueError('Global bucket exceeds original record limit')
            batch.append(source)
            if len(batch)==MAX_WINDOWS:yield batch;batch=[]
    if not page_count:raise ValueError('Missing bucket directory pages')
    if batch:yield batch
