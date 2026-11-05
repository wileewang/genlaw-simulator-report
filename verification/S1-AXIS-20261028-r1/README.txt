GenLaw S1 axis investigation. Diagnostic software evidence only.
The old meshes, NC and manufacturing criteria are unchanged. No complete patched CAMotics engine was run.
The proposed pure-Z source patch is experimentally checked using the upstream depth() function and a type-only C++ harness.
upstream/ and proposed/ include CAMotics code licensed under the included upstream LICENSE and COPYING files.
Compile with g++ -std=c++11 -O2 -ffp-contract=off -I harness upstream/ConicSweep.cpp harness/main.cpp -o original
Compile the proposed/ConicSweep.cpp similarly. Feed the 12 numbers in each conic-regression.json case values row.
Each result line contains the cut classification and the diagnostic discriminant. The latter still shows the original expression.
