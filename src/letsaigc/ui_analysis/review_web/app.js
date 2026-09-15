import {screenToCanonical, rectangle, patches, defaultSnapOptions, snapReferences, snapGeometry} from '/state.js';
const $ = id => document.getElementById(id), copy = value => structuredClone(value);
const NS = 'http://www.w3.org/2000/svg';
const labels = {text:'T 文字',image:'I 图片',container:'C 容器',other:'O 其他'};
const colors = {text:'#44e0bd',image:'#f5be65',container:'#88aaff',other:'#ef91cf'};
let base, doc, head, csrf, selected, mode='select', undo=[], redo=[], gesture, pendingSave;
let view, initialView, working=false, ctrlHeld=false;
const snapOptions=defaultSnapOptions();
const notice = message => { $('notice').textContent=message; };
const changed = () => base && JSON.stringify(base)!==JSON.stringify(doc);
const element = () => doc?.elements.find(e=>e.element_id===selected);
function node(tag, attributes={}, text) {
  const n=document.createElementNS(NS,tag);
  for(const [key,value] of Object.entries(attributes)) n.setAttribute(key,value);
  if(text!==undefined) n.textContent=text;
  return n;
}
async function api(path, body) {
  const response=await fetch(path,{credentials:'same-origin',headers:body?{'Content-Type':'application/json','X-Review-CSRF':csrf||''}:{},
    ...(body?{method:'POST',body:JSON.stringify(body)}:{})});
  const value=await response.json();
  if(!response.ok) throw new Error(value.error||`HTTP ${response.status}`);
  return value;
}
function remember() { undo.push(copy(doc)); if(undo.length>100) undo.shift(); redo=[]; pendingSave=null; }
function mutate(fn) { remember(); fn(); render(); }
function setView() { $('canvas').setAttribute('viewBox',view.join(' ')); $('zoom').textContent=Math.round(doc.width/view[2]*100)+'%'; }
function fit() { if(gesture)cancelGesture(); view=[0,0,doc.width,doc.height]; initialView=[...view]; setView(); }
function render() {
  if(!doc||!head)return;
  $('version').textContent=`草稿 v${head.draft_revision} · 已确认 ${head.confirmed_revision===null?'无':'v'+head.confirmed_revision}`;
  $('dirty').textContent=changed()?'有未保存修改':'无未保存修改';
  $('count').textContent=doc.elements.length;
  $('save').disabled=working||!changed(); $('confirm').disabled=working||changed();
  $('undo').disabled=!undo.length; $('redo').disabled=!redo.length;
  $('elements').replaceChildren(); $('boxes').replaceChildren(); $('handles').replaceChildren();
  for(const [i,e] of doc.elements.entries()) {
    const b=document.createElement('button'); b.textContent=`${i+1}. ${labels[e.base_type]} ${e.semantic_tags.join(' / ')} ${e.locked_fields.length?'🔒':''}`;
    b.classList.toggle('active',selected===e.element_id); b.onclick=()=>{cancelGesture();selected=e.element_id;render();};
    $('elements').append(b);
    const [x1,y1,x2,y2]=e.bbox;
    $('boxes').append(node('rect',{x:x1,y:y1,width:x2-x1,height:y2-y1,stroke:colors[e.base_type],
      class:selected===e.element_id?'selected outline':'outline'}));
    $('boxes').append(node('rect',{x:x1,y:y1,width:x2-x1,height:y2-y1,
      'data-element':e.element_id,class:'border-hit'}));
    $('boxes').append(node('text',{x:x1+3,y:y1+13},`${i+1} ${e.base_type}`));
  }
  const e=element(); $('form').hidden=!e;
  $('selection').textContent=e?e.element_id:'选择一个元素开始校正';
  if(e) {
    const scale=($('canvas').getScreenCTM()?.a)||1;
    if(!e.locked_fields.includes('bbox')) {
      const [x1,y1,x2,y2]=e.bbox;
      [[x1,y1],[x2,y1],[x2,y2],[x1,y2]].forEach(([cx,cy],i)=>
        $('handles').append(node('circle',{cx,cy,r:5/scale,'data-corner':i})));
    }
    $('type').value=e.base_type;
    ['x1','y1','x2','y2'].forEach((id,i)=>{$(id).value=e.bbox[i];$(id).disabled=e.locked_fields.includes('bbox');});
    $('type').disabled=e.locked_fields.includes('base_type'); $('tags').disabled=e.locked_fields.includes('semantic_tags');
    for(const option of $('tags').options) option.selected=e.semantic_tags.includes(option.value);
    $('parent').replaceChildren(new Option('无父元素',''));
    for(const p of doc.elements) if(p.element_id!==e.element_id) $('parent').append(new Option(
      `${doc.elements.indexOf(p)+1}. ${labels[p.base_type]}`,p.element_id));
    $('parent').value=e.parent_id||''; $('parent').disabled=e.locked_fields.includes('parent_id');
    $('links').replaceChildren(); $('links').disabled=e.locked_fields.includes('text_region_ids');
    for(const t of doc.texts) $('links').append(new Option(t.effective_text.slice(0,70)||'空文字',t.text_region_id,false,e.text_region_ids.includes(t.text_region_id)));
    $('text-fields').replaceChildren();
    for(const t of doc.texts.filter(t=>e.text_region_ids.includes(t.text_region_id))) {
      const label=document.createElement('label'); label.textContent=`有效文字 · ${t.ocr_text_id?'原 OCR：'+t.original_text:'人工新增'}`;
      const input=document.createElement('textarea'); input.value=t.effective_text; input.maxLength=4096; input.dataset.text=t.text_region_id;
      input.disabled=e.locked_fields.includes('text'); label.append(input); $('text-fields').append(label);
    }
    $('locked').checked=e.locked_fields.length>0; $('delete').disabled=e.locked_fields.length>0;
  }
  renderGuides();
  $('history').replaceChildren();
  for(const item of head.history) $('history').append(new Option(`v${item.revision}${item.confirmed?' · 已确认':' · 草稿'}`,item.revision));
}
async function load(resetView=false) {
  const value=await api('/api/review'); csrf=value.csrf; head=value.head; base=copy(value.document); doc=copy(base);
  $('task').textContent=doc.task_id; undo=[];redo=[];pendingSave=null;
  $('original').setAttribute('href','/api/artifacts/'+head.base_refs.canonical_ref.artifact_id);
  $('original').setAttribute('width',doc.width);$('original').setAttribute('height',doc.height);
  if(resetView||!view) fit(); render();
}
$('form').onsubmit=event=>{
  event.preventDefault(); const e=element(); if(!e)return;
  const box=['x1','y1','x2','y2'].map(id=>Number($(id).value));
  if(!box.every(Number.isInteger)||box[0]<0||box[1]<0||box[2]>doc.width||box[3]>doc.height||box[0]>=box[2]||box[1]>=box[3])
    return notice('矩形坐标无效，请使用原图范围内的整数。');
  const parent=$('parent').value||null; let cursor=parent; const seen=new Set([e.element_id]);
  while(cursor){if(seen.has(cursor))return notice('父级关系不能形成循环。');seen.add(cursor);cursor=doc.elements.find(x=>x.element_id===cursor)?.parent_id;}
  const values=[...$('text-fields').querySelectorAll('textarea')].map(n=>[n.dataset.text,n.value]);
  mutate(()=>{
    e.bbox=box;e.base_type=$('type').value;e.semantic_tags=[...$('tags').selectedOptions].map(n=>n.value);
    e.parent_id=parent;e.text_region_ids=[...$('links').selectedOptions].map(n=>n.value);
    for(const [id,value]of values)doc.texts.find(t=>t.text_region_id===id).effective_text=value;
  }); notice('属性已应用，保存草稿后再确认版本。');
};
$('locked').onchange=()=>{const e=element();mutate(()=>{e.locked_fields=$('locked').checked?
  ['bbox','base_type','semantic_tags','parent_id','text_region_ids','text']:[];});};
function remove() {const e=element();if(!e)return;if(e.locked_fields.length)return notice('请先解除锁定。');
  if(doc.elements.some(x=>x.parent_id===e.element_id&&x.locked_fields.includes('parent_id')))return notice('请先解除子项的父级锁定。');
  mutate(()=>{doc.elements=doc.elements.filter(x=>x!==e);for(const child of doc.elements)if(child.parent_id===e.element_id)child.parent_id=null;selected=null;});}
$('delete').onclick=remove;
for(const tool of ['select','draw','pan'])$(tool).onclick=()=>{cancelGesture();mode=tool;for(const t of ['select','draw','pan'])$(t).setAttribute('aria-pressed',t===mode);};
$('undo').onclick=()=>{cancelGesture();if(undo.length){redo.push(copy(doc));doc=undo.pop();pendingSave=null;render();}};
$('redo').onclick=()=>{cancelGesture();if(redo.length){undo.push(copy(doc));doc=redo.pop();pendingSave=null;render();}};
$('fit').onclick=fit;
function zoom(factor,anchor){if(!doc||!view||gesture)return;
  const w=Math.max(doc.width/8,Math.min(doc.width*3,view[2]*factor)),ratio=w/view[2],h=view[3]*ratio;
  const [x,y]=anchor||[view[0]+view[2]/2,view[1]+view[3]/2];
  view=[x-(x-view[0])*ratio,y-(y-view[1])*ratio,w,h];setView();render();}
$('zoom-in').onclick=()=>zoom(.8);$('zoom-out').onclick=()=>zoom(1.25);
const svg=$('canvas'), point=event=>screenToCanonical(svg,event.clientX,event.clientY,doc.width,doc.height);
svg.addEventListener('wheel',event=>{
  event.preventDefault();if(!doc||gesture)return;
  const matrix=svg.getScreenCTM();if(!matrix)return;
  const anchor=new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());
  const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?svg.clientHeight:1);
  zoom(Math.exp(Math.max(-1,Math.min(1,delta*.002))),[anchor.x,anchor.y]);
},{passive:false});
svg.oncontextmenu=event=>event.preventDefault();
function snapScale(){const m=svg.getScreenCTM();return m?[Math.hypot(m.a,m.b),Math.hypot(m.c,m.d)]:[1,1];}
function effectiveSnap(){return {...snapOptions,enabled:snapOptions.enabled!==ctrlHeld};}
function renderGuides(){
  const layer=$('snap-guides');layer.replaceChildren();
  const g=gesture;if(!g?.solution)return;
  const [sx,sy]=snapScale(), guides=g.solution.guides;
  for(const id of new Set(guides.flatMap(x=>x.ids))){
    const ref=g.references.find(r=>r.id===id);if(!ref)continue;
    const [x,y,x2,y2]=ref.bbox;layer.append(node('rect',{x,y,width:x2-x,height:y2-y,class:'snap-reference'}));
  }
  for(const guide of guides){
    const axis=guide.axis, other=1-axis, box=g.solution.box;
    if(guide.type==='spacing'){
      for(const seg of guide.segments){
        const p=axis===0?[seg.from,seg.cross,seg.to,seg.cross]:[seg.cross,seg.from,seg.cross,seg.to];
        layer.append(node('line',{x1:p[0],y1:p[1],x2:p[2],y2:p[3],class:'snap-distance'}));
        for(const end of [seg.from,seg.to]){
          const tick=axis===0?[end,seg.cross-4/sy,end,seg.cross+4/sy]:[seg.cross-4/sx,end,seg.cross+4/sx,end];
          layer.append(node('line',{x1:tick[0],y1:tick[1],x2:tick[2],y2:tick[3],class:'snap-distance'}));
        }
        const x=axis===0?(seg.from+seg.to)/2:seg.cross+8/sx;
        const y=axis===0?seg.cross-8/sy:(seg.from+seg.to)/2;
        layer.append(node('text',{x,y,'text-anchor':axis===0?'middle':'start',style:`font-size:${11/sy}px`},`${seg.distance} px`));
      }
    }else{
      const refs=g.references.filter(r=>guide.ids.includes(r.id)).map(r=>r.bbox);
      const low=guide.type==='canvas'?0:Math.min(box[other],...refs.map(b=>b[other]));
      const high=guide.type==='canvas'?(axis===0?doc.height:doc.width):Math.max(box[other+2],...refs.map(b=>b[other+2]));
      const p=axis===0?[guide.target,low,guide.target,high]:[low,guide.target,high,guide.target];
      layer.append(node('line',{x1:p[0],y1:p[1],x2:p[2],y2:p[3],class:'snap-alignment'}));
    }
  }
}
for(const key of Object.keys(snapOptions))$('snap-'+key).onclick=()=>{
  snapOptions[key]=!snapOptions[key];$('snap-'+key).setAttribute('aria-pressed',snapOptions[key]);
  if(gesture?.last)updateGesture();
};
svg.onpointerdown=event=>{
  if(!doc||working||gesture||![0,2].includes(event.button))return;
  event.preventDefault();svg.focus({preventScroll:true});ctrlHeld=event.ctrlKey;
  const start=point(event),corner=event.target.getAttribute('data-corner'),identity=event.target.getAttribute('data-element');
  const common={pointerId:event.pointerId,start,before:copy(doc),selectedBefore:selected,client:[event.clientX,event.clientY],last:[event.clientX,event.clientY],moved:false};
  if(event.button===2||mode==='pan')gesture={...common,kind:'pan',client:common.last,view:[...view],scale:snapScale()[0]};
  else if(mode==='draw')gesture={...common,kind:'draw',references:snapReferences(doc.elements)};
  else if(corner!==null&&element()&&!element().locked_fields.includes('bbox')){
    const b=element().bbox,opposite=[[b[2],b[3]],[b[0],b[3]],[b[0],b[1]],[b[2],b[1]]][Number(corner)];
    gesture={...common,kind:'resize',fixed:opposite,box:[...b],references:snapReferences(doc.elements,selected)};
  }else if(identity){
    selected=identity;render();
    if(!element().locked_fields.includes('bbox'))gesture={...common,kind:'move',box:[...element().bbox],references:snapReferences(doc.elements,selected)};
  }else{selected=null;render();}
  if(gesture){
    if(gesture.kind==='draw'){
      const rounded=start.map(Math.round);
      const initial=snapGeometry({kind:'draw',box:[...rounded,...rounded],active:[0,1],point:true,
        references:gesture.references,width:doc.width,height:doc.height,options:effectiveSnap(),scale:snapScale()});
      gesture.fixed=initial.box.slice(0,2);
    }
    svg.setPointerCapture(event.pointerId);
  }
};
function updateGesture(){
  const g=gesture;if(!g)return;
  if(g.kind==='pan'){view=[g.view[0]-(g.last[0]-g.client[0])/g.scale,g.view[1]-(g.last[1]-g.client[1])/g.scale,...g.view.slice(2)];return setView();}
  g.moved ||= Math.hypot(g.last[0]-g.client[0],g.last[1]-g.client[1])>1;
  if(!g.moved)return;
  const end=point({clientX:g.last[0],clientY:g.last[1]});let box,active=[2,3];
  if(g.kind==='move'){
    const b=g.box,dx=Math.max(-b[0],Math.min(doc.width-b[2],Math.round(end[0]-g.start[0]))),
      dy=Math.max(-b[1],Math.min(doc.height-b[3],Math.round(end[1]-g.start[1])));
    box=[b[0]+dx,b[1]+dy,b[2]+dx,b[3]+dy];
  }else{
    const rounded=end.map(Math.round);box=rectangle(g.fixed,rounded);
    active=[rounded[0]<g.fixed[0]?0:2,rounded[1]<g.fixed[1]?1:3];
  }
  g.solution=snapGeometry({kind:g.kind,box,active,references:g.references,width:doc.width,height:doc.height,
    options:effectiveSnap(),scale:snapScale(),previous:g.solution?.hits});
  box=g.solution.box;
  if(g.kind!=='draw'&&box[2]>box[0]&&box[3]>box[1])element().bbox=box;
  render();
  if(g.kind==='draw')$('handles').replaceChildren(node('rect',{x:box[0],y:box[1],width:box[2]-box[0],height:box[3]-box[1],class:'draw-preview'}));
}
svg.onpointermove=event=>{
  if(!gesture||event.pointerId!==gesture.pointerId)return;
  gesture.last=[event.clientX,event.clientY];ctrlHeld=event.ctrlKey;updateGesture();
};
svg.onpointerup=event=>{
  if(!gesture||event.pointerId!==gesture.pointerId)return;
  gesture.last=[event.clientX,event.clientY];ctrlHeld=event.ctrlKey;updateGesture();
  const active=gesture;gesture=null;
  if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);
  if(active.kind==='pan'||!active.moved)return render();
  if(active.kind==='draw'){
    const box=active.solution.box;if(box[2]<=box[0]||box[3]<=box[1])return render();
    const id='temp-'+crypto.randomUUID(),type=$('new-type').value;selected=id;
    doc.elements.push({element_id:id,base_type:type,semantic_tags:[],bbox:box,parent_id:null,text_region_ids:type==='text'?[id+'-text']:[],locked_fields:[],field_sources:{},replaces_ids:[]});
    if(type==='text')doc.texts.push({text_region_id:id+'-text',effective_text:'',bbox:box,origin:'human',ocr_text_id:null});
  }
  if(JSON.stringify(active.before)!==JSON.stringify(doc)){undo.push(active.before);if(undo.length>100)undo.shift();redo=[];pendingSave=null;}
  render();
};
function cancelGesture(){
  const g=gesture;gesture=null;
  if(g){doc=g.before;selected=g.selectedBefore;if(g.kind==='pan'){view=g.view;setView();}
    if(svg.hasPointerCapture(g.pointerId))svg.releasePointerCapture(g.pointerId);}
  render();
}
svg.onpointercancel=cancelGesture;
svg.onlostpointercapture=()=>{if(gesture)cancelGesture();};
const typing=event=>['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.target.isContentEditable;
window.addEventListener('keydown',event=>{
  if(typing(event))return;
  if(event.key==='Control'){ctrlHeld=true;if(gesture)updateGesture();}
  if(event.key==='Escape'){cancelGesture();return;}
  if(event.key==='Delete'){event.preventDefault();cancelGesture();remove();}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();$(event.shiftKey?'redo':'undo').click();}
});
window.addEventListener('keyup',event=>{
  if(event.key==='Control'){ctrlHeld=false;if(!typing(event)&&gesture)updateGesture();}
});
window.addEventListener('blur',()=>{ctrlHeld=false;cancelGesture();});
window.addEventListener('beforeunload',event=>{if(changed()){event.preventDefault();event.returnValue='';}});
$('save').onclick=async()=>{
  if(working)return;working=true;render();
  try{
    const actions=patches(base,doc);if(actions.length>256)throw new Error('一次修改超过 256 项，请撤销部分修改后保存。');
    pendingSave ||= {request_id:'save-'+crypto.randomUUID(),base_draft_revision:head.draft_revision,
      base_confirmed_revision:head.confirmed_revision,actions};
    const result=await api('/api/review/drafts',pendingSave);selected=result.id_map[selected]||selected;await load();notice('草稿已保存。确认版本后才会生成新的切片与标注图。');
  }catch(error){notice(`${error.message} · 修改已保留；冲突时可重新载入并人工重做。`);}
  finally{working=false;render();}
};
$('confirm').onclick=async()=>{
  if(working||changed())return;working=true;render();
  const request={request_id:'confirm-'+crypto.randomUUID(),draft_revision:head.draft_revision,expected_confirmed_revision:head.confirmed_revision};
  try{await api('/api/review/confirm',request);notice('正在生成确认版本…');
    for(let i=0;i<600;i++){
      await new Promise(resolve=>setTimeout(resolve,500));let result;
      try{result=await api('/api/review/requests/'+request.request_id);}catch(error){if(i<4)continue;throw error;}
      if(result.state==='confirmed'){await load();notice(`v${result.revision} 已确认 · 本地生成完成 · 外部调用 0`);return;}
      if(['failed','conflict'].includes(result.state))throw new Error(result.error||result.state);
    }throw new Error('确认仍在处理，可重新载入查看已确认版本。');
  }catch(error){notice(error.message);}finally{working=false;render();}
};
$('reload').onclick=async()=>{if(changed()&&!window.confirm('放弃尚未保存的修改，重新载入草稿？'))return;
  try{await load();notice('已载入最新草稿。');}catch(error){notice(error.message);}};
$('restore').onclick=async()=>{
  if(changed()){notice('请先保存或放弃未保存修改。');return;}
  try{await api('/api/review/drafts',{request_id:'restore-'+crypto.randomUUID(),base_draft_revision:head.draft_revision,
    base_confirmed_revision:head.confirmed_revision,actions:[{action:'restore_revision',revision:Number($('history').value)}]});
    await load();notice('历史版本已恢复为新草稿，旧版本仍保留。');}catch(error){notice(error.message);}
};
const bootstrap=location.hash.slice(1);history.replaceState(null,'',location.pathname);
try{if(bootstrap){const session=await api('/api/session',{bootstrap});csrf=session.csrf;}await load(true);notice('本地人工校正 · 原图与 OCR 保留 · 不调用模型');}
catch(error){notice(`${error.message} · 请通过 ui review 重新打开会话。`);$('form').hidden=true;$('save').disabled=true;$('confirm').disabled=true;}
// Commit property edits on blur/change so selecting another element cannot lose them.
$('form').addEventListener('change', event => {if(event.target.id!=='locked')$('form').requestSubmit();});
