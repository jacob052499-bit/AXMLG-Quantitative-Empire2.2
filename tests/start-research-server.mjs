// Regression fixture only. Never included in either distribution.
import {startApp} from '../server.mjs';
await startApp({distributionOverride:{product:'AXMLG Quantitative Empire',version:'2.1.0',mode:'operator-local',port:Number(process.env.PORT||4311)}});
