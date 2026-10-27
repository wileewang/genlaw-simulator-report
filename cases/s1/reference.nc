(Exported by FreeCAD)
(Post Processor: Path.Post.scripts.linuxcnc_post)
(Output Time:2026-10-27 17:31:47.700224)
(begin preamble)
G17 G54 G40 G49 G80 G90
G21
(begin operation: TC: D2 Drill 118deg)
(machine units: mm/min)
(TC: D2 Drill 118deg) 
M5
M6 T1 
M3 S1000 
(finish operation: TC: D2 Drill 118deg)
(begin operation: Drilling)
(machine units: mm/min)
(Drilling) 
(Begin Drilling) 
G90 
G0 Z3.00000 
G98 
G0 X3.00000 Y2.00000 
G0 Z2.00000 
G81 X3.00000 Y2.00000 Z-2.20086 F60.00000 R1.00000 
G80 
G0 Z3.00000 
(finish operation: Drilling)
(begin postamble)
M05
G17 G54 G90 G80 G40
M2
