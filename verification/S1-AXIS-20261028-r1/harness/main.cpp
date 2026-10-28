#include "ConicSweep.h"
#include <iomanip>
#include <iostream>
int main(){
  double l,rt,rb,ax,ay,az,bx,by,bz,px,py,pz;
  while(std::cin>>l>>rt>>rb>>ax>>ay>>az>>bx>>by>>bz>>px>>py>>pz){
    CAMotics::ConicSweep sweep(l,rt,rb);
    double result=sweep.depth({ax,ay,az},{bx,by,bz},{px,py,pz});
    double tm=(rt-rb)/l, e=(bx-ax)*(bx-ax)+(by-ay)*(by-ay)-(tm*(bz-az))*(tm*(bz-az));
    double g=(ax-px)*(bx-ax)+(ay-py)*(by-ay)+(tm*tm*(az-pz)-tm*rb)*(az-bz);
    double r=(ax-px)*(ax-px)+(ay-py)*(ay-py)-(tm*(az-pz)-rb)*(tm*(az-pz)-rb);
    std::cout<<std::setprecision(17)<<result<<" "<<(g*g-e*r)<<"\n";
  }
  return std::cin.eof()?0:1;
}
