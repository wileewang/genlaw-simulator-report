(Exported by FreeCAD)
(Post Processor: Path.Post.scripts.linuxcnc_post)
(Output Time:2026-10-31 18:59:44.000137)
(begin preamble)
G17 G54 G40 G49 G80 G90
G21
M5
G0 Z3
G21
(begin operation: TC SelectedChamfer T1)
(machine units: mm/min)
(TC SelectedChamfer T1) 
M5
M6 T1 
M3 S3000 
(finish operation: TC SelectedChamfer T1)
(begin operation: SelectedChamferEdges)
(machine units: mm/min)
(SelectedChamferEdges) 
G0 Z3.00000 
G0 X6.00000 Y-0.30000 
G0 Z2.00000 
G1 X6.00000 Y-0.30000 Z-0.80000 F60.00000 
G1 X0.00000 Y-0.30000 Z-0.80000 F120.00000 
G0 Z3.00000 
G0 Z3.00000 
G0 X0.00000 Y4.30000 
G0 Z2.00000 
G1 X0.00000 Y4.30000 Z-0.80000 F60.00000 
G1 X6.00000 Y4.30000 Z-0.80000 F120.00000 
G0 Z3.00000 
G0 Z3.00000 
(finish operation: SelectedChamferEdges)
(begin postamble)
M05
G17 G54 G90 G80 G40
M2
