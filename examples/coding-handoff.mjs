import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { GravityClient } from "../sdk/typescript/index.js";

const client = new GravityClient();
const project = randomUUID();
const filter = {field:"metadata.project", operator:"eq", value:project};
const id = () => randomUUID();
async function decision(revision, tool) {
  const quote = "The migration runner is " + tool + ".";
  return client.call("remember", {request_id:project+revision, source:"Repository decision "+revision,
    text:quote, metadata:{project}, facts:[{key:"migration.runner", value:tool, quote}]});
}
const previous = await client.startRun("Review repository migrations");
await decision("A", "Flyway");
await client.recall(previous.run_id, "migration runner", {filter});
await client.call("handoff", {request_id:id(), run_id:previous.run_id, summary:"Recheck the migration decision before release."});
await decision("B", "Atlas");
const peer = new GravityClient({key:process.env.GRAVITY_PEER_KEY || process.env.GRAVITY_KEY});
const next = await peer.startRun("Resume release in a second client", {parentRunId:previous.run_id});
const recall = await peer.recall(next.run_id, "migration runner", {filter});
assert.equal(recall.items[0].value, "Atlas");
assert.equal(recall.items[0].source_id, project+"B");
assert.ok(next.handoff.includes("Recheck"));
await peer.call("handoff", {request_id:id(), run_id:next.run_id, summary:"Release uses Atlas.", source_ids:[project+"B"]});
console.log("Resumed with", recall.items[0].value, "from", recall.items[0].source, "trace", recall.trace_id);
