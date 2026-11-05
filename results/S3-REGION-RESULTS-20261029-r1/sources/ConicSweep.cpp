/******************************************************************************\

  CAMotics is an Open-Source simulation and CAM software.
  Copyright (C) 2011-2019 Joseph Coffland <joseph@cauldrondevelopment.com>

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 2 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.

\******************************************************************************/

#include "ConicSweep.h"

#include <cbang/log/Logger.h>

#include <limits>

using namespace std;
using namespace cb;
using namespace CAMotics;


ConicSweep::ConicSweep(double length, double radius1, double radius2) :
  l(length), rt(radius1), rb(radius2 == -1 ? radius1 : radius2),
  Tm((rt - rb) / l) {
}


void ConicSweep::getBBoxes(const Vector3D &start, const Vector3D &end,
                           vector<Rectangle3D> &bboxes,
                           double tolerance) const {
  Sweep::getBBoxes(start, end, bboxes, rt < rb ? rb : rt, l, 0, tolerance);
}


namespace {
  inline double sqr(double x) {return x * x;}
}


double ConicSweep::depth(const Vector3D &A, const Vector3D &B,
                         const Vector3D &P) const {
  const double Ax = A.x(), Ay = A.y(), Az = A.z();
  const double Bx = B.x(), By = B.y(), Bz = B.z();
  const double Px = P.x(), Py = P.y(), Pz = P.z();

  // Check z-height
  if (Pz < min(Az, Bz) || max(Az, Bz) + l < Pz) return -1;

  // Pure Z translation has an analytic swept-frustum cross section.
  // Avoid the repeated quadratic root on the tool axis: cancellation in
  // gamma^2 - epsilon*rho can otherwise reject points inside the sweep.
  if (Ax == Bx && Ay == By && 0 < l) {
    const double hMin = max(0.0, Pz - max(Az, Bz));
    const double hMax = min(l, Pz - min(Az, Bz));
    if (hMax < hMin) return -1;
    const double radius = rb + Tm * (Tm < 0 ? hMin : hMax);
    return sqr(Px - Ax) + sqr(Py - Ay) <= sqr(radius) ? 1 : -1;
  }

  // A horizontal cylinder sweep is a finite capsule in each supported XY
  // section. Compare distances directly instead of subtracting nearly equal
  // quadratic terms at tangent tool paths. Such cancellation can leave false
  // internal material sheets even when the two cutter envelopes exactly meet.
  // Extended precision is used for the comparison, without enlarging the tool
  // or changing the surface / simulation resolution.
  if (Az == Bz && rt == rb && 0 < l) {
    const long double dx = (long double)Bx - Ax;
    const long double dy = (long double)By - Ay;
    const long double px = (long double)Px - Ax;
    const long double py = (long double)Py - Ay;
    const long double length2 = dx * dx + dy * dy;
    const long double projection = px * dx + py * dy;
    const long double radius2 = (long double)rb * rb;
    if (projection <= 0) return px * px + py * py <= radius2 ? 1 : -1;
    if (projection >= length2) {
      const long double qx = (long double)Px - Bx;
      const long double qy = (long double)Py - By;
      return qx * qx + qy * qy <= radius2 ? 1 : -1;
    }
    const long double cross = px * dy - py * dx;
    return cross * cross <= radius2 * length2 ? 1 : -1;
  }

  // epsilon * beta^2 + gamma * beta + rho = 0
  double epsilon = sqr(Bx - Ax) + sqr(By - Ay) - sqr(Tm * (Bz - Az));

  // If this is a straight up and down move of a cylindrical tool choose
  // a fake arbitrarily small epsilon.
  // TODO improve cylindrical computation speed by computing special case
  if (epsilon == 0 && Bz != Az && Tm == 0) epsilon = 0.000000001;

  const double gamma = (Ax - Px) * (Bx - Ax) + (Ay - Py) * (By - Ay) +
    (sqr(Tm) * (Az - Pz) - Tm * rb) * (Az - Bz);
  const double rho = sqr(Ax - Px) + sqr(Ay - Py) - sqr(Tm * (Az - Pz) - rb);
  const double sigma = sqr(gamma) - epsilon * rho;

  // Check if solution is valid
  if (epsilon == 0 || sigma < 0) return -1;

  const double beta = (-gamma - sqrt(sigma)) / epsilon; // Quadradic equation

  // Check if z-heights make sense
  const double Qz = (Bz - Az) * beta + Az;

  // Contact point is outside of z-height range.
  if (Pz < Qz || Qz + l < Pz) {
    // Check if point is cut by bottom disc
    if (rb) {
      const double beta = (Pz - Az) / (Bz - Az); // E is on AB at z-height

      if (0 <= beta && beta <= 1) {
        // Compute squared distance to E on XY plane
        const double Ex = beta * (Bx - Ax) + Ax;
        const double Ey = beta * (By - Ay) + Ay;
        const double d2 = sqr(Ex - Px) + sqr(Ey - Py);

        if (d2 <= rb * rb) return 1;
      }
    }

    // Check if point is cut by top disc
    if (rt) {
      const double beta = (Pz - Az - l) / (Bz - Az); // E is on AB at z-height

      if (0 <= beta && beta <= 1) {
        // Compute squared distance to E on XY plane
        const double Ex = beta * (Bx - Ax) + Ax;
        const double Ey = beta * (By - Ay) + Ay;
        const double d2 = sqr(Ex - Px) + sqr(Ey - Py);

        if (d2 <= rt * rt) return 1;
      }
    }

    return -1;
  }

  // Check that it's on the line segment
  if (beta < 0 || 1 < beta) return -1;

  return 1;
}
