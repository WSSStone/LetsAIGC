// Exercise the shipped event handlers without browser or model dependencies.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.DOMPoint=class{constructor(x,y){this.x=x;this.y=y;}matrixTransform(){return this;}};
const state=await import('data:text/javascript;base64,'+Buffer.from(readFileSync('src/letsaigc/ui_analysis/review_web/state.js')).toString('base64'));
class Element {
 constructor(){this.attributes={};this.children=[];this.value='';this.disabled=false;this.tagName='BUTTON';this.classList={toggle(){}};this.handlers={};this.captures=new Set();}
 setAttribute(k,v){this.attributes[k]=String(v);}
 getAttribute(k){return this.attributes[k]??null;}
 append(...v){this.children.push(...v);}
 replaceChildren(...v){this.children=v;}
 get options(){return this.children;}
 querySelectorAll(){return [];}
 addEventListener(k,v){this.handlers[k]=v;}
 click(){return this.onclick?.({target:this});}
 focus(){}
 getScreenCTM(){return {a:1,b:0,c:0,d:1,inverse(){return this;}};}
 setPointerCapture(id){this.captures.add(id);}
 hasPointerCapture(id){return this.captures.has(id);}
 releasePointerCapture(id){this.captures.delete(id);this.onlostpointercapture?.();}
}
const nodes=new Map(), get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
get('new-type').value='image';
const document={getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element()};
const handlers={},window={addEventListener:(k,f)=>handlers[k]=f};
const element=(id,bbox)=>({element_id:id,bbox,base_type:'image',semantic_tags:[],text_region_ids:[],locked_fields:[],parent_id:null,field_sources:{}});
const doc={task_id:'test',width:1000,height:1000,elements:[element('moving',[30,100,50,120]),element('reference',[100,100,120,120])],texts:[]};
const head={draft_revision:0,confirmed_revision:null,history:[],base_refs:{canonical_ref:{artifact_id:'image'}}};
const fetch=async()=>({ok:true,json:async()=>({document:structuredClone(doc),head,csrf:'test'})});
const app=readFileSync('src/letsaigc/ui_analysis/review_web/app.js','utf8').replace(/^import .*?;\s*/,'');
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
await new AsyncFunction('document','window','fetch','DOMPoint','Option','location','history','crypto',...Object.keys(state),app)(document,window,fetch,
 class{constructor(x,y){this.x=x;this.y=y;}matrixTransform(){return this;}},class extends Element{constructor(text,value){super();this.value=value;this.textContent=text;}},
 {hash:'',pathname:'/'},{replaceState(){}},{randomUUID:()=>String(Math.random())},...Object.values(state));
const svg=get('canvas');
const target=attrs=>{const e=new Element();Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));return e;};
const event=(x,y,extra={})=>({clientX:x,clientY:y,pointerId:1,button:0,ctrlKey:false,preventDefault(){},target:target({}),...extra});
const down=(x,y,attrs={})=>svg.onpointerdown(event(x,y,{target:target(attrs)}));
const move=(x,y,extra={})=>svg.onpointermove(event(x,y,extra));
const up=(x,y,extra={})=>svg.onpointerup(event(x,y,extra));
const coords=()=>['x1','y1','x2','y2'].map(id=>Number(get(id).value));
const key=(name,extra={})=>handlers.keydown({key:name,target:svg,preventDefault(){},...extra});
for(const k of ['centers','canvas','spacing'])get('snap-'+k).click();
down(30,110,{'data-element':'moving'});up(30,110);assert.deepEqual(coords(),[30,100,50,120]);assert.equal(get('undo').disabled,true);
down(30,110,{'data-element':'moving'});move(76,110);assert.deepEqual(coords(),[80,100,100,120]);assert.ok(get('snap-guides').children.length);
key('Control');assert.deepEqual(coords(),[76,100,96,120]);
handlers.keyup({key:'Control',target:svg});assert.deepEqual(coords(),[80,100,100,120]);
up(76,110);assert.deepEqual(coords(),[80,100,100,120]);assert.equal(get('snap-guides').children.length,0);
get('undo').click();assert.deepEqual(coords(),[30,100,50,120]);get('redo').click();assert.deepEqual(coords(),[80,100,100,120]);get('undo').click();
for(const cancel of [()=>key('Escape'),()=>svg.onpointercancel(),()=>handlers.blur(),()=>get('pan').click(),()=>svg.onlostpointercapture()]){
 get('select').click();down(30,110,{'data-element':'moving'});move(76,110);cancel();assert.deepEqual(coords(),[30,100,50,120]);assert.equal(get('snap-guides').children.length,0);
}
get('select').click();down(30,110,{'data-element':'moving'});move(76,110);key('z',{ctrlKey:true});assert.deepEqual(coords(),[30,100,50,120]);
get('draw').click();down(51,200);move(96,240);assert.equal(+get('handles').children[0].getAttribute('width'),50);up(96,240);assert.deepEqual(coords(),[50,200,100,240]);assert.equal(get('count').textContent,3);get('undo').click();assert.equal(get('count').textContent,2);
// Reverse resizing keeps the fixed corner and rounds only the active endpoint.
get('select').click();down(30,110,{'data-element':'moving'});up(30,110);
down(50,120,{'data-corner':'2'});move(10,80);up(10,80);assert.deepEqual(coords(),[10,80,30,100]);get('undo').click();
// Ctrl in a text field cannot affect the active gesture.
down(30,110,{'data-element':'moving'});move(76,110);key('Control',{target:{tagName:'INPUT'}});assert.deepEqual(coords(),[80,100,100,120]);key('Escape');
console.log('snap gesture lifecycle scenarios passed');
