// Minimal test-only types; only the unmodified upstream depth() is exercised.
// This harness is not a build or replacement of CAMotics.
#pragma once
#include <algorithm>
#include <cmath>
#include <vector>
namespace cb {
struct Vector3D {
  double a,b,c;
  Vector3D(double x,double y,double z):a(x),b(y),c(z){}
  double x()const{return a;} double y()const{return b;} double z()const{return c;}
};
struct Rectangle3D {};
}
namespace CAMotics {
class Sweep {
protected:
  void getBBoxes(const cb::Vector3D&,const cb::Vector3D&,
                std::vector<cb::Rectangle3D>&,double,double,double,double)const{}
};
class ConicSweep: public Sweep {
  const double l,rt,rb,Tm;
public:
  ConicSweep(double length,double radius1,double radius2=-1);
  void getBBoxes(const cb::Vector3D&,const cb::Vector3D&,
                std::vector<cb::Rectangle3D>&,double)const;
  double depth(const cb::Vector3D&,const cb::Vector3D&,const cb::Vector3D&)const;
};
}
