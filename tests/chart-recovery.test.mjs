import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
test('a failed chart can retry in place after the engine recovers',async()=>{
 let available=false,initializations=0;const status={textContent:''},shell={querySelector:()=>status,querySelectorAll:()=>[]};
 const node={dataset:{qchart:JSON.stringify({type:'line',data:{url:'/chart'}})},clientWidth:800,isConnected:true,children:[],getClientRects:()=>[{}],closest:()=>shell,addEventListener(){},replaceChildren(...children){this.children=children;},removeAttribute(name){if(name==='data-chart-ready')delete this.dataset.chartReady;}};
 const document={addEventListener(){},querySelectorAll:()=>[node],createElement:tag=>({tag,textContent:''})};
 const scope=vm.createContext({document,AbortSignal,AbortController,ResizeObserver:class{observe(){}disconnect(){}},requestAnimationFrame:fn=>{fn();return 1;},fetch:async()=>({ok:available,json:async()=>available?{series:[{name:'nav',values:[100,101]}]}:{error:'engine offline'}}),echarts:{init(){initializations++;return {setOption(){},on(){},resize(){},dispose(){}};}}});scope.window=scope;
 vm.runInContext(await readFile(new URL('../app/interactive.js',import.meta.url),'utf8'),scope);
 scope.QuantCharts.mount();await new Promise(setImmediate);assert.equal(node.dataset.chartReady,'error');assert.match(status.textContent,/engine offline/);const retry=node.children.find(x=>x.tag==='button');assert.equal(retry.textContent,'重試此圖表');
 available=true;retry.onclick();await new Promise(setImmediate);assert.equal(node.dataset.chartReady,'true');assert.equal(initializations,1);assert.match(status.textContent,/懸停/);
});
