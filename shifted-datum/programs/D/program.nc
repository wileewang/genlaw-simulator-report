(Exported by FreeCAD)
(Post Processor: Path.Post.scripts.linuxcnc_post)
(Output Time:2026-10-31 19:01:08.435809)
(begin preamble)
G17 G54 G40 G49 G80 G90
G21
M5
G0 Z5.5
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
G0 Z5.50000 
G0 X5.00000 Y1.70000 
G0 Z4.50000 
G1 X5.00000 Y1.70000 Z1.70000 F60.00000 
G1 X-1.00000 Y1.70000 Z1.70000 F120.00000 
G0 Z5.50000 
G0 Z5.50000 
G0 X-1.00000 Y6.30000 
G0 Z4.50000 
G1 X-1.00000 Y6.30000 Z1.70000 F60.00000 
G1 X5.00000 Y6.30000 Z1.70000 F120.00000 
G0 Z5.50000 
G0 Z5.50000 
(finish operation: SelectedChamferEdges)
(begin postamble)
M05
G17 G54 G90 G80 G40
M2
