'use strict';
const visualState=state=>({pending:'Needs confirmation',matched:'Overlay confirmed',mismatch:'Overlay mismatch',unavailable:'Visual check unavailable'})[state]||state.replaceAll('_',' ');
const reviewState=state=>({not_reviewed:'Awaiting review',deferred:'Review deferred',rejected:'Review rejected',physician_reviewed:'Physician reviewed',reviewer_accepted:'Reviewed',adjudicated_reference:'Adjudicated reference'})[state]||state.replaceAll('_',' ');

const $=id=>document.getElementById(id),clone=x=>structuredClone(x);
const S={run:null,caseId:null,source:null,state:null,id:null,g:null,obs:null,dirty:false,evidence:null,sync:false,epoch:0,pending:null};
let defaults,definition;
const viewer=OpenSeadragon({id:'viewer',showNavigationControl:false,showNavigator:true,animationTime:.15,blendTime:0,maxZoomPixelRatio:4,
  gestureSettingsMouse:{clickToZoom:false,dblClickToZoom:false}});
const anno=AnnotoriousOSD.createOSDAnnotator(viewer,{drawingEnabled:false,autoSave:true});
function status(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
function safe(fn){return async (...args)=>{if(S.busy)return;S.busy=true;document.body.inert=true;document.body.setAttribute('aria-busy','true');
  try{await fn(...args);}catch(e){status(e.message,true);console.error(e);}
  finally{document.body.inert=false;document.body.removeAttribute('aria-busy');S.busy=false;}};}
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await r.json();if(!r.ok){const e=Error(result.error||r.statusText);e.http=r.status;throw e;}return result;}
function actor(){const id=$('actor').value.trim(),role=$('role').value;if(!id)throw Error('Enter an operator ID.');
  if(role==='reviewer'&&/^(ai[-_]|gpt|astra)/i.test(id))throw Error('Human review requires the human reviewer’s own ID.');return {id,role};}
function key(suffix){return `sixth:${S.run.workspace_id||S.run.run_id}:${S.caseId}:${S.source.source_key}:${suffix}`;}
function drafts(){try{return JSON.parse(localStorage.getItem(key('drafts'))||'{}');}catch{return {};}}
function cache(){if(!S.caseId||!S.dirty||!S.g||!S.obs)return;const d=drafts();d[S.id]={id:S.id,g:S.g,obs:S.obs,base:S.state.annotations[S.id]?.content_hash||null};localStorage.setItem(key('drafts'),JSON.stringify(d));}
function changed(){if(S.sync)return;S.dirty=true;S.evidence=null;cache();update();}
function current(){return S.state?.annotations[S.id];}
function imagesReady(){return S.evidence&&['focus','context'].every(k=>$(k).complete&&$(k).naturalWidth>0&&$(k).dataset.evidence===S.evidence.evidence_id);}
function update(){const a=current();$('confirm').disabled=!(a&&!S.dirty&&imagesReady()&&S.evidence.evidence_id===a.evidence_id);
  $('save').disabled=!S.g||!!S.pending;$('retry').hidden=!S.pending;
  $('revision').textContent=S.dirty?'Unsaved changes':a?`${visualState(a.visual.state)} · ${reviewState(a.review.state)}`:'Unsaved region';
  $('review').disabled=$('role').value!=='reviewer'||!a||S.dirty||!imagesReady();}
function makeForm(){for(const name of ['focus','context']){const title=document.createElement('div');title.className='scope-title';title.textContent=name==='focus'?'Inside the region':'Surrounding context';$('scope-fields').append(title);
  for(const [field,caption] of [['assessment_state','Assessment'],['findings','Findings'],['note','Notes']]){
    const l=document.createElement('label');l.textContent=caption;const input=document.createElement(field==='assessment_state'?'select':'textarea');input.id=`${name}-${field}`;if(field==='findings')input.placeholder='One finding per line';
    if(field==='assessment_state')for(const state of definition.properties[name].properties.assessment_state.enum){const o=document.createElement('option');o.value=state;o.textContent=state.replaceAll('_',' ');input.append(o);}else input.rows=2;
    input.oninput=()=>{S.obs[name][field]=field==='findings'?input.value.split('\n').map(x=>x.trim()).filter(Boolean):input.value||null;changed();};l.append(input);$('scope-fields').append(l);}}}
function fill(obs){S.obs=clone(obs);$('label').value=obs.label;for(const name of ['focus','context'])for(const field of ['assessment_state','findings','note'])$(`${name}-${field}`).value=Array.isArray(obs[name][field])?obs[name][field].join('\n'):obs[name][field]??'';}
const contextKey=()=>S.source.coordinate_unit==='pixel'?'context_side_px':'context_side_um';
function geometryFields(){$('bounds').value=S.g.bbox_level0.map(x=>Number(x.toFixed(3))).join(', ');$('context-size').value=S.g[contextKey()];}
function shape(id,g){const [x,y,x1,y1]=g.bbox_level0,bounds={minX:x,minY:y,maxX:x1,maxY:y1};return {id,bodies:[],target:{annotation:id,selector:{type:g.type==='polygon'?'POLYGON':'RECTANGLE',geometry:g.type==='polygon'?{points:g.points,bounds}:{x,y,w:x1-x,h:y1-y,rot:0,bounds}}}};}
function redraw(){S.sync=true;const items=Object.values(S.state.annotations).filter(a=>!a.deleted&&a.id!==S.id).map(a=>shape(a.id,a.geometry));if(S.g)items.push(shape(S.id,S.g));anno.setAnnotations(items);S.sync=false;}
function list(){const el=$('regions');el.replaceChildren();for(const a of Object.values(S.state.annotations).filter(a=>!a.deleted)){const b=document.createElement('button');b.textContent=a.observation.label;b.classList.toggle('active',a.id===S.id);const sub=document.createElement('small');sub.textContent=`${visualState(a.visual.state)}${a.review.state==='not_reviewed'?'':` · ${reviewState(a.review.state)}`}`;b.append(sub);b.onclick=safe(()=>select(a.id));el.append(b);}
  for(const d of Object.values(drafts())){const b=document.createElement('button');b.textContent=`Draft · ${d.obs.label||'Untitled'}`;b.onclick=safe(()=>restore(d));el.append(b);}}
async function evidence(data){S.evidence=data;const epoch=++S.epoch;await Promise.all(['focus','context'].map(k=>new Promise((resolve,reject)=>{const im=$(k);im.dataset.evidence=data.evidence_id;im.onload=()=>{if(epoch===S.epoch)update();resolve();};im.onerror=()=>reject(Error('Evidence image failed to load.'));const url=`/evidence/${data.evidence_id}/${data.files[k+'_overlay'].file}`;im.src=url;$(k+'-link').href=url;})));if(epoch===S.epoch)update();}
async function prepare(){await evidence(await api('/api/prepare',{case_id:S.caseId,source_key:S.source.source_key,geometry:S.g}));}
async function select(id){cache();const a=S.state.annotations[id];S.id=id;S.g=clone(a.geometry);fill(a.observation);S.dirty=false;geometryFields();redraw();list();update();await evidence(await api('/api/evidence/'+a.evidence_id));}
async function restore(d){if((S.state.annotations[d.id]?.content_hash||null)!==d.base)throw Error('The saved version changed. This draft is retained; compare it with the current version before editing.');cache();S.id=d.id;S.g=clone(d.g);fill(d.obs);S.dirty=true;geometryFields();redraw();list();await prepare();}
async function newRegion(){cache();S.id=crypto.randomUUID();const w=Math.min(350,S.source.width*.25),h=Math.min(260,S.source.height*.25);
  const point=viewer.viewport.viewportToImageCoordinates(viewer.viewport.getCenter(true));const x=Math.max(0,Math.min(S.source.width-w,point.x-w/2)),y=Math.max(0,Math.min(S.source.height-h,point.y-h/2));
  S.g={type:'rectangle',role:'sampling_window',bbox_level0:[x,y,x+w,y+h],points:null,[contextKey()]:Math.ceil(Math.max(w*(S.source.mpp_x||1),h*(S.source.mpp_y||1))*2.5)};
  fill(defaults);S.dirty=true;cache();geometryFields();redraw();list();await prepare();status('Inspect the source and both scales, then record the visible evidence.');}
async function loadCase(id){cache();S.caseId=id;const data=await api('/api/case/'+id);S.source=data.source;S.state=data.state;S.id=null;S.g=null;S.obs=null;S.dirty=false;S.evidence=null;S.pending=JSON.parse(localStorage.getItem(key('pending'))||'null');
  const src=S.source;const min=0,max=src.level_dimensions.length-1;
  await new Promise(resolve=>{viewer.addOnceHandler('open',resolve);viewer.open({width:src.width,height:src.height,tileSize:512,minLevel:min,maxLevel:max,
    getLevelScale:level=>1/src.downsamples[max-level],getNumTiles:level=>new OpenSeadragon.Point(Math.ceil(src.level_dimensions[max-level][0]/512),Math.ceil(src.level_dimensions[max-level][1]/512)),
    getTileUrl:(level,x,y)=>`/tiles/${id}/${src.source_key}/${max-level}/${x}/${y}/tile.png`});});
  $('source-scale').textContent=src.coordinate_unit==='pixel'?`${src.width} × ${src.height} px`:`${src.mpp_x} × ${src.mpp_y} µm/px`;
  $('context-label').firstChild.textContent=`Context side · ${src.coordinate_unit==='pixel'?'pixels':'µm'}`;
  const first=Object.values(S.state.annotations).find(a=>!a.deleted);const draft=Object.values(drafts()).find(d=>(S.state.annotations[d.id]?.content_hash||null)===d.base);
  if(draft)await restore(draft);else if(first)await select(first.id);else await newRegion();list();update();}
function base(action){return {case_id:S.caseId,source_key:S.source.source_key,base_revision:S.state.revision,operation_id:crypto.randomUUID(),action,actor:actor()};}
async function commit(data){if(S.pending&&JSON.stringify(S.pending)!==JSON.stringify(data))throw Error('Retry the pending operation before submitting a different action.');S.pending=data;localStorage.setItem(key('pending'),JSON.stringify(data));update();
  try{const r=await api('/api/action',data);S.pending=null;localStorage.removeItem(key('pending'));const next=await api('/api/case/'+S.caseId);S.state=next.state;update();return r;}
  catch(e){if(e.http){S.pending=null;localStorage.removeItem(key('pending'));}update();throw e;}}
function clearDraft(id){const d=drafts();delete d[id];localStorage.setItem(key('drafts'),JSON.stringify(d));}
async function save(){const data={...base('save'),annotation_id:S.id,geometry:clone(S.g),observation:clone(S.obs)};await commit(data);clearDraft(S.id);S.dirty=false;await select(S.id);status('Saved. Inspect the final focus/context overlay and matching observation.');}
async function action(name,extra={}){const a=current();if(!a||S.dirty)throw Error('Save or reload the current region first.');return commit({...base(name),annotation_id:a.id,content_hash:a.content_hash,...extra});}
$('label').oninput=()=>{S.obs.label=$('label').value;changed();};
$('apply-json').onclick=safe(()=>{const patch=JSON.parse($('json').value);if(!patch||Array.isArray(patch)||typeof patch!=='object')throw Error('JSON must be an object.');const obs=clone(S.obs);for(const [k,v] of Object.entries(patch))obs[k]=['focus','context'].includes(k)?{...obs[k],...v}:v;fill(obs);changed();status('JSON applied to draft. Save to validate the contract.');});
$('apply-geometry').onclick=safe(async()=>{const bounds=$('bounds').value.split(/[,\s]+/).filter(Boolean).map(Number);S.g={type:'rectangle',role:'sampling_window',bbox_level0:bounds,points:null,[contextKey()]:Number($('context-size').value)};changed();await prepare();redraw();status('Geometry updated.');});
function drawn(a){if(S.sync)return;const q=a.target.selector.geometry;const polygon=a.target.selector.type==='POLYGON';if(a.id!==S.id){cache();S.id=a.id;fill(defaults);}
  S.g={type:polygon?'polygon':'rectangle',role:polygon?'inspection_region':'sampling_window',points:polygon?q.points:null,bbox_level0:[q.bounds.minX,q.bounds.minY,q.bounds.maxX,q.bounds.maxY],
    [contextKey()]:Math.ceil(Math.max((q.bounds.maxX-q.bounds.minX)*(S.source.mpp_x||1),(q.bounds.maxY-q.bounds.minY)*(S.source.mpp_y||1))*2.5)};
  anno.setDrawingEnabled(false);changed();geometryFields();list();safe(prepare)();}
anno.on('createAnnotation',drawn);anno.on('updateAnnotation',drawn);
anno.on('selectionChanged',selected=>{if(!S.sync&&selected.length&&selected[0].id!==S.id&&S.state.annotations[selected[0].id])safe(()=>select(selected[0].id))();});
$('new').onclick=safe(newRegion);$('home').onclick=()=>viewer.viewport.goHome();
for(const tool of ['rectangle','polygon'])$(tool).onclick=()=>{anno.setDrawingTool(tool);anno.setDrawingEnabled(true);status(`Draw a ${tool} on the source image.`);};
$('save').onclick=safe(save);$('confirm').onclick=safe(async()=>{if(!imagesReady()||S.dirty)throw Error('Load and inspect the saved evidence first.');await action('visual',{state:'matched',evidence_id:current().evidence_id});update();list();status('Visual inspection recorded for this saved version.');});
$('retry').onclick=safe(async()=>{const request=clone(S.pending);await commit(request);if(request.action==='save'){clearDraft(request.annotation_id);S.dirty=false;await select(request.annotation_id);}else await loadCase(S.caseId);status('Pending operation recovered using the same operation ID.');});
$('reload').onclick=safe(()=>loadCase(S.caseId));$('export').onclick=safe(async()=>{const r=await api('/api/export',{});status('Results exported.');$('export').title=r.directory;});
$('role').onchange=update;$('actor').oninput=update;
$('review').onclick=safe(async()=>{if(!imagesReady())throw Error('Inspect the saved images first.');await action('review',{decision:$('decision').value});update();status('Human review decision recorded.');});
$('delete').onclick=safe(async()=>{await action('delete');clearDraft(S.id);S.dirty=false;await newRegion();status('Region removed; earlier versions remain available.');});
$('timer-start').onclick=safe(async()=>{const id=crypto.randomUUID();await commit({...base('review_start'),review_session_id:id});status('Human review timer started.');});
$('timer-stop').onclick=safe(async()=>{const who=actor();const entry=Object.entries(S.state.review_sessions).find(([,s])=>!s.end_utc&&s.actor.id===who.id&&s.actor.role===who.role);if(!entry)throw Error('No open timer for this reviewer.');await commit({...base('review_stop'),review_session_id:entry[0]});status('Human review timer paused.');});
window.addEventListener('beforeunload',cache);
safe(async()=>{S.run=await api('/api/run');const contract=await api('/api/schema');defaults=contract.defaults;definition=contract.schema;makeForm();
  $('run-label').textContent=`Inspect${S.run.purpose==='engineering'?' · Demo':''}`;$('model').textContent=`Requested model: ${S.run.model_requested} / ${S.run.effort_requested}`;
  for(const item of S.run.queue){const b=document.createElement('button');b.textContent=item.case_id;b.onclick=safe(()=>loadCase(item.case_id));$('sources').append(b);}await loadCase(S.run.queue[0].case_id);status('Ready. Select a region to inspect.');})();
