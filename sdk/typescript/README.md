# Gravity Memory client

From the source checkout, install with `npm install ./sdk/typescript`. Set `GRAVITY_URL`, `GRAVITY_NAMESPACE`, and `GRAVITY_KEY` in the server-side application's environment. The key needs read and write access for this example.

```ts
import { GravityClient } from "@gravity-memory/client";

const memory = new GravityClient();
const run = await memory.startRun("Prepare an account handoff");
const recall = await memory.recall(run.run_id, "preferred contact channel", { budgetBytes: 2048 });
console.log(recall.items, recall.trace_id);
```

Use a stable requestId when retrying writes or recalls. Run the cross-session coding example from the repository using `node examples/coding-handoff.mjs`.
