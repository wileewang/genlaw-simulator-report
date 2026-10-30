import tempfile,struct,unittest
from pathlib import Path
from genlaw_pipeline.declared_dimensions import exact_surface_coverage
from mesh_metrics import np,STL_DTYPE,load_mesh

ROOT=Path(__file__).resolve().parents[1]

class SurfaceCoverage(unittest.TestCase):
    def mesh(self,extras=(),replacement=None):
        vertices=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],dtype='<f4')
        faces=np.array([[0,2,1],[0,1,3],[0,3,2],[1,2,3]],dtype='<u4')
        triangles=list(vertices[faces]);triangles.extend(extras(vertices,faces) if callable(extras) else extras)
        raw=np.zeros(len(triangles),dtype=STL_DTYPE);raw['points']=triangles
        tmp=tempfile.TemporaryDirectory(dir=ROOT/'.runtime/tmp');self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/'actual.stl';path.write_bytes(b'\0'*80+struct.pack('<I',len(raw))+raw.tobytes())
        v,f,a=load_mesh(path)
        if replacement:v,f=replacement(v,f)
        return exact_surface_coverage(path,v,f,a)

    def test_no_duplicate(self):self.assertTrue(self.mesh()['every_original_face_exactly_represented'])
    def test_identical_duplicate(self):self.assertEqual(self.mesh(lambda v,f:[v[f[0]]])['exact_same_winding_duplicates'],1)
    def test_cyclic_duplicate(self):self.assertEqual(self.mesh(lambda v,f:[v[f[0]][[1,2,0]]])['exact_same_winding_duplicates'],1)
    def test_reverse_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError,'orientation'):self.mesh(lambda v,f:[v[f[0]][[0,2,1]]])
    def test_missing_distinct_face_rejected(self):
        with self.assertRaisesRegex(ValueError,'orientation'):self.mesh(replacement=lambda v,f:(v,f[:-1]))
    def test_vertex_move_rejected(self):
        with self.assertRaisesRegex(ValueError,'vertices'):self.mesh(replacement=lambda v,f:(v+.01,f))

if __name__=='__main__':unittest.main()
