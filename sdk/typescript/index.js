import { randomUUID } from "node:crypto";

export class GravityClient {
  #base;
  #key;
  constructor({ url = process.env.GRAVITY_URL, namespace = process.env.GRAVITY_NAMESPACE,
                key = process.env.GRAVITY_KEY } = {}) {
    const origin = new URL(url);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname);
    if (origin.protocol !== "https:" && !(local && origin.protocol === "http:")) {
      throw new Error("Use HTTPS for a remote Gravity Memory service");
    }
    if (origin.username || origin.password || origin.search || origin.hash || origin.pathname !== "/") {
      throw new Error("Use a plain server origin");
    }
    if (!key || !namespace || !/^[a-z0-9][a-z0-9_-]{0,62}$/.test(namespace)) {
      throw new Error("Set GRAVITY_NAMESPACE and GRAVITY_KEY");
    }
    this.#base = origin.origin + "/v2/namespaces/" + namespace + "/";
    this.#key = key;
  }
  async call(operation, args = {}) {
    if (!/^[a-z][a-z0-9_]*$/.test(operation)) throw new Error("Invalid operation");
    const res = await fetch(this.#base + operation, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(90000),
      headers: { Authorization: "Bearer " + this.#key, "Content-Type": "application/json" },
      body: JSON.stringify(args)
    });
    const result = await res.json();
    if (!res.ok) throw new Error("Gravity Memory HTTP " + res.status + ": " + (result.error || res.statusText));
    return result;
  }
  startRun(title, { requestId = randomUUID(), parentRunId } = {}) {
    return this.call("start_run", { title, request_id: requestId, parent_run_id: parentRunId });
  }
  recall(runId, query, { requestId = randomUUID(), budgetBytes = 4096, filter } = {}) {
    return this.call("recall", { run_id: runId, query, request_id: requestId, budget_bytes: budgetBytes, filter });
  }
}
