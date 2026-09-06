import {screenToCanonical, rectangle, patches} from '/state.js';
const $ = id => document.getElementById(id), copy = value => structuredClone(value);
const NS = 'http://www.w3.org/2000/svg';
const labels = {text:'T 文字',image:'I 图片',container:'C 容器',other:'O 其他'};
const colors = {text:'#44e0bd',image:'#f5be65',container:'#88aaff',other:'#ef91cf'};
let base, doc, head, csrf, selected, mode='select', undo=[], redo=[], gesture, pendingSave;
let view, initialView, working=false;
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
function fit() { view=[0,0,doc.width,doc.height]; initialView=[...view]; setView(); }
function render() {
  $('version').textContent=`草稿 v${head.draft_revision} · 已确认 ${head.confirmed_revision===null?'无':'v'+head.confirmed_revision}`;
  $('dirty').textContent=changed()?'有未保存修改':'无未保存修改';
  $('count').textContent=doc.elements.length;
  $('save').disabled=working||!changed(); $('confirm').disabled=working||changed();
  $('undo').disabled=!undo.length; $('redo').disabled=!redo.length;
  $('elements').replaceChildren(); $('boxes').replaceChildren(); $('handles').replaceChildren();
  for(const [i,e] of doc.elements.entries()) {
    const b=document.createElement('button'); b.textContent=`${i+1}. ${labels[e.base_type]} ${e.semantic_tags.join(' / ')} ${e.locked_fields.length?'🔒':''}`;
    b.classList.toggle('active',selected===e.element_id); b.onclick=()=>{selected=e.element_id;render();};
    $('elements').append(b);
    const [x1,y1,x2,y2]=e.bbox;
    $('boxes').append(node('rect',{x:x1,y:y1,width:x2-x1,height:y2-y1,stroke:colors[e.base_type],
      'data-element':e.element_id,class:selected===e.element_id?'selected':''}));
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
for(const tool of ['select','draw','pan'])$(tool).onclick=()=>{mode=tool;gesture=null;for(const t of ['select','draw','pan'])$(t).setAttribute('aria-pressed',t===mode);};
$('undo').onclick=()=>{if(undo.length){redo.push(copy(doc));doc=undo.pop();pendingSave=null;render();}};
$('redo').onclick=()=>{if(redo.length){undo.push(copy(doc));doc=redo.pop();pendingSave=null;render();}};
$('fit').onclick=fit;
function zoom(factor){const w=view[2]*factor,h=view[3]*factor;if(w<doc.width/8||w>doc.width*3)return;
  view=[view[0]+(view[2]-w)/2,view[1]+(view[3]-h)/2,w,h];setView();render();}
$('zoom-in').onclick=()=>zoom(.8);$('zoom-out').onclick=()=>zoom(1.25);
const svg=$('canvas'), point=event=>screenToCanonical(svg,event.clientX,event.clientY,doc.width,doc.height);
svg.onpointerdown=event=>{
  if(!doc||event.button!==0)return;
  const start=point(event); const corner=event.target.getAttribute('data-corner');
  const identity=event.target.getAttribute('data-element');
  if(mode==='pan'){gesture={kind:'pan',client:[event.clientX,event.clientY],view:[...view],scale:svg.getScreenCTM().a};}
  else if(mode==='draw'){gesture={kind:'draw',start,before:copy(doc)};}
  else if(corner!==null&&element()){gesture={kind:'resize',start,corner:Number(corner),box:[...element().bbox],before:copy(doc)};}
  else if(identity){selected=identity;render();if(!element().locked_fields.includes('bbox'))gesture={kind:'move',start,box:[...element().bbox],before:copy(doc)};}
  else{selected=null;render();}
  svg.setPointerCapture(event.pointerId);
};
svg.onpointermove=event=>{
  if(!gesture)return;
  if(gesture.kind==='pan'){view=[gesture.view[0]-(event.clientX-gesture.client[0])/gesture.scale,
    gesture.view[1]-(event.clientY-gesture.client[1])/gesture.scale,...gesture.view.slice(2)];return setView();}
  const end=point(event);
  if(gesture.kind==='draw'){
    const box=rectangle(gesture.start,end);$('handles').replaceChildren(node('rect',{x:box[0],y:box[1],width:box[2]-box[0],height:box[3]-box[1],stroke:'#fff'}));return;
  }
  const e=element();if(!e)return;let box;
  if(gesture.kind==='move'){
    const b=gesture.box,dx=Math.max(-b[0],Math.min(doc.width-b[2],Math.round(end[0]-gesture.start[0]))),
      dy=Math.max(-b[1],Math.min(doc.height-b[3],Math.round(end[1]-gesture.start[1])));
    box=[b[0]+dx,b[1]+dy,b[2]+dx,b[3]+dy];
  }else{
    const b=gesture.box,opposite=[[b[2],b[3]],[b[0],b[3]],[b[0],b[1]],[b[2],b[1]]][gesture.corner];box=rectangle(opposite,end);
  }
  if(box[2]>box[0]&&box[3]>box[1]){e.bbox=box;render();}
};
svg.onpointerup=event=>{
  if(!gesture)return; const active=gesture;gesture=null;
  if(active.kind==='pan')return;
  if(active.kind==='draw'){
    const box=rectangle(active.start,point(event));if(box[2]<=box[0]||box[3]<=box[1])return render();
    const id='temp-'+crypto.randomUUID(),type=$('new-type').value;selected=id;
    doc.elements.push({element_id:id,base_type:type,semantic_tags:[],bbox:box,parent_id:null,text_region_ids:type==='text'?[id+'-text']:[],locked_fields:[],field_sources:{},replaces_ids:[]});
    if(type==='text')doc.texts.push({text_region_id:id+'-text',effective_text:'',bbox:box,origin:'human',ocr_text_id:null});
  }
  if(JSON.stringify(active.before)!==JSON.stringify(doc)){undo.push(active.before);if(undo.length>100)undo.shift();redo=[];pendingSave=null;}
  render();
};
function cancelGesture(){if(gesture?.before)doc=gesture.before;gesture=null;render();}
svg.onpointercancel=cancelGesture;
window.addEventListener('keydown',event=>{
  if(['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.target.isContentEditable)return;
  if(event.key==='Escape'){cancelGesture();return;}
  if(event.key==='Delete'){event.preventDefault();remove();}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();$(event.shiftKey?'redo':'undo').click();}
});
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
