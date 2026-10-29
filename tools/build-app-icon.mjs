// Code-native rendering of the existing chart mark, for Windows app icons.
import {writeFile,mkdir} from 'node:fs/promises';
import {deflateSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
const destination=fileURLToPath(new URL('../desktop/',import.meta.url));
const crcTable=Array.from({length:256},(_,n)=>{for(let i=0;i<8;i++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
const crc32=b=>{let c=0xffffffff;for(const v of b)c=crcTable[(c^v)&255]^(c>>>8);return (c^0xffffffff)>>>0;};
function chunk(type,data){const t=Buffer.from(type),out=Buffer.alloc(12+data.length);out.writeUInt32BE(data.length);t.copy(out,4);data.copy(out,8);out.writeUInt32BE(crc32(Buffer.concat([t,data])),8+data.length);return out;}
const points=[[62,176],[62,113],[110,148],[151,67],[198,102]];
function segmentDistance(x,y,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)));return Math.hypot(x-a[0]-t*dx,y-a[1]-t*dy);}
function pixel(x,y){
 const qx=Math.max(Math.abs(x-128)-76,0),qy=Math.max(Math.abs(y-128)-76,0);if(Math.hypot(qx,qy)>40)return [0,0,0,0];
 const line=points.slice(1).some((p,i)=>segmentDistance(x,y,points[i],p)<9);
 return line?[228,239,231,255]:[17,24,20,255];
}
function png(size){
 const raw=Buffer.alloc(size*(size*4+1));
 for(let y=0;y<size;y++)for(let x=0;x<size;x++){const sum=[0,0,0,0];for(let sy=0;sy<4;sy++)for(let sx=0;sx<4;sx++){const c=pixel((x+(sx+.5)/4)*256/size,(y+(sy+.5)/4)*256/size);c.forEach((v,i)=>sum[i]+=v);}for(let i=0;i<4;i++)raw[y*(size*4+1)+1+x*4+i]=Math.round(sum[i]/16);}
 const header=Buffer.alloc(13);header.writeUInt32BE(size,0);header.writeUInt32BE(size,4);header[8]=8;header[9]=6;
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',header),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}
await mkdir(destination,{recursive:true});
const sizes=[16,24,32,48,64,128,256],images=sizes.map(png),header=Buffer.alloc(6+16*sizes.length);header.writeUInt16LE(1,2);header.writeUInt16LE(sizes.length,4);let offset=header.length;
for(let i=0;i<sizes.length;i++){const p=6+16*i;header[p]=header[p+1]=sizes[i]===256?0:sizes[i];header.writeUInt16LE(1,p+4);header.writeUInt16LE(32,p+6);header.writeUInt32LE(images[i].length,p+8);header.writeUInt32LE(offset,p+12);offset+=images[i].length;}
await writeFile(new URL('../desktop/icon.ico',import.meta.url),Buffer.concat([header,...images]));
await writeFile(new URL('../desktop/icon.png',import.meta.url),images.at(-1));
await writeFile(new URL('../desktop/icon.svg',import.meta.url),'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect x="12" y="12" width="232" height="232" rx="40" fill="#111814"/><path d="M62 176V113l48 35 41-81 47 35" fill="none" stroke="#e4efe7" stroke-width="18" stroke-linejoin="round" stroke-linecap="round"/></svg>\n');
console.log('Desktop icon generated in 7 Windows sizes.');
