"""Small deployment probe, not the production process-card adapter.

Run with the deployed FreeCAD interpreter under run_check.py. The core phase
writes the independent target before CAM can generate any NC.
"""
import hashlib
import json
import math
import os
from pathlib import Path as FilePath
import time
import traceback

started = time.monotonic()
out = FilePath(os.environ["GENLAW_PROBE_OUTPUT"])
out.mkdir(parents=True, exist_ok=True)
phase = os.environ["GENLAW_PROBE_PHASE"]
record = {"phase": phase, "scope": "predevelopment_tool_probe",
          "case_id": "S1-offset-through-hole", "geometry_acceptance_pass": False}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounds(shape):
    b = shape.BoundBox
    return {"min": [b.XMin, b.YMin, b.ZMin], "max": [b.XMax, b.YMax, b.ZMax]}


try:
    import FreeCAD as App
    import Part

    record["freecad_version"] = list(App.Version())
    record["gui_up"] = bool(App.GuiUp)
    record["import_seconds"] = time.monotonic() - started
    doc = App.newDocument("S1_Predevelopment")
    model = doc.addObject("PartDesign::Feature", "Target")

    if phase == "core":
        box = Part.makeBox(8, 6, 1.5, App.Vector(0, 0, -1.5))
        hole = Part.makeCylinder(1, 1.5, App.Vector(3, 2, -1.5))
        model.Shape = box.cut(hole)
        assert model.Shape.isValid() and len(model.Shape.Solids) == 1
        assert abs(model.Shape.Volume - (72 - 1.5 * math.pi)) < 1e-8
        doc.recompute()
        model.Shape.exportBrep(str(out / "target.brep"))
        Part.export([model], str(out / "target.step"))
        doc.saveAs(str(out / "target.FCStd"))
        record.update(target_valid=True, target_volume_mm3=model.Shape.Volume,
                      target_bounds=bounds(model.Shape),
                      target_sha256=digest(out / "target.brep"),
                      target_created_without_nc_or_simulation=True)
    elif phase == "cam":
        core = json.loads((out / "core-result.json").read_text())
        assert core["status"] == "passed"
        target_hash = digest(out / "target.brep")
        assert target_hash == core["target_sha256"]
        # Assign explicitly: FreeCAD Shape properties can return copies.
        target = Part.Shape()
        target.read(str(out / "target.brep"))
        model.Shape = target
        doc.recompute()

        import Path
        import Path.Main.Job as Job
        import Path.Op.Drilling as Drilling
        from Path.Tool.toolbit import ToolBit
        from Path.Post.scripts import linuxcnc_post

        job = Job.Create("Job", [model], None)
        for prop in ("ExtXneg", "ExtXpos", "ExtYneg", "ExtYpos", "ExtZneg", "ExtZpos"):
            setattr(job.Stock, prop, 0)
        drill = ToolBit.from_shape_id("drill.fcstd", label="S1_D2_Drill")
        tool = drill.attach_to_doc(doc=doc)
        tool.Diameter = 2
        if "TipAngle" in tool.PropertiesList:
            tool.TipAngle = 118
        if "Length" in tool.PropertiesList:
            tool.Length = 20
        tc = job.Tools.Group[0]
        tc.Label = "TC: D2 Drill 118deg"
        tc.Tool = tool
        tc.ToolNumber = 1
        tc.SpindleSpeed = 1000
        tc.HorizFeed = "60 mm/min"
        tc.VertFeed = "60 mm/min"
        doc.recompute()

        op = Drilling.Create("Drilling", parentJob=job)
        op.ToolController = tc
        op.Proxy.findAllHoles(op)
        for name, value in (("StartDepth", 0), ("FinalDepth", -1.6),
                            ("SafeHeight", 2), ("ClearanceHeight", 3),
                            ("RetractHeight", 1)):
            op.setExpression(name, None)
            setattr(op, name, value)
        op.ExtraOffset = "Drill Tip"
        op.PeckEnabled = False
        doc.recompute()
        assert len(op.Base) == 1, "Expected exactly one drilled feature"
        assert len(op.Path.Commands) > 0
        assert bounds(job.Stock.Shape) == {"min": [0.0, 0.0, -1.5], "max": [8.0, 6.0, 0.0]}

        nc = out / "freecad-s1.nc"
        post_args = "--no-show-editor --no-tlo --precision 5"
        # Zero tool offset is explicit for this reference tool-tip-coordinate case.
        # Do not alter or filter the final NC before sending it to CAMotics.
        text = linuxcnc_post.export([tc, op], str(nc), post_args)
        assert nc.exists() and nc.stat().st_size > 0 and text
        assert "G81" in text and "X3.00000" in text and "Y2.00000" in text
        assert all(line.count("(") == line.count(")") <= 1 for line in text.splitlines())
        assert digest(out / "target.brep") == target_hash
        doc.saveAs(str(out / "cam-job.FCStd"))
        record.update(
            target_sha256=target_hash,
            nc_sha256=digest(nc),
            stock_bounds=bounds(job.Stock.Shape),
            operation="Path.Op.Drilling",
            operations_count=len(job.Operations.Group),
            selected_features=[{"object": obj.Name, "subelements": list(subs)}
                               for obj, subs in op.Base],
            path_commands=[{"name": c.Name, "parameters": dict(c.Parameters)}
                           for c in op.Path.Commands],
            tool_properties={p: str(getattr(tool, p)) for p in tool.PropertiesList
                             if p in ("Diameter", "TipAngle", "Length", "CuttingEdgeHeight")},
            postprocessor="FreeCAD bundled linuxcnc_post",
            postprocessor_arguments=post_args,
            linuxcnc_runtime_required=False,
            reference_offsets={"G54": [0, 0, 0], "tool_length_offset": 0},
            physical_cutting_parameters_qualified=False,
            production_process_card_adapter_implemented=False,
        )
    else:
        raise ValueError("Unknown phase")
    record["status"] = "passed"
    App.closeDocument(doc.Name)
except Exception as error:
    record.update(status="failed", error=str(error), traceback=traceback.format_exc())
    traceback.print_exc()
finally:
    record["seconds_inside_probe"] = time.monotonic() - started
    (out / (phase + "-result.json")).write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2), flush=True)
