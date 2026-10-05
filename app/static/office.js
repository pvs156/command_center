// Original pixel artwork. The scene consumes stored run events; it creates no work events.
// name = the agent's (renameable) name; role = what they do.
export const ROLES = {
  scout: {name:'Sam',role:'Scout',title:'Job discovery',color:'#d6ab64',x:75,y:94,room:'topLeft'},
  eligibility: {name:'Grace',role:'Gatekeeper',title:'Eligibility checks',color:'#82bca0',x:166,y:94,room:'topLeft'},
  analyst: {name:'Ana',role:'Analyst',title:'Resume match & gaps',color:'#8da9d4',x:301,y:94,room:'topRight'},
  researcher: {name:'Rosa',role:'Researcher',title:'Company & contacts',color:'#bf9bcc',x:399,y:94,room:'topRight'},
  writer: {name:'Will',role:'Writer',title:'Tailoring & outreach',color:'#d89573',x:100,y:226,room:'bottomLeft'},
  intake: {name:'Ivy',role:'Intake',title:'Jobs you bring in',color:'#7fc4c9',x:171,y:226,room:'bottomLeft'},
  reviewer: {name:'Remy',role:'Reviewer',title:'Quality checks',color:'#bdd183',x:315,y:226,room:'bottomRight'},
  lead: {name:'You',role:'You',title:'Your decisions',color:'#eee1a6',x:396,y:226,room:'bottomRight'},
};
// Who hands work to whom: Ana feeds Will and Rosa in parallel; both feed Remy, who brings it to you.
const HANDOFF={scout:['eligibility'],intake:['eligibility'],eligibility:['analyst'],analyst:['writer','researcher'],writer:['reviewer'],researcher:['reviewer'],reviewer:['lead']};
export function setNames(names){for(const [id,n] of Object.entries(names||{}))if(ROLES[id]&&n)ROLES[id].name=n}
const DOORS={topLeft:[118,145],topRight:[354,145],bottomLeft:[118,156],bottomRight:[354,156]};
let canvas,ctx,raf,selected='scout',onSelect,agents={},packets=[],seen=new Set(),reduced=false,last=0,sceneTime=0;
const rect=(x,y,w,h,c)=>{ctx.fillStyle=c;ctx.fillRect(Math.round(x),Math.round(y),w,h)};
function text(t,x,y,color='#dbdfb9',size=5){ctx.font=`${size}px monospace`;ctx.fillStyle=color;ctx.fillText(t,x,y)}
function plant(x,y){rect(x-3,y+8,8,5,'#553f31');rect(x-4,y+6,10,4,'#b38b5f');rect(x,y-1,2,8,'#6c8752');rect(x-5,y,5,5,'#557751');rect(x+2,y-4,5,6,'#668c5a');rect(x-3,y-5,5,5,'#85a966');rect(x+1,y-1,4,3,'#a0b777')}
function bookcase(x,y,w=27){rect(x-1,y-1,w+2,20,'#243333');rect(x,y,w,19,'#594d3b');for(let row=0;row<2;row++){rect(x+2,y+2+row*9,w-4,6,'#2e3c3b');const colors=['#b37158','#b2b77b','#74908b','#d2b36d','#8e7d9e'];for(let j=0;j<8;j++){rect(x+3+j*3,y+3+row*9,2,5-(j%2),colors[(j+row)%5])}}}
function desk(x,y,accent='#739884'){rect(x+2,y+8,35,15,'#1b222b44');rect(x,y+1,35,18,'#604b35');rect(x,y,35,14,'#bb9c69');rect(x+2,y+2,31,10,'#c5a877');rect(x+2,y+15,3,7,'#6b5841');rect(x+30,y+15,3,7,'#6b5841');rect(x+8,y-7,17,13,'#354248');rect(x+9,y-6,15,10,'#889787');rect(x+10,y-5,13,8,'#243c3e');rect(x+12,y-3,8,1,accent);rect(x+12,y,5,1,accent);rect(x+15,y+6,5,2,'#43504b');rect(x+9,y+9,14,3,'#777f70');rect(x+28,y+5,3,4,'#ece1b9');rect(x+28,y+4,3,1,'#655943');rect(x+30,y+5,2,2,'#ece1b9');rect(x+11,y+24,13,8,'#384e4a');rect(x+12,y+23,11,6,accent);rect(x+16,y+31,3,3,'#36423e')}
function sofa(x,y,color='#865b59',vertical=false){if(vertical){rect(x+1,y+2,13,39,'#16222a55');rect(x,y,12,38,color);rect(x+2,y+3,9,15,'#aa7c6b');rect(x+2,y+20,9,15,'#aa7c6b');rect(x-2,y,4,38,'#684b49')}else{rect(x+2,y+2,40,17,'#16222a55');rect(x,y,39,16,color);rect(x+3,y+5,15,10,'#aa7c6b');rect(x+20,y+5,15,10,'#aa7c6b');rect(x,y,39,4,'#684b49');rect(x-2,y+3,4,14,'#684b49');rect(x+37,y+3,4,14,'#684b49')}}
function floor(x,y,w,h,color,wood=true){rect(x,y,w,h,color);if(wood){for(let yy=y;yy<y+h;yy+=8){rect(x,yy,w,1,'#604d3e44');for(let xx=x+((yy-y)%16?13:30);xx<x+w;xx+=34)rect(xx,yy,1,8,'#58493433')}}else{for(let yy=y;yy<y+h;yy+=12)for(let xx=x;xx<x+w;xx+=12)rect(xx,yy,11,11,((xx-x)/12+(yy-y)/12)%2?'#667d72':'#6d8379')}}
function room(x,y,w,h,color,wood){rect(x+3,y+5,w,h,'#101d2455');floor(x,y,w,h,color,wood);rect(x,y,w,8,'#334449');rect(x,y+8,w,3,'#223536');rect(x,y,5,h,'#2c3d41');rect(x+w-5,y,5,h,'#2c3d41');rect(x,y+h-5,w,5,'#29383a')}
function windowAt(x,y){rect(x-2,y-2,28,19,'#766d50');rect(x,y,24,15,'#a5bfaf');rect(x+2,y+2,9,11,'#75998e');rect(x+13,y+2,9,11,'#92b8a3');rect(x+11,y,2,15,'#d2cfaa');rect(x,y+7,24,2,'#d2cfaa');rect(x-3,y+15,30,3,'#b6a078')}
function scene(){
  rect(0,0,480,292,'#1d2a2d');rect(13,15,454,266,'#142024');
  room(18,21,210,120,'#a1845e',true);room(240,21,220,120,'#7f9993',false);
  room(18,160,210,111,'#a6896c',true);room(240,160,220,111,'#7b8a6c',true);
  floor(18,141,442,19,'#8c9888',false);rect(18,149,442,1,'#b0b6a0');
  // Door openings connect the four rooms to the central hallway.
  for(const [x,y] of Object.values(DOORS)){rect(x-10,y-10,20,21,'#939b7d');rect(x-11,y-7,1,15,'#bac0a0')}
  rect(228,20,12,251,'#203137');rect(228,140,12,21,'#939b7d');
  text('DISCOVERY & ELIGIBILITY',31,38,'#e8e3c0',5);text('RESEARCH LIBRARY',254,38,'#e4e9c9',5);
  text('APPLICATION STUDIO',31,177,'#eee1bc',5);text('YOUR REVIEW ROOM',254,177,'#e5e7ba',5);
  desk(59,69,'#bd945c');desk(151,69,'#80a787');desk(285,69,'#789cbb');desk(383,69,'#af8fb5');
  desk(84,204,'#c78e69');desk(299,204,'#aabd78');desk(380,204,'#d8c686');
  bookcase(34,45);bookcase(113,46,24);bookcase(250,45,26);bookcase(328,45,43);
  windowAt(175,43);windowAt(413,43);windowAt(33,190);
  plant(35,117);plant(210,114);plant(252,116);plant(443,116);plant(33,250);plant(211,245);plant(254,249);plant(442,246);
  // Intake desk: links and job descriptions you bring in arrive in the tray, then go to the Gatekeeper.
  desk(155,204,'#7fc4c9');
  rect(158,183,40,11,'#445a5c');rect(160,185,36,7,'#cfe3df');text('INTAKE',165,191,'#3f5b5d',4);
  rect(196,212,11,6,'#6d5a43');rect(197,210,9,3,'#e9e3c8');rect(198,208,8,2,'#f3efdc');
  rect(305,185,32,12,'#455943');rect(307,187,28,8,'#bcc9a2');text('REVIEW',310,193,'#566743',4);
  rect(377,182,41,15,'#445344');rect(379,184,37,11,'#dbd8ae');text('YOUR CALL',382,191,'#546445',4);
  // Corridor runner and hanging lamps.
  rect(155,145,51,10,'#a4ac8b');rect(157,147,47,6,'#b3b598');
  rect(272,145,51,10,'#a4ac8b');rect(274,147,47,6,'#b3b598');
  for(const x of [104,350]){rect(x,14,1,9,'#667a72');rect(x-5,23,11,3,'#b7af7d');rect(x-7,26,15,3,'#dec687')}
  rect(21,273,436,1,'#3a5050');text('A PLACE TO DO GOOD WORK.',181,284,'#7d9991',4);
}
function character(x,y,color,state,frame,isSelected,pending=0){
  x=Math.round(x);y=Math.round(y);let step=state==='walking'?Math.floor(frame/130)%2:0;
  rect(x-6,y+8,14,3,'#21322b44');
  if(isSelected){rect(x-9,y+11,19,1,'#e3e9b4');rect(x-10,y+7,1,4,'#e3e9b4');rect(x+10,y+7,1,4,'#e3e9b4')}
  rect(x-4,y-17,9,4,'#4a3c38');rect(x-5,y-14,11,5,'#4a3c38');rect(x-3,y-13,7,7,'#dcac85');rect(x-4,y-14,8,2,'#5d473b');
  rect(x-2,y-10,1,1,'#263938');rect(x+2,y-10,1,1,'#263938');rect(x-1,y-6,3,2,'#c69375');
  rect(x-4,y-4,9,8,color);rect(x-6,y-3,2,7,color);rect(x+5,y-3,2,7,color);
  rect(x-6,y+3,2,2,'#dcac85');rect(x+5,y+3,2,2,'#dcac85');rect(x-3,y+4,3,5-step,'#334b4c');rect(x+2,y+4,3,4+step,'#334b4c');rect(x-4,y+8-step,4,2,'#263b3d');rect(x+2,y+8+step,4,2,'#263b3d');
  if(state==='working'){rect(x+9,y-18,11,8,'#e0e7c6');text('···',x+10,y-13,'#48614e',5)}
  if(pending){const bob=Math.round(Math.sin(frame/260)*1.5);const label=String(pending>9?'9+':pending);const w=8+label.length*4;rect(x-w/2,y-31+bob,w,10,'#f0d27a');rect(x-1,y-21+bob,3,2,'#f0d27a');text(label,x-w/2+4,y-23+bob,'#5c4520',7);return}
  if(state==='waiting'){rect(x+9,y-19,9,9,'#e0cc8e');text('!',x+12,y-12,'#69553c',6)}
  if(state==='error'){rect(x+9,y-19,9,9,'#df9b7b');text('!',x+12,y-12,'#753f31',6)}
}
function tick(t){const dt=Math.min(50,t-last||16);last=t;sceneTime=t;scene();
  for(const [id,a] of Object.entries(agents)){
    if(a.path.length&&!reduced){const goal=a.path[0],dx=goal[0]-a.x,dy=goal[1]-a.y,dist=Math.hypot(dx,dy),speed=.075*dt;if(dist<speed){a.x=goal[0];a.y=goal[1];a.path.shift()}else{a.x+=dx/dist*speed;a.y+=dy/dist*speed}}
    else if(reduced&&a.path.length){const end=a.path.at(-1);a.x=end[0];a.y=end[1];a.path=[]}
    character(a.x,a.y,ROLES[id].color,a.path.length?'walking':a.state,t,id===selected,a.path.length?0:(a.pending||0));
    const label=ROLES[id].name;const w=label.length*3+8;rect(a.x-w/2,a.y+15,w,9,'#223935dd');text(label.toUpperCase(),a.x-w/2+4,a.y+21,'#dbe4c4',4);
  }
  for(const p of packets){p.progress=Math.min(1,p.progress+dt/1200);const x=p.from[0]+(p.to[0]-p.from[0])*p.progress,y=p.from[1]+(p.to[1]-p.from[1])*p.progress;rect(x,y,7,9,'#e4dbb2');rect(x+2,y+3,3,1,'#829478');rect(x+2,y+5,3,1,'#829478')}
  packets=packets.filter(p=>p.progress<1);raf=requestAnimationFrame(tick);
}
export function mountOffice(element,callback){unmountOffice();canvas=element;ctx=canvas.getContext('2d');canvas.width=960;canvas.height=584;ctx.scale(2,2);ctx.imageSmoothingEnabled=false;onSelect=callback;seen=new Set();packets=[];agents={};for(const [id,r] of Object.entries(ROLES))agents[id]={x:r.x,y:r.y,state:'idle',path:[]};
  canvas.onclick=e=>{const box=canvas.getBoundingClientRect(),x=(e.clientX-box.left)*480/box.width,y=(e.clientY-box.top)*292/box.height;let nearest=Object.keys(agents).sort((a,b)=>Math.hypot(x-agents[a].x,y-agents[a].y)-Math.hypot(x-agents[b].x,y-agents[b].y))[0];if(Math.hypot(x-agents[nearest].x,y-agents[nearest].y)<35){selected=nearest;onSelect(nearest)}};
  reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;raf=requestAnimationFrame(tick);
}
export function unmountOffice(){if(raf)cancelAnimationFrame(raf);raf=null;canvas=null}
export function selectAgent(id){selected=id}
export function setReduced(value){reduced=value}
export function applyEvent(e){if(!canvas||seen.has(e.seq)||!agents[e.actor])return;seen.add(e.seq);const a=agents[e.actor],r=ROLES[e.actor];
  if(e.type==='working'&&a.pending){a.state='working'}
  else if(e.type==='working'){a.state='working';const door=DOORS[r.room];a.path=[[door[0],r.y],[door[0],door[1]],[door[0],r.y],[r.x,r.y]]}
  else if(e.type==='waiting'){a.state='waiting'}else if(e.type==='error'){a.state='error';a.path=[]}
  else if(e.type==='done'){a.state='idle';if(!a.path.length&&!a.pending)a.path=[[r.x,r.y]];for(const id of HANDOFF[e.actor]||[]){const next=ROLES[id];if(next&&!reduced)packets.push({from:[r.x,r.y-8],to:[next.x,next.y-8],progress:0})}}
}

// Agents with something waiting on you walk to your review room and queue by your desk.
const QUEUE=[[352,251],[372,257],[420,257],[441,247],[334,236],[443,214]];
function route(from,to,fromRoom,toRoom){
  const d1=DOORS[fromRoom],d2=DOORS[toRoom];
  if(fromRoom===toRoom)return [[to[0],to[1]]];
  return [[d1[0],from[1]],[d1[0],150],[d2[0],150],[d2[0],d2[1]+12],[to[0],to[1]]];
}
export function setPending(counts){
  if(!canvas)return;
  const waiting=Object.keys(ROLES).filter(id=>id!=='lead'&&counts[id]>0);
  for(const [id,a] of Object.entries(agents)){
    if(id==='lead')continue;
    const r=ROLES[id],n=counts[id]||0,slot=waiting.indexOf(id);
    if(n&&!a.pending){a.pending=n;const spot=QUEUE[slot%QUEUE.length];a.home=false;
      if(reduced){a.x=spot[0];a.y=spot[1];a.path=[]}else a.path=route([a.x,a.y],spot,r.room,'bottomRight')}
    else if(!n&&a.pending){a.pending=0;
      if(reduced){a.x=r.x;a.y=r.y;a.path=[]}else a.path=route([a.x,a.y],[r.x,r.y],'bottomRight',r.room)}
    else a.pending=n;
  }
}
