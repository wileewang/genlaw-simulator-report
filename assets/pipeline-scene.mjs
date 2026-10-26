// Illustrative process animation. No CAM, NC execution, or acceptance computation.
// Geometry projection and solids use the vendored Anatomy toolkit (MIT).
import {iso, slabOf, cylinder, ring, lineOnTop, pathOf, segment} from './anatomy/iso-kit.mjs';

export const STEPS = [
  {title:'零件与要求', owner:'上游模型', input:'CadQuery + 标注', output:'几何 + 绑定要求', state:'待绑定', has:'已有几何输入', gap:'几何特征 ↔ 公差 / 粗糙度', detail:'上游增加绑定与引用校验'},
  {title:'工艺 Agent', owner:'Agent + 程序校验', input:'几何 + 资源', output:'JSON 工艺卡', state:'待建立', has:'有限定工序规则', gap:'工艺 Agent + 工艺卡规范', detail:'补资源库与参数 / 能力校验'},
  {title:'CAM 编程', owner:'程序 · FreeCAD CAM', input:'工艺卡 + CAD', output:'最终 G-code', state:'待接入', has:'有自研限定 NC 生成', gap:'工艺卡 → CAM Job 适配', detail:'接 FreeCAD CAM 与设备后处理'},
  {title:'去料仿真', owner:'程序 · 仿真引擎', input:'NC + 实际来料', output:'剩余材料 + 记录', state:'有基础', has:'有限定工序去料与材料接续', gap:'接通独立外部仿真', detail:'适配最终 NC、实际来料与设备'},
  {title:'几何验收', owner:'程序 · 几何比较', input:'剩余材料 + 原目标', output:'差异报告 / 回放', state:'有基础', has:'有基础几何比对与回放', gap:'按特征公差验收 + 失败回传', detail:'粗糙度仍需另行验证'},
];
export const DURATION = 25000;
export function frameAt(time) {
  if (!Number.isFinite(time)) throw new TypeError('Finite playback time required');
  const t=Math.max(0,Math.min(DURATION,time));
  const stage=Math.min(4,Math.floor(t/5000));
  return {stage,progress:(t-stage*5000)/5000};
}
const C={ink:'#183e43',muted:'#688084',line:'#bdcfce',teal:'#16867f',purple:'#8162b1',orange:'#c17a42',blue:'#4c83ab'};
const centers=[155,405,655,905,1155];
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const txt=(x,y,s,size=14,color=C.ink,anchor='middle',weight=400)=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" text-anchor="${anchor}" font-weight="${weight}">${esc(s)}</text>`;
const line=(d,color=C.ink,width=1.5,dash='')=>`<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"${dash?` stroke-dasharray="${dash}"`:''}/>`;
const rect=(x,y,w,h,fill,stroke='none',r=8)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
function solid(s,fill='#e4ece8',stroke=C.ink){
  return `<g><path d="${s.fill}" fill="${fill}"/>${s.shades.map((d,i)=>d?`<path d="${d}" fill="${stroke}" opacity="${.08+i*.055}"/>`:'').join('')}<path d="${s.top}" fill="${fill}"/>${line(s.crease,stroke,.9)}${line(s.outline,stroke,1.2)}</g>`;
}
const slab=(p,plan,z,h,fill)=>solid(slabOf(plan,z,h,p),fill);
const cyl=(p,x,y,r,z,h,fill)=>solid(cylinder(x,y,r,z,h,p,24),fill);
const plan={x:-22,y:-17,w:44,d:34,r:3};
function plate(p,z=20,hole=6,offset=0,color='#c9dfd8'){
  let s=slab(p,plan,z,7,color);
  if(hole>0){
    const d=ring(offset,0,hole,z+7,p,32);
    s+=`<path d="${d}" fill="#37635f"/>`;
    s+=line(ring(offset,0,hole*.64,z+7,p,32),'#8bb2a6',1.1);
    s+=line(d,'#244d4b',1.2);
  }
  return s;
}
function base(p,active,color){
  return `<ellipse cx="${p.origin[0]}" cy="310" rx="91" ry="26" fill="#183e43" opacity=".045"/>`+
    slab(p,{x:-30,y:-24,w:60,d:48,r:6},0,4,active?'#dcece6':'#edf1ed')+
    (active?line(slabOf({x:-30,y:-24,w:60,d:48,r:6},0,4,p).top,color,2):'');
}
function partScene(p,t){
  const top=iso([0,0,27],p), pulse=6+5*Math.sin(t*Math.PI);
  return plate(p)+`<circle cx="${top[0]}" cy="${top[1]}" r="${pulse+15}" fill="none" stroke="${C.purple}" stroke-width="1.3" stroke-dasharray="3 4"/>`+
    line(`M${top[0]+17} ${top[1]-9}L${p.origin[0]+57} 164V151`,C.purple,1.3)+
    rect(p.origin[0]-12,118,143,34,'#eee8f6')+txt(p.origin[0]+59,140,'孔 ↔ Ø10 ±0.1',14,C.purple)+
    txt(p.origin[0]-55,190,'CAD',12,C.teal,'middle',700);
}
function agentScene(p,t){
  let s='';
  for(let i=0;i<3;i++){
    const z=14+i*9+(i===2?8*Math.sin(t*Math.PI):0);
    s+=slab(p,{x:-19+i*2,y:-16+i*2,w:38,d:29,r:2},z,2,i===2?'#e9e0f3':'#f6f4f9');
    for(let j=0;j<3;j++)s+=line(lineOnTop([-11+i*2,-10+i*2+j*7],[11+i*2,-10+i*2+j*7],z+2,p),i===2?C.purple:'#9b90a8',1.8);
  }
  const x=p.origin[0];
  s+=rect(x-40,114,80,32,'#eee8f6')+txt(x,136,'Agent',15,C.purple,'middle',600);
  for(const [dx,dy] of [[-57,28],[57,28],[0,-18]])s+=line(`M${x} 114L${x+dx} ${114+dy}`,C.purple,1,'3 5')+`<circle cx="${x+dx}" cy="${114+dy}" r="4" fill="${C.purple}" opacity=".7"/>`;
  return s;
}
function machineFrame(p){
  return slab(p,{x:-26,y:-20,w:52,d:40,r:3},7,7,'#dce6e3')+
    slab(p,{x:-25,y:-19,w:6,d:8,r:1},14,43,'#e5eae7')+
    slab(p,{x:19,y:-19,w:6,d:8,r:1},14,43,'#d5dfdc')+
    slab(p,{x:-26,y:-20,w:52,d:10,r:2},57,7,'#ecede8');
}
function camScene(p,t){
  let s=machineFrame(p)+plate(p,17,0,0,'#e5ddd0');
  s+=line(ring(0,0,6,24,p,36),C.orange,2,'3 3');
  s+=line(segment([-15,0,38],[0,0,38],p)+segment([0,0,38],[0,0,24],p),C.orange,2,'5 4');
  const z=32+7*Math.cos(t*2*Math.PI);
  s+=cyl(p,0,0,4,z+8,12,'#d8e3e2')+cyl(p,0,0,1.5,z,8,'#80a9a3');
  const x=p.origin[0];
  s+=rect(x+52,171,43,39,'#fff8ed','#dfbe98',6)+txt(x+73.5,196,'NC',15,C.orange,'middle',700);
  return s;
}
function simulationScene(p,t){
  const cut=Math.max(0,Math.min(1,(t-.3)/.43));
  let s=slab(p,{x:-27,y:-22,w:54,d:44,r:3},7,7,'#dce6e3')+plate(p,17,cut*6,0,'#d4e3de');
  // A preset illustrative plunge and retract, not an executed NC motion.
  const z=t<.35?54-30*t/.35:t<.7?24-9*(t-.35)/.35:15+39*(t-.7)/.3;
  s+=line(segment([0,0,15],[0,0,62],p),'#b1c7c2',1,'3 5');
  s+=cyl(p,0,0,4,z+9,13,'#d8e3e2')+cyl(p,0,0,1.8,z,9,'#5b9690');
  if(t>.2&&t<.78){
    for(let i=0;i<6;i++){
      const a=i*Math.PI/3+t*5, q=iso([Math.cos(a)*(6+cut*5),Math.sin(a)*(6+cut*5),26+(i%3)*2],p);
      s+=line(`M${q[0]} ${q[1]}l3 -2`,C.orange,1.7);
    }
  }
  return s;
}
function acceptanceScene(p,t,failed){
  let s=plate(p,18,6,failed?8:0,'#d1e3db');
  const ghostZ=25+12*(1-t);
  s+=line(slabOf(plan,ghostZ,0,p).top,C.blue,1.5,'5 4')+line(ring(0,0,6,ghostZ,p,32),C.blue,1.6,'3 3');
  const x=p.origin[0]+70,y=173,color=failed?C.orange:C.teal;
  s+=`<circle cx="${x}" cy="${y}" r="18" fill="${failed?'#fcf0e1':'#e4f1e9'}" stroke="${color}"/>`;
  s+=line(failed?`M${x-5} ${y-5}l10 10M${x+5} ${y-5}l-10 10`:`M${x-8} ${y}l5 5 11 -11`,color,2);
  if(failed)s+=line(ring(8,0,9,25,p,32),C.orange,2);
  return s;
}
const scenes=[partScene,agentScene,camScene,simulationScene,acceptanceScene];
export function renderPipeline({stage=0,progress=0,failed=false,overview=false}={}){
  if(!Number.isInteger(stage)||stage<0||stage>4||!Number.isFinite(progress)||progress<0||progress>1)throw new RangeError('Invalid illustrative frame');
  let body=rect(0,0,1320,510,'#f8faf6');
  body+=rect(428,20,464,40,'#edf2ec','#dce5db',20)+txt(660,46,'共享资源：材料 / 设备 / 刀具 / 夹具',16,C.muted);
  body+=line('M660 60V81M405 81H905M405 81V105M655 81V105M905 81V105','#bdcebd',1.2,'3 4');
  for(let i=0;i<5;i++){
    const active=!overview&&stage===i,p={origin:[centers[i],282],scale:1.9,azimuth:45,elevation:30};
    const color=i<2?C.purple:C.teal;
    const t=active ? progress : (i===3 ? ((stage>3 || overview) ? .88 : 0) : .7);
    body+=`<g data-step="${i}" class="station" opacity="${overview||active?1:.62}">`;
    body+=base(p,active,color)+scenes[i](p,t,failed&&i===4);
    body+=txt(centers[i],358,`${String(i+1).padStart(2,'0')}  ${STEPS[i].title}`,19,C.ink,'middle',650);
    body+=rect(centers[i]-39,371,78,24,i<3?'#f4ebdf':'#e7f0e8','none',12)+txt(centers[i],388,STEPS[i].state,12,i<3?C.orange:C.teal);
    body+='</g>';
  }
  const labels=['几何 + 要求','工艺卡','G-code','剩余材料'];
  for(let i=0;i<4;i++){
    const x=centers[i]+106,end=centers[i+1]-104;
    body+=line(`M${x} 259H${end}`,C.muted,1.6)+`<path d="M${end-6} 255l7 4-7 4" fill="none" stroke="${C.muted}" stroke-width="1.6"/>`;
    body+=txt((x+end)/2,284,labels[i],12,C.muted);
    if(stage===i&&progress>.6&&!overview){
      const at=x+(end-x)*(progress-.6)/.4;
      body+=`<circle cx="${at}" cy="259" r="4" fill="${C.orange}"/>`;
    }
  }
  body+=line('M155 404V425H1155V404',C.blue,1.2,'4 4')+`<path d="M1151 410l4-7 4 7" fill="none" stroke="${C.blue}" stroke-width="1.2"/>`;
  body+=rect(530,414,250,23,'#f8faf6')+txt(655,430,'原始 CAD / 公差 → 独立验收',13,C.blue);
  const feedback=failed&&stage===4;
  body+=line('M1155 441V468H405V441',feedback?C.orange:'#ccd5ce',1.5,'5 5')+`<path d="M401 447l4-7 4 7" fill="none" stroke="${feedback?C.orange:'#ccd5ce'}" stroke-width="1.5"/>`;
  body+=rect(648,455,264,25,'#f8faf6')+txt(780,473,'失败证据 → 工艺 Agent 修改方案',13,feedback?C.orange:C.muted);
  if(feedback){const x=1155-750*progress;body+=`<circle cx="${x}" cy="468" r="4" fill="${C.orange}"/>`;}
  body+=txt(22,496,'流程示意 · 非实际加工结果',11,C.muted,'start')+txt(1298,496,'GENLAW / ANATOMY',10,C.muted,'end');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1320" height="510" viewBox="0 0 1320 510" role="img" aria-labelledby="flow-title flow-desc"><title id="flow-title">从零件到几何验收的 Anatomy 流程示意</title><desc id="flow-desc">零件与要求，工艺 Agent，CAM 编程，去料仿真，几何验收。共享材料、设备、刀具和夹具。原始 CAD 和公差直接用于验收，失败回传 Agent。前三环节待绑定、建立或接入；仿真和验收已有基础。此图为预设流程示意，不是实际加工或计算结果。</desc><style>text{font-family:system-ui,'Noto Sans CJK SC','Microsoft YaHei','Droid Sans Fallback',sans-serif}.station{cursor:pointer}.station:hover{opacity:1}</style>${body}</svg>`;
}
