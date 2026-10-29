import {readFile} from 'node:fs/promises';import {startApp} from '../server.mjs';
await startApp({distributionOverride:JSON.parse(await readFile(process.env.TEST_POLICY_FILE,'utf8'))});
