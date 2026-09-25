import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
const source=readFileSync(new URL('../static/evidence.js',import.meta.url),'utf8');
const {evidenceGroups,expandEvidence}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

test('one sentence remains one visible record without losing either account',()=>{
 const entries=[
  {id:'offer',kind:'statement',source:'Павел',text:'Получил письмо и просрочил платёж.'},
  {id:'debt',kind:'statement',source:'Павел',text:'Получил письмо и просрочил платёж.'},
  {id:'other',kind:'statement',source:'Марина',text:'Получил письмо и просрочил платёж.'},
  {id:'paper',kind:'observation',source:'Павел',text:'Получил письмо и просрочил платёж.'}
 ];
 assert.equal(evidenceGroups(entries).length,3);
 assert.deepEqual(expandEvidence(entries,['offer']),['offer','debt']);
 assert.deepEqual(expandEvidence(entries,['debt']),['offer','debt']);
 assert.deepEqual(expandEvidence(entries,['other']),['other']);
 assert.equal(entries[0].ids,undefined);
});

test('different observations and contradictory later statements stay separate',()=>{
 const entries=[
  {id:'a',kind:'statement',source:'Павел',text:'Я снимал маску.'},
  {id:'b',kind:'statement',source:'Павел',text:'Я не снимал маску.'},
  {id:'c',kind:'observation',source:'Шкаф',text:'Царапина.'},
  {id:'d',kind:'observation',source:'Шкаф',text:'Царапина.'}
 ];
 assert.equal(evidenceGroups(entries).length,4);
 assert.deepEqual(expandEvidence(entries,['a','c']),['a','c']);
});
