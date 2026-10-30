"""Bounded STL inspection, exact indexed display export and sectional IoU.

The IoU reference is the independent S1 CAD's verified analytic section.
The measurement is a numerical volume estimate, not a tolerance certificate.
"""
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "step2gcode_demo/validation"))
from local_resource_limits import require_bounded_worker
import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.ops import polygonize_full, unary_union

MAX_TRIANGLES = 500_000
STL_DTYPE = np.dtype([("normal", "<f4", (3,)), ("points", "<f4", (3, 3)), ("attribute", "<u2")])


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def load_mesh(path):
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(84)
    count = struct.unpack_from("<I", header, 80)[0]
    if not 0 < count <= MAX_TRIANGLES or path.stat().st_size != 84 + count * 50:
        raise ValueError("Invalid or over-budget STL")
    raw = np.memmap(path, dtype=STL_DTYPE, mode="r", offset=84, shape=(count,))
    points = np.ascontiguousarray(raw["points"].reshape(-1, 3))
    if not np.isfinite(points).all():
        raise ValueError("Non-finite mesh coordinates")
    points[points == 0] = 0
    _, first, inverse = np.unique(points.view("V12").reshape(-1), return_index=True, return_inverse=True)
    vertices = points[first]
    faces = inverse.reshape(-1, 3).astype("<u4")
    canonical = np.ascontiguousarray(np.sort(faces, axis=1)).view("V12").reshape(-1)
    _, keep = np.unique(canonical, return_index=True)
    duplicates = len(faces) - len(keep)
    faces = faces[np.sort(keep)]
    triangles = vertices[faces].astype(np.float64)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    if np.any(np.linalg.norm(normals, axis=1) <= 1e-16):
        raise ValueError("Degenerate mesh triangle")
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    signs = np.where(edges[:, 0] < edges[:, 1], 1, -1)
    edges.sort(axis=1)
    _, edge_ids, counts = np.unique(np.ascontiguousarray(edges).view("<u8").reshape(-1),
                                    return_inverse=True, return_counts=True)
    winding = np.bincount(edge_ids, weights=signs)
    if not np.all(counts == 2) or np.any(winding != 0):
        raise ValueError("Mesh is not closed and consistently oriented; no hole filling or repair")
    volume = float(np.einsum("ij,ij->", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])) / 6)
    if volume <= 0:
        raise ValueError("Non-positive oriented volume")
    return vertices, faces, {
        "source_sha256": sha(path), "raw_triangles": count,
        "exact_duplicate_faces_removed": duplicates, "triangles": len(faces),
        "vertices": len(vertices), "watertight": True, "consistent_winding": True,
        "volume_mm3": volume, "bounds": {"min": vertices.min(axis=0).tolist(), "max": vertices.max(axis=0).tolist()},
        "registration_or_alignment": False, "holes_filled": False, "vertex_displacement_mm": 0,
    }


def write_indexed(path, vertices, faces):
    with Path(path).open("xb") as stream:
        stream.write(b"GLWMESH1")
        stream.write(struct.pack("<II", len(vertices), len(faces)))
        stream.write(np.asarray(vertices, dtype="<f4").tobytes())
        stream.write(np.asarray(faces, dtype="<u4").tobytes())


class Sections:
    def __init__(self, vertices, faces):
        self.triangles = vertices[faces].astype(np.float64)
        self.zmin = self.triangles[:, :, 2].min(axis=1)
        self.zmax = self.triangles[:, :, 2].max(axis=1)
        self.calls = 0

    def at(self, z):
        self.calls += 1
        tri = self.triangles[(self.zmin < z) & (self.zmax > z)]
        if len(tri) == 0:
            return Polygon()
        if len(tri) > 12_000:
            raise ValueError("Cross section exceeds local line budget")
        # Mid-interval quadrature avoids slicing exactly through a vertex.
        points = np.empty((len(tri), 2, 2), dtype=np.float64)
        added = np.zeros(len(tri), dtype=np.int8)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = tri[:, i], tri[:, j]
            hit = ((a[:, 2] < z) & (b[:, 2] > z)) | ((b[:, 2] < z) & (a[:, 2] > z))
            ids = np.flatnonzero(hit)
            ratio = (z - a[hit, 2]) / (b[hit, 2] - a[hit, 2])
            xy = a[hit, :2] + ratio[:, None] * (b[hit, :2] - a[hit, :2])
            points[ids, added[ids]] = xy
            added[ids] += 1
        if not np.all(added == 2):
            raise ValueError("Ambiguous section edge")
        # Only equalize floating interpolation roundoff; do not repair gaps.
        points = np.round(points, 8)
        points = points[np.any(points[:, 0] != points[:, 1], axis=1)]
        polygons, cuts, dangles, invalid = polygonize_full(points)
        if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
            raise ValueError("Unclosed or invalid section")
        pieces = list(polygons.geoms)
        outer_rings = [Polygon(piece.exterior) for piece in pieces]
        selected = [piece for piece in pieces if
                    sum(ring.contains(piece.representative_point()) for ring in outer_rings) % 2 == 1]
        section = unary_union(selected)
        if not section.is_valid:
            raise ValueError("Invalid material section")
        return section


def integrate(sections, levels, reference_polygon, reference_z, order, comparison_polygon=None):
    nodes, weights = np.polynomial.legendre.leggauss(order)
    volumes = np.zeros(4, dtype=np.float64)  # actual, target, intersection, optional shifted control
    for lo, hi in zip(levels[:-1], levels[1:]):
        if hi - lo <= 1e-10:
            continue
        half = (hi - lo) / 2
        for node, weight in zip(nodes, weights):
            z = (hi + lo) / 2 + half * node
            section = sections.at(z)
            target = reference_polygon if reference_z[0] < z < reference_z[1] else Polygon()
            intersection = section.intersection(target).area
            alternative = 0
            if comparison_polygon is not None and not target.is_empty:
                alternative = section.intersection(comparison_polygon).area
            volumes += weight * half * np.array((section.area, target.area, intersection, alternative))
    a, b, intersection, alternative = volumes
    union = a + b - intersection
    if not (union > 0 and -1e-8 <= intersection <= min(a, b) + 1e-8):
        raise ValueError("Invalid intersection / union")
    return {"actual_volume_mm3": float(a), "target_volume_mm3": float(b),
            "intersection_mm3": float(intersection), "union_mm3": float(union),
            "iou": float(intersection / union), "alternative_intersection_mm3": float(alternative),
            "quadrature_order": order}


def cube_mesh(low, high):
    x, y, z = low
    X, Y, Z = high
    vertices = np.array([(x, y, z), (X, y, z), (X, Y, z), (x, Y, z),
                         (x, y, Z), (X, y, Z), (X, Y, Z), (x, Y, Z)], dtype=np.float64)
    faces = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
                      (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
                      (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)])
    return vertices, faces


def controls():
    results = []
    for name, lo, hi, expected in (
        ("identical_cube", (0, 0, 0), (1, 1, 1), 1),
        ("same_volume_shifted_cube", (0.25, 0, 0), (1.25, 1, 1), 0.6),
        ("disjoint_cube", (2, 0, 0), (3, 1, 1), 0),
    ):
        vertices, faces = cube_mesh(lo, hi)
        value = integrate(Sections(vertices, faces), [0, 1], box(0, 0, 1, 1), (0, 1), 2)
        assert abs(value["iou"] - expected) < 1e-10
        results.append({"case": name, "expected": expected, "measured": value["iou"], "pass": True})
    vertices, faces = cube_mesh((0, 0, -1.5), (8, 6, 0))
    reference = box(0, 0, 8, 6).difference(Point(3, 2).buffer(1, quad_segs=2048))
    value = integrate(Sections(vertices, faces), [-1.5, 0], reference, (-1.5, 0), 2)
    expected = (72 - math.pi * 1.5) / 72
    assert abs(value["iou"] - expected) < 1e-7
    results.append({"case": "hole_not_drilled", "expected": expected, "measured": value["iou"], "pass": True})
    return results


def main():
    require_bounded_worker()
    started = time.monotonic()
    output = ROOT / "web/content/s1-artifacts"
    source = ROOT / "step2gcode_demo/out_predevelopment_s1/verified-post/fine.stl"
    reference = json.loads((output / "reference.json").read_text())
    assert reference["reference_class"] == "axis_aligned_box_minus_one_z_through_cylinder"
    assert sha(ROOT / reference["source"]) == reference["sha256"]
    assert sha(source) == "4680f7055a581fa4cce0d30895fb8fe409ae9e4834d4549a8a78cd93ddf296b7"
    control_results = controls()
    vertices, faces, inspection = load_mesh(source)
    print("Full final mesh validated", inspection["triangles"], flush=True)
    write_indexed(output / "actual.mesh", vertices, faces)
    target_vertices, target_faces, target_inspection = load_mesh(output / "target-display.stl")
    write_indexed(output / "target.mesh", target_vertices, target_faces)
    zlo, zhi = reference["bounds"]["min"][2], reference["bounds"]["max"][2]
    levels = np.unique(np.concatenate((vertices[:, 2].astype(np.float64), [zlo, zhi])))
    if len(levels) > 192:
        raise ValueError(f"Too many section levels for local budget: {len(levels)}")
    print("Section levels", len(levels), flush=True)
    center = reference["hole_center_xy"]
    radius = reference["hole_radius_mm"]
    rectangle = box(*reference["bounds"]["min"][:2], *reference["bounds"]["max"][:2])
    target = rectangle.difference(Point(*center).buffer(radius, quad_segs=1024))
    refined_target = rectangle.difference(Point(*center).buffer(radius, quad_segs=2048))
    wrong_hole = rectangle.difference(Point(center[0] + 0.25, center[1]).buffer(radius, quad_segs=2048))
    sections = Sections(vertices, faces)
    coarse = integrate(sections, levels, target, (zlo, zhi), 2)
    print("Order 2 IoU", coarse["iou"], flush=True)
    refined = integrate(sections, levels, refined_target, (zlo, zhi), 3, wrong_hole)
    assert abs(refined["actual_volume_mm3"] - inspection["volume_mm3"]) < 1e-4
    stability = abs(coarse["iou"] - refined["iou"])
    assert stability < 1e-5
    wrong_intersection = refined.pop("alternative_intersection_mm3")
    wrong_iou = wrong_intersection / (refined["actual_volume_mm3"] + refined["target_volume_mm3"] - wrong_intersection)
    assert wrong_iou < refined["iou"] - 0.005
    control_results.append({"case": "same_volume_wrong_hole_reference", "iou": wrong_iou,
                            "pass": True, "scope": "comparison sensitivity only; original CAD retained unchanged"})
    result = {
        "schema": "genlaw.sectional-iou.v1", "status": "measured_numerical_estimate",
        "case_id": "s1", "iou": refined["iou"], "iou_percent": refined["iou"] * 100,
        "method": "STL cross-section polygons integrated over all vertex-height intervals with 3-point Gauss quadrature; independent analytic CAD section",
        "units": "mm", "registration": "none; same declared G54 / CAD frame",
        "actual_mesh": inspection, "reference_cad": reference,
        "volumes": refined, "coarser_check": coarse,
        "absolute_iou_refinement_difference": stability, "section_levels": len(levels),
        "section_evaluations": sections.calls, "section_roundoff_quantization_mm": 1e-8,
        "circle_polygon_segments": 8192, "circle_max_sag_mm": radius * (1 - math.cos(math.pi / 8192)),
        "excess_material_mm3": refined["actual_volume_mm3"] - refined["intersection_mm3"],
        "missing_material_mm3": refined["target_volume_mm3"] - refined["intersection_mm3"],
        "controls": control_results, "seconds_inside_worker": time.monotonic() - started,
        "numerical_stability_is_not_certified_error_bound": True,
        "full_tolerance_acceptance": False, "roughness_or_material_physics_verified": False,
        "original_cad_unchanged": sha(ROOT / reference["source"]) == reference["sha256"],
        "scope": "S1 prismatic CAD reference only; not a generic multi-axis geometry validator",
        "display_mesh": {"representation": "exact indexed float32 vertices and original triangles", "decimation": False},
    }
    with (output / "metrics.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"iou_percent": result["iou_percent"], "seconds": result["seconds_inside_worker"],
                      "refinement_difference": stability, "controls": len(control_results)}), flush=True)


if __name__ == "__main__":
    main()
