// Explicitly synthetic engineering fixture; never market or strategy performance evidence.
export function hourlyCSV(count=3600){
  const rows=['timestamp,open,high,low,close,volume'];let previous=16000;
  for(let i=0;i<count;i++){
    const time=Date.UTC(2024,0,1)+i*3600000;
    const close=16000+400*Math.sin(i/37)+80*Math.sin(i/11)+i*.03;
    const open=previous+2*Math.sin(i*.71),high=Math.max(open,close)+6,low=Math.min(open,close)-6;
    rows.push([new Date(time).toISOString(),open,high,low,close,0].join(','));previous=close;
  }
  return rows.join('\n');
}
