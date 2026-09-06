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
