// A single spoken sentence can support several authored accounts. Keep those
// account IDs for presentation/verification, but show the sentence only once.
export function evidenceGroups(entries){
 const groups=[], statements=new Map();
 for(const entry of entries){
  const key=entry.kind==='statement'?JSON.stringify([entry.source,entry.text.trim()]):null;
  const existing=key===null?null:statements.get(key);
  if(existing){existing.ids.push(entry.id);continue;}
  const group={...entry,ids:[entry.id]};groups.push(group);
  if(key!==null)statements.set(key,group);
 }
 return groups;
}

export function expandEvidence(entries,selected){
 const wanted=new Set(selected);
 return [...new Set(evidenceGroups(entries).flatMap(group=>group.ids.some(id=>wanted.has(id))?group.ids:[]))];
}
