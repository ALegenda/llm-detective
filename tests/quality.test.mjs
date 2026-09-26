import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
const source=readFileSync(new URL('../static/quality.js',import.meta.url),'utf8');
const {readableEvidence}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

test('stored technical evidence references render as source names without prefix collisions',()=>{
 const entries=[{id:'f_1',source:'Карточка',title:'Время'},
   {id:'f_10',source:'Журнал',title:'Подпись'},
   {id:'s_live_abc',source:'Ирина',title:'Я отказалась.'}];
 assert.equal(readableEvidence('f_1 и f_10 подтверждаются s_live_abc.',entries),
   '«Карточка: Время» и «Журнал: Подпись» подтверждаются «Ирина: Я отказалась.».');
 assert.equal(readableEvidence('f_100 — неизвестная ссылка',entries),'f_100 — неизвестная ссылка');
});
