import { api } from "./api.js";

class House {
  devices = $state([]);
  rules = $state([]);
  orphaned = $state([]);
  proposals = $state([]);
  activity = $state([]);
  state = $state({});
  offline = $state(false);

  async #load(path, apply) {
    try {
      apply(await api.get(path));
      this.offline = false;
    } catch (err) {
      // Unreachable is a state the app shows and keeps working through. Any
      // other failure is a real error and belongs to the caller.
      if (err.offline) this.offline = true;
      else throw err;
    }
  }

  loadDevices()   { return this.#load("/api/drishti/devices",   (b) => (this.devices = b.devices)); }
  loadProposals() { return this.#load("/api/drishti/proposals", (b) => (this.proposals = b.proposals)); }
  loadActivity()  { return this.#load("/api/drishti/activity",  (b) => (this.activity = b.entries)); }
  loadState()     { return this.#load("/api/drishti/state",     (b) => (this.state = b)); }
  loadRules() {
    return this.#load("/api/drishti/rules", (b) => {
      this.rules = b.rules;
      this.orphaned = b.orphaned;
    });
  }
}

export const house = new House();
