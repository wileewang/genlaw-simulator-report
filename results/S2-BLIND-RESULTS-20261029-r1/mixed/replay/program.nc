(Exported by FreeCAD)
(Post Processor: Path.Post.scripts.linuxcnc_post)
(Output Time:2026-10-29 00:04:16.041888)
(begin preamble)
G17 G54 G40 G49 G80 G90
G21
(begin operation: TC GenLaw Drill)
(machine units: mm/min)
(TC GenLaw Drill) 
M5
M6 T1 
M3 S1000 
(finish operation: TC GenLaw Drill)
(begin operation: Drilling)
(machine units: mm/min)
(Drilling) 
(Begin Drilling) 
G90 
G0 Z3.00000 
G98 
G0 X2.00000 Y2.00000 
G0 Z2.00000 
G81 X2.00000 Y2.00000 Z-2.20086 F60.00000 R1.00000 
G80 
G0 Z3.00000 
(finish operation: Drilling)
(begin operation: Drilling001)
(machine units: mm/min)
(Drilling001) 
(Begin Drilling) 
G90 
G0 Z3.00000 
G98 
G0 X5.50000 Y4.00000 
G0 Z2.00000 
G81 X5.50000 Y4.00000 Z-1.10086 F60.00000 R1.00000 
G80 
G0 Z3.00000 
(finish operation: Drilling001)
(begin postamble)
M05
G17 G54 G90 G80 G40
M2
