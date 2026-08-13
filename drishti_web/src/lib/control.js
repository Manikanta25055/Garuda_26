import { api } from "./api.js";

// Device control travels through the local lane of /instruct, which is the
// only control path Drishti has. The lane finds a device by its name or by its
// id with underscores turned into spaces — sending the raw id would match
// nothing, so "lamp_desk" has to go over as "lamp desk".
export function phraseFor(id, action) {
  return `turn the ${String(id).replace(/_/g, " ")} ${action}`;
}

export function isActuator(device) {
  return !String(device.type ?? "").startsWith("sensor.");
}

export function setDevice(id, action) {
  return api.post("/api/drishti/instruct", { text: phraseFor(id, action) });
}

// Turns off every reachable actuator, one at a time. Not "cuts power and halts
// detection": nothing in Drishti can halt the pipeline, and a button that
// claims to is worse than no button.
export async function allOff(devices) {
  const targets = devices.filter((d) => isActuator(d) && d.available && d.state !== "off");
  for (const device of targets) {
    await setDevice(device.id, "off");
  }
  return targets.map((d) => d.id);
}
