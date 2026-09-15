export function screenToCanonical(svg, clientX, clientY, width, height) {
  const point = new DOMPoint(clientX, clientY).matrixTransform(svg.getScreenCTM().inverse());
  return [Math.max(0, Math.min(width, point.x)), Math.max(0, Math.min(height, point.y))];
}
export function rectangle(a, b) {
  return [Math.floor(Math.min(a[0], b[0])), Math.floor(Math.min(a[1], b[1])),
          Math.ceil(Math.max(a[0], b[0])), Math.ceil(Math.max(a[1], b[1]))];
}
export function patches(base, doc) {
  const actions = [], unlocked = new Set(), old = new Map(base.elements.map(e => [e.element_id, e]));
  const current = new Map(doc.elements.map(e => [e.element_id, e]));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  for (const e of base.elements) {
    const n = current.get(e.element_id);
    const lockedValueChanged = n && e.locked_fields.some(field => field === 'text'
      ? base.texts.some(t => e.text_region_ids.includes(t.text_region_id) &&
          t.effective_text !== doc.texts.find(x => x.text_region_id === t.text_region_id)?.effective_text)
      : !same(e[field], n[field]));
    if (!n || !same(e.locked_fields, n.locked_fields) || lockedValueChanged) {
      actions.push({action: 'set_lock', element_id: e.element_id, fields: []});
      unlocked.add(e.element_id);
    }
  }
  for (const e of doc.elements) {
    if (!old.has(e.element_id)) {
      actions.push({action: 'add_region', element_id: e.element_id, bbox: e.bbox, base_type: e.base_type,
        semantic_tags: e.semantic_tags, replaces_ids: e.replaces_ids || [],
        text: doc.texts.find(t => e.text_region_ids.includes(t.text_region_id))?.effective_text || ''});
    }
  }
  for (const e of doc.elements) {
    const previous = old.get(e.element_id);
    for (const [field, action] of Object.entries({bbox:'update_box', base_type:'set_type',
        semantic_tags:'set_tags', parent_id:'set_parent', text_region_ids:'set_text_links'})) {
      if (previous && !same(previous[field], e[field]) || !previous && field === 'parent_id' && e.parent_id)
        actions.push({action, element_id: e.element_id, [field]: e[field]});
    }
  }
  for (const e of base.elements) if (!current.has(e.element_id))
    actions.push({action:'delete_region', element_id:e.element_id, children:'detach_children'});
  for (const t of doc.texts) {
    const oldText = base.texts.find(x => x.text_region_id === t.text_region_id);
    if (oldText && oldText.effective_text !== t.effective_text)
      actions.push({action:'set_text', text_region_id:t.text_region_id, text:t.effective_text});
  }
  for (const e of doc.elements) {
    if (unlocked.has(e.element_id) || !same(old.get(e.element_id)?.locked_fields || [], e.locked_fields))
      actions.push({action:'set_lock', element_id:e.element_id, fields:e.locked_fields});
  }
  return actions;
}

// All snap geometry is canonical pixels; distances are measured in CSS pixels.
export const defaultSnapOptions = () => ({enabled:true, edges:true, centers:true, canvas:true, spacing:true});
const compareId = (a,b) => a < b ? -1 : a > b ? 1 : 0;
const commonSpan = (boxes,axis) => [Math.max(...boxes.map(b=>b[axis])),Math.min(...boxes.map(b=>b[axis+2]))];
const validBox = (b,w,h) => b.every(Number.isInteger)&&b[0]>=0&&b[1]>=0&&b[2]<=w&&b[3]<=h&&b[2]>b[0]&&b[3]>b[1];

export function snapReferences(elements, excludedId) {
  return elements.filter(e=>e.element_id!==excludedId).map(e=>({id:e.element_id,bbox:[...e.bbox]}))
    .sort((a,b)=>compareId(a.id,b.id));
}

export function snapGeometry({kind, box, active=[2,3], references=[], width, height,
    options=defaultSnapOptions(), scale=[1,1], previous=[null,null], point=false}) {
  const raw=[...box], hits=[null,null], result=[...raw];
  if(!options.enabled) return {box:result,hits,guides:[]};
  const lists=[[],[]];
  function shifted(axis,value) {
    const b=[...raw];
    if(kind==='move'){b[axis]+=value;b[axis+2]+=value;}
    else if(point){b[axis]=value;b[axis+2]=value;}
    else b[active[axis]]=value;
    return b;
  }
  function add(axis,value,type,ids,extra={}) {
    if(!Number.isInteger(value))return;
    const b=shifted(axis,value);
    if(point ? value<0||value>(axis?height:width) :
        b[axis]<0||b[axis+2]>(axis?height:width)||b[axis+2]<=b[axis])return;
    const distance=Math.abs(kind==='move'?value:value-raw[active[axis]])*scale[axis];
    const key=JSON.stringify([type,ids,extra.source,extra.target,extra.mode]);
    const retained=previous[axis]?.key===key;
    if(distance>(retained?10:6))return;
    lists[axis].push({axis,value,type,ids,key,distance,retained,...extra});
  }
  for(const axis of [0,1]) {
    const sources=kind==='move' ? [
      ...(options.edges?[{v:raw[axis],name:'low'},{v:raw[axis+2],name:'high'}]:[]),
      ...(options.centers?[{v:(raw[axis]+raw[axis+2])/2,name:'center'}]:[])
    ] : [{v:raw[active[axis]],name:'endpoint'}];
    for(const ref of references) {
      const targets=[...(options.edges?[[ref.bbox[axis],'edge'],[ref.bbox[axis+2],'edge']]:[]),
        ...(options.centers?[[(ref.bbox[axis]+ref.bbox[axis+2])/2,'center']]:[])];
      for(const [target,type] of targets) for(const source of sources)
        add(axis,kind==='move'?target-source.v:target,type,[ref.id],{target,source:source.name});
    }
    if(options.canvas) {
      const canvasSources=kind==='move'?[{v:raw[axis],name:'low'},{v:raw[axis+2],name:'high'}]:sources;
      for(const target of [0,axis?height:width]) for(const source of canvasSources)
        add(axis,kind==='move'?target-source.v:target,'canvas',[],{target,source:source.name});
    }
    if(!options.spacing||point)continue;
    const other=1-axis;
    for(const left of references) for(const right of references) {
      const l=left.bbox,r=right.bbox;
      if(left.id===right.id||l[axis+2]>r[axis])continue;
      const span=commonSpan([l,r,raw],other);
      if(span[0]>=span[1])continue;
      for(const mode of ['between','before','after']) {
        let delta,coefficient;
        if(mode==='between'){delta=l[axis+2]+r[axis]-raw[axis]-raw[axis+2];coefficient=kind==='move'?2:1;}
        else if(mode==='after'){
          if(kind!=='move'&&active[axis]!==axis)continue;
          delta=r[axis+2]+r[axis]-l[axis+2]-raw[axis];coefficient=1;
        }else{
          if(kind!=='move'&&active[axis]!==axis+2)continue;
          delta=l[axis]-r[axis]+l[axis+2]-raw[axis+2];coefficient=1;
        }
        const value=kind==='move'?delta/coefficient:raw[active[axis]]+delta/coefficient;
        if(!Number.isInteger(value)||Math.abs(delta/coefficient)*scale[axis]>10)continue;
        const candidate={axis,mode,ids:[left.id,right.id]};
        if(spacingSegments(shifted(axis,value),candidate,references))add(axis,value,'spacing',candidate.ids,{mode});
      }
    }
  }
  const rank={edge:0,center:1,canvas:2,spacing:3};
  for(const axis of [0,1]) {
    lists[axis].sort((a,b)=>Number(b.retained)-Number(a.retained)||a.distance-b.distance||rank[a.type]-rank[b.type]||compareId(a.key,b.key));
    hits[axis]=lists[axis][0]||null;
    if(hits[axis]){const b=shifted(axis,hits[axis].value);result[axis]=b[axis];result[axis+2]=b[axis+2];}
  }
  // The other axis may remove common overlap. Never show a false gap constraint.
  for(let pass=0;pass<2;pass++)for(const axis of [0,1]) if(hits[axis]?.type==='spacing'&&!spacingSegments(result,hits[axis],references)) {
    hits[axis]=null;result[axis]=raw[axis];result[axis+2]=raw[axis+2];
  }
  if(!point&&!validBox(result,width,height))return {box:raw,hits:[null,null],guides:[]};
  const guides=hits.filter(Boolean).map(hit=>({...hit,
    segments:hit.type==='spacing'?spacingSegments(result,hit,references):null}));
  return {box:result,hits,guides};
}

function spacingSegments(box,hit,references) {
  const [left,right]=hit.ids.map(id=>references.find(r=>r.id===id)?.bbox);
  if(!left||!right)return null;
  const a=hit.axis,o=1-a;
  const ordered=hit.mode==='between'?[left,box,right]:hit.mode==='after'?[left,right,box]:[box,left,right];
  const span=commonSpan(ordered,o);
  if(span[0]>=span[1])return null;
  const gaps=[ordered[1][a]-ordered[0][a+2],ordered[2][a]-ordered[1][a+2]];
  if(gaps[0]<0||gaps[0]!==gaps[1])return null;
  // Only adjacent objects in this common row/column may establish a gap.
  for(let i=0;i<2;i++) for(const ref of references) {
    if(hit.ids.includes(ref.id))continue;
    const b=ref.bbox;
    if(b[a]>=ordered[i][a+2]&&b[a+2]<=ordered[i+1][a]&&
        Math.max(span[0],b[o])<Math.min(span[1],b[o+2]))return null;
  }
  return [0,1].map(i=>({from:ordered[i][a+2],to:ordered[i+1][a],cross:(span[0]+span[1])/2,distance:gaps[i]}));
}
